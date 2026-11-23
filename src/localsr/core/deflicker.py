"""Source-driven, model-independent video de-flicker and static shimmer stabiliser.

Stage A estimates a smooth spatial gain map from optical-flow-aligned source
frames in a +-3 frame shot window to remove source exposure/illumination flicker
while leaving clean clips untouched via a soft dead zone.

Stage B runs a source-gated recursive blend in output space (computed in torch
on the job's accelerator when available, else CPU) to suppress per-frame
synthesis shimmer in static regions without ghosting moving objects or crossing
scene cuts.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from .video_io import TimedVideoFrame

ANALYSIS_MAX_DIMENSION = 640
CUT_LUMA_SIZE = (64, 48)
CUT_THRESHOLD = 25.0

STAGE_A_GRID = (16, 12)  # (width_blocks, height_blocks)
# Stage A only needs a 16x12 grid of gains, so it measures on a quarter of the analysis
# pixels: optical flow is the dominant cost and scales with the pixel count.
STAGE_A_MAX_DIMENSION = 320
STAGE_A_RADIUS = 3
STAGE_A_DEADZONE = 0.01
STAGE_A_GAIN_MIN = 0.8
STAGE_A_GAIN_MAX = 1.25
STAGE_A_MAD_MAX = 0.05

STAGE_B_A = 0.8
STAGE_B_K = 2.0
_EPS = 1.0
_LUMA_VALID_LOW = 16.0
_LUMA_VALID_HIGH = 240.0
_EROSION_KERNEL = np.ones((3, 3), np.uint8)


@dataclass(frozen=True)
class AnalysisFrame:
    """Compact source-frame summary used by Stage A and Stage B analysis."""

    y: np.ndarray
    gray: np.ndarray
    y64: np.ndarray
    log_y: np.ndarray
    # Stage A copies (longer edge <= STAGE_A_MAX_DIMENSION).
    a_y: np.ndarray
    a_gray: np.ndarray
    a_log_y: np.ndarray


def _luma(rgb: np.ndarray) -> np.ndarray:
    rgb_f = rgb.astype(np.float32, copy=False)
    return 0.299 * rgb_f[..., 0] + 0.587 * rgb_f[..., 1] + 0.114 * rgb_f[..., 2]


def prepare_analysis_frame(rgb: np.ndarray) -> AnalysisFrame:
    """Downscale ``rgb`` so its longer edge is at most 640 px and extract luma buffers."""
    height, width = rgb.shape[:2]
    longest = max(height, width)
    if longest > ANALYSIS_MAX_DIMENSION:
        scale = ANALYSIS_MAX_DIMENSION / float(longest)
        target_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        small = cv2.resize(rgb, target_size, interpolation=cv2.INTER_AREA)
    else:
        small = rgb
    y = _luma(small)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    y64 = cv2.resize(y, CUT_LUMA_SIZE, interpolation=cv2.INTER_AREA)
    log_y = np.log(y + _EPS)
    small_h, small_w = y.shape
    if max(small_h, small_w) > STAGE_A_MAX_DIMENSION:
        a_scale = STAGE_A_MAX_DIMENSION / float(max(small_h, small_w))
        a_size = (max(1, round(small_w * a_scale)), max(1, round(small_h * a_scale)))
        a_y = cv2.resize(y, a_size, interpolation=cv2.INTER_AREA)
        a_gray = cv2.resize(gray, a_size, interpolation=cv2.INTER_AREA)
        a_log_y = np.log(a_y + _EPS)
    else:
        a_y, a_gray, a_log_y = y, gray, log_y
    return AnalysisFrame(
        y=y, gray=gray, y64=y64, log_y=log_y, a_y=a_y, a_gray=a_gray, a_log_y=a_log_y
    )


def resolve_deflicker_device(device_str: str) -> torch.device:
    """Run output-size maths on cuda/mps/xpu when available; fall back to CPU."""
    kind = device_str.split(":", 1)[0].lower()
    if kind == "cuda" and torch.cuda.is_available():
        return torch.device(device_str)
    if kind == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    if kind == "xpu" and hasattr(torch, "xpu") and torch.xpu.is_available():
        return torch.device(device_str)
    return torch.device("cpu")


def is_scene_cut(prev_y64: np.ndarray, curr_y64: np.ndarray) -> bool:
    """Return True when gain-compensated 64x48 luma differs beyond the cut threshold."""
    gain = (float(prev_y64.mean()) + _EPS) / (float(curr_y64.mean()) + _EPS)
    return bool(np.abs(prev_y64 - curr_y64 * gain).mean() > CUT_THRESHOLD)


class _DeflickerProcessor:
    """Stateful streaming Stage A + Stage B processor for a single video job."""

    def __init__(
        self,
        *,
        device_str: str = "cpu",
        flow_preset: int = cv2.DISOPTICAL_FLOW_PRESET_FAST,
    ) -> None:
        self.device = resolve_deflicker_device(device_str)
        self._dis = cv2.DISOpticalFlow_create(flow_preset)
        self._grid_shape: tuple[int, int] | None = None
        self._grid_x: np.ndarray | None = None
        self._grid_y: np.ndarray | None = None
        self._prev_out: torch.Tensor | None = None
        self._vbar: np.ndarray | None = None
        self._prev_v_src: np.ndarray | None = None
        self._sigmas: deque[float] = deque(maxlen=10)

    def _warp_grid(self, height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
        if self._grid_shape != (height, width) or self._grid_x is None or self._grid_y is None:
            self._grid_x, self._grid_y = np.meshgrid(
                np.arange(width, dtype=np.float32),
                np.arange(height, dtype=np.float32),
            )
            self._grid_shape = (height, width)
        return self._grid_x, self._grid_y

    def _block_log_ratio(self, target: AnalysisFrame, neighbour: AnalysisFrame) -> np.ndarray:
        gw, gh = STAGE_A_GRID
        height, width = target.a_y.shape
        if height >= 12 and width >= 12:
            flow = self._dis.calc(target.a_gray, neighbour.a_gray, None)
            grid_x, grid_y = self._warp_grid(height, width)
            yu = cv2.remap(
                neighbour.a_y,
                grid_x + flow[..., 0],
                grid_y + flow[..., 1],
                cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
        else:
            yu = neighbour.a_y

        valid = (
            (target.a_y > _LUMA_VALID_LOW)
            & (target.a_y < _LUMA_VALID_HIGH)
            & (yu > _LUMA_VALID_LOW)
            & (yu < _LUMA_VALID_HIGH)
        )
        if not valid.any():
            return np.zeros((gh, gw), dtype=np.float32)

        ratio = np.full((height, width), np.nan, dtype=np.float32)
        ratio[valid] = np.log(yu[valid] + _EPS) - target.a_log_y[valid]
        global_med = float(np.median(ratio[valid]))
        result = np.full((gh, gw), global_med, dtype=np.float32)

        bh, bw = height // gh, width // gw
        if bh > 0 and bw > 0:
            cropped_r = (
                ratio[: gh * bh, : gw * bw]
                .reshape(gh, bh, gw, bw)
                .transpose(0, 2, 1, 3)
                .reshape(gh, gw, bh * bw)
            )
            cropped_v = (
                valid[: gh * bh, : gw * bw]
                .reshape(gh, bh, gw, bw)
                .transpose(0, 2, 1, 3)
                .reshape(gh, gw, bh * bw)
            )
            counts = cropped_v.sum(axis=-1)
            all_valid = counts == (bh * bw)
            if all_valid.any():
                blocks = cropped_r[all_valid]
                med = np.median(blocks, axis=-1)
                mad = np.median(np.abs(blocks - med[:, None]), axis=-1)
                result[all_valid] = np.where(mad < STAGE_A_MAD_MAX, med, global_med)
            partial = (~all_valid) & (counts > 0.5 * bh * bw)
            if partial.any():
                pj, pi = np.where(partial)
                for j, i in zip(pj.tolist(), pi.tolist(), strict=True):
                    block = cropped_r[j, i][cropped_v[j, i]]
                    med = float(np.median(block))
                    if float(np.median(np.abs(block - med))) < STAGE_A_MAD_MAX:
                        result[j, i] = med
        return result

    def stage_a_gain(
        self,
        target: AnalysisFrame,
        target_shot: int,
        neighbours: Iterable[tuple[int, AnalysisFrame, int]],
        target_index: int,
    ) -> np.ndarray:
        """Estimate the Stage A (12, 16) multiplicative gain map for ``target``."""
        gw, gh = STAGE_A_GRID
        values = [np.zeros((gh, gw), dtype=np.float32)]
        for idx, frame, shot_id in neighbours:
            if idx == target_index or shot_id != target_shot:
                continue
            values.append(self._block_log_ratio(target, frame))

        centre = np.median(np.stack(values), axis=0).astype(np.float32)
        centre = cv2.medianBlur(centre, 3)
        shrunk = np.sign(centre) * np.maximum(0.0, np.abs(centre) - STAGE_A_DEADZONE)
        return np.clip(np.exp(shrunk), STAGE_A_GAIN_MIN, STAGE_A_GAIN_MAX).astype(np.float32)

    def apply_frame(
        self,
        out_rgb: np.ndarray,
        analysis: AnalysisFrame,
        gain: np.ndarray,
        is_cut: bool,
    ) -> np.ndarray:
        """Apply Stage A gain and Stage B recursive shimmer stabilisation."""
        has_gain = bool(np.any(gain != 1.0))
        all_gain = has_gain and bool(np.all(gain != 1.0))
        ah, aw = analysis.y.shape
        v_t = (
            analysis.y * cv2.resize(gain, (aw, ah), interpolation=cv2.INTER_LINEAR)
            if has_gain
            else analysis.y
        )

        out_h, out_w = out_rgb.shape[:2]
        with torch.no_grad():
            out_tensor = (
                torch.from_numpy(np.ascontiguousarray(out_rgb))
                .to(self.device)
                .permute(2, 0, 1)
                .unsqueeze(0)
                .to(torch.float32)
            )
            if has_gain:
                gain_tensor = (
                    torch.from_numpy(gain).to(self.device).view(1, 1, gain.shape[0], gain.shape[1])
                )
                gm = F.interpolate(
                    gain_tensor, size=(out_h, out_w), mode="bilinear", align_corners=False
                )
                p_t = out_tensor * gm
            else:
                gm = None
                p_t = out_tensor

            reset = (
                self._prev_out is None
                or self._vbar is None
                or self._prev_v_src is None
                or is_cut
                or self._prev_out.shape != p_t.shape
            )
            if reset:
                o_t = p_t
                self._vbar = v_t.copy()
                self._sigmas.clear()
            else:
                assert self._prev_out is not None
                assert self._vbar is not None
                assert self._prev_v_src is not None
                d0 = v_t - self._prev_v_src
                d0 = d0 - float(np.median(d0))
                s_t = max(1.0, 1.4826 * float(np.median(np.abs(d0))))
                if self._sigmas:
                    s_t = min(s_t, 1.5 * float(np.median(self._sigmas)))
                self._sigmas.append(s_t)

                ok_px = (
                    (self._vbar > _LUMA_VALID_LOW)
                    & (self._vbar < _LUMA_VALID_HIGH)
                    & (v_t > _LUMA_VALID_LOW)
                    & (v_t < _LUMA_VALID_HIGH)
                )
                kk = (
                    float(np.median((v_t[ok_px] + _EPS) / (self._vbar[ok_px] + _EPS)))
                    if ok_px.any()
                    else 1.0
                )
                prev_v = self._vbar * kk
                diff = cv2.blur(np.abs(v_t - prev_v), (3, 3))
                conf = np.exp(-((diff / (STAGE_B_K * s_t)) ** 2)).astype(np.float32)
                conf[diff > 4.0 * s_t] = 0.0
                conf = cv2.erode(conf * STAGE_B_A, _EROSION_KERNEL)
                self._vbar = conf * prev_v + (1.0 - conf) * v_t

                conf_tensor = torch.from_numpy(conf).to(self.device).view(1, 1, ah, aw)
                cu = (
                    F.interpolate(
                        conf_tensor, size=(out_h, out_w), mode="bilinear", align_corners=False
                    )
                    if (ah, aw) != (out_h, out_w)
                    else conf_tensor
                )
                o_t = torch.lerp(p_t, self._prev_out * kk, cu)

            self._prev_out = o_t
            self._prev_v_src = v_t

            if reset and not has_gain:
                return out_rgb

            if has_gain:
                dither = torch.empty(
                    (1, 1, out_h, out_w), dtype=torch.float32, device=self.device
                ).uniform_(-0.5, 0.5)
                if all_gain:
                    to_u8 = o_t + dither
                else:
                    assert gm is not None
                    to_u8 = torch.where(gm != 1.0, o_t + dither, o_t)
            else:
                to_u8 = o_t

            return (
                to_u8.clamp(0.0, 255.0)
                .round()
                .to(torch.uint8)
                .squeeze(0)
                .permute(1, 2, 0)
                .contiguous()
                .cpu()
                .numpy()
            )

    def close(self) -> None:
        self._prev_out = None
        self._vbar = None
        self._prev_v_src = None
        self._sigmas.clear()


def _pixels(frame: TimedVideoFrame | np.ndarray) -> np.ndarray:
    return frame.rgb if isinstance(frame, TimedVideoFrame) else frame


def _with_pixels(
    frame: TimedVideoFrame | np.ndarray, rgb: np.ndarray
) -> TimedVideoFrame | np.ndarray:
    return frame.with_pixels(rgb) if isinstance(frame, TimedVideoFrame) else rgb


def deflicker_stream(
    frames: Iterable[tuple[TimedVideoFrame | np.ndarray, AnalysisFrame | np.ndarray]],
    *,
    device_str: str = "cpu",
    cancel_event: threading.Event | None = None,
    on_frame: Callable[[int], None] | None = None,
    keep: Callable[[], bool] | None = None,
    flow_preset: int = cv2.DISOPTICAL_FLOW_PRESET_FAST,
    diagnostics: list[tuple[np.ndarray, bool]] | None = None,
) -> Iterator[TimedVideoFrame | np.ndarray]:
    """Stream-deflicker ``(output_frame, source_or_analysis)`` pairs with bounded memory.

    Holds at most a 3-frame look-ahead of output frames and a +-3 window of
    compact <=640 px analysis frames. Preserves frame timing/metadata, progress
    callbacks, and keep/discard stop semantics.
    """
    processor = _DeflickerProcessor(device_str=device_str, flow_preset=flow_preset)
    output_buffer: deque[tuple[int, TimedVideoFrame | np.ndarray]] = deque()
    analysis_window: deque[tuple[int, AnalysisFrame, int, bool]] = deque()
    next_emit = 0
    last_index = -1
    shot_id = 0
    prev_y64: np.ndarray | None = None

    def keeping() -> bool:
        return keep is not None and keep()

    def cancelled_discard() -> bool:
        return cancel_event is not None and cancel_event.is_set() and not keeping()

    def emit_one(index: int) -> TimedVideoFrame | np.ndarray:
        out_idx, frame = output_buffer.popleft()
        assert out_idx == index
        target_entry = next(entry for entry in analysis_window if entry[0] == index)
        _, target_analysis, target_shot, is_cut = target_entry
        neighbours = [
            (idx, analysis, shot)
            for idx, analysis, shot, _ in analysis_window
            if index - STAGE_A_RADIUS <= idx <= index + STAGE_A_RADIUS
        ]
        gain = processor.stage_a_gain(target_analysis, target_shot, neighbours, index)
        if diagnostics is not None:
            diagnostics.append((gain, is_cut))
        result_rgb = processor.apply_frame(_pixels(frame), target_analysis, gain, is_cut)
        min_analysis = max(0, index + 1 - STAGE_A_RADIUS)
        while analysis_window and analysis_window[0][0] < min_analysis:
            analysis_window.popleft()
        return _with_pixels(frame, result_rgb)

    stopped = False
    try:
        try:
            for stream_index, (frame, source) in enumerate(frames):
                if cancelled_discard():
                    raise InterruptedError("video job cancelled")
                analysis = (
                    source if isinstance(source, AnalysisFrame) else prepare_analysis_frame(source)
                )
                if prev_y64 is None:
                    is_cut = True
                else:
                    is_cut = is_scene_cut(prev_y64, analysis.y64)
                    if is_cut:
                        shot_id += 1
                prev_y64 = analysis.y64

                output_buffer.append((stream_index, frame))
                analysis_window.append((stream_index, analysis, shot_id, is_cut))
                last_index = stream_index

                while next_emit + STAGE_A_RADIUS <= stream_index:
                    if on_frame is not None:
                        on_frame(next_emit)
                    yield emit_one(next_emit)
                    next_emit += 1
        except InterruptedError:
            if not keeping():
                raise
            stopped = True

        while next_emit <= last_index:
            if cancelled_discard():
                raise InterruptedError("video job cancelled")
            if on_frame is not None:
                on_frame(next_emit)
            yield emit_one(next_emit)
            next_emit += 1

        if stopped:
            raise InterruptedError("video job cancelled")
    finally:
        output_buffer.clear()
        analysis_window.clear()
        processor.close()
