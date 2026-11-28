"""Composable stream stages for the frame-by-frame video pipeline."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Protocol

import av
import numpy as np
import torch
from PIL import Image

from .deflicker import AnalysisFrame, prepare_analysis_frame
from .frame_interpolation import InterpolationTarget, interpolate_frames
from .hdr import anchor_hdr_detail, tone_map_to_sdr
from .inference import InferenceEngine, UnstableRestorationError
from .video_colour import colour_frames
from .video_io import TimedVideoFrame, uint8_chw_to_rgb_hwc

if TYPE_CHECKING:
    from .eta import RemainingTime
    from .face_detection import FaceMask
    from .video_pipeline import VideoFixStage, VideoJobConfig


def rgb_to_tensor(rgb: np.ndarray) -> torch.Tensor:
    """Convert an HxWx3 uint8 RGB array to a (3, H, W) float32 tensor in [0, 1]."""
    arr = rgb.astype(np.float32) / 255.0
    return torch.from_numpy(arr).permute(2, 0, 1)


@dataclass(frozen=True)
class StreamFrame:
    """One video frame plus the per-frame side data carried across stream stages."""

    frame: TimedVideoFrame
    frame_index: int = 0
    source_rgb: np.ndarray | None = None
    source_preview: np.ndarray | None = None
    analysis: AnalysisFrame | None = None
    tensor: torch.Tensor | None = None
    out_chw: np.ndarray | None = None

    def with_pixels(self, rgb: np.ndarray) -> StreamFrame:
        return replace(self, frame=self.frame.with_pixels(rgb), tensor=None, out_chw=None)


class VideoStreamStage(Protocol):
    """Stream transform taking an iterator of frames and yielding transformed frames."""

    def __call__(self, frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]: ...


def _noop_tile_progress(_completed: int, _total: int, _active_tile: int) -> None:
    """Tile timing is reported by ``tile_callback``; the engine requires a callback."""


@dataclass
class VideoChainContext:
    """Shared job configuration, callbacks, and mutable runtime state for stream stages."""

    config: VideoJobConfig
    engine: InferenceEngine
    cancel_event: threading.Event
    preserve_hdr: bool
    transfer: int
    precision_str: str
    fps: float
    interpolation: InterpolationTarget | None
    before_upscale: bool
    total_frames: int
    remaining_time: RemainingTime | None
    model_info: object | None
    native_scale: int
    output_scale: int
    skip_primary_sr: bool
    use_face_aware: bool
    output_width: int
    output_height: int
    job_started_at: float
    colour_index: int | None
    has_preprocess_stages: bool
    stage_offsets: list[float]
    stage_spans: list[float]
    primary_stage_index: int
    frame_started_cb: Callable[[int, int], None] | None
    frame_completed_cb: Callable[[int, int, str], None] | None
    enhanced_frame_cb: Callable[[int, int, np.ndarray], None] | None
    progress_cb: Callable[[int, int, float, float | None], None] | None
    tile_callback: Callable[..., None] | None
    tile_progress_cb: Callable[..., None] | None
    warning_callback: Callable[[str], None] | None
    source_frame_cb: Callable[[int, int, np.ndarray], None] | None
    stage_cb: Callable[..., None] | None
    synthesizer: Any
    keep_partial: bool | Callable[[], bool] | None
    now_fn: Callable[[], float]
    detect_faces_fn: Callable[..., FaceMask]
    process_face_aware_fn: Callable[..., np.ndarray]
    deflicker_fn: Callable[..., Iterator[TimedVideoFrame | np.ndarray]]
    prefetch_fn: Callable[[Iterator[Any], int], Iterator[Any]]
    release_after_last_fn: Callable[[Iterator[Any], Any, str], Iterator[Any]]
    should_keep_partial_fn: Callable[[Any], bool]
    colour_keyframe_interval: int
    inference_started_at: float | None = None
    frames_processed: int = 0
    warned_unstable: set[int] = field(default_factory=set)
    cached_face_mask: FaceMask | None = None
    frames_since_detection: int = 0
    field_order: str = "unknown"

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("video job cancelled")

    def mark_inference_started(self) -> None:
        if self.inference_started_at is None:
            self.inference_started_at = self.now_fn()

    def record_tile_eta(self, frame_index: int, stage_index: int, done: int, count: int) -> None:
        if self.remaining_time is None or not count:
            return
        progress_value = (
            frame_index
            + self.stage_offsets[stage_index]
            + self.stage_spans[stage_index] * (done / count)
        )
        if self.has_preprocess_stages and frame_index == 0 and done == 1 and stage_index > 0:
            self.remaining_time.skip_warmup(self.now_fn(), progress_value)
        else:
            self.remaining_time.update(self.now_fn(), progress_value)

    def make_tile_callback(
        self,
        frame_index: int,
        stage_index: int,
        source_rgb: np.ndarray,
        *,
        forward_tiles: bool,
    ) -> Callable[..., None]:
        def _callback(phase, tile, pixels, done, count, image_w, image_h, size) -> None:
            if phase == "completed":
                self.record_tile_eta(frame_index, stage_index, done, count)
            if forward_tiles and self.tile_progress_cb is not None:
                elapsed = self.now_fn() - self.job_started_at
                remaining = self.remaining_time.remaining() if self.remaining_time else None
                self.tile_progress_cb(
                    frame_index, self.total_frames, done, count, elapsed, remaining, size
                )
            if forward_tiles and self.tile_callback is not None:
                if pixels is not None and self.preserve_hdr:
                    core = source_rgb[
                        tile.core_y : tile.core_y + tile.core_h,
                        tile.core_x : tile.core_x + tile.core_w,
                    ]
                    pixels = anchor_hdr_detail(
                        core, pixels.transpose(1, 2, 0).copy(), self.transfer
                    ).transpose(2, 0, 1)
                self.tile_callback(phase, tile, pixels, done, count, image_w, image_h, size)

        return _callback

    def warn_unstable(self, stage_index: int, stage: VideoFixStage, error: Exception) -> None:
        if stage_index in self.warned_unstable:
            return
        self.warned_unstable.add(stage_index)
        label = stage.fix or "restoration"
        note = (
            f"The {label} fix ({stage.model_id or stage.model_path}) was "
            f"skipped for one or more frames because the model became "
            f"unstable: {error}"
        )
        if self.warning_callback is not None:
            self.warning_callback(note)
        add_note = getattr(self.engine, "add_note", None)
        if callable(add_note):
            add_note(note)

    def working_tensor(self, item: StreamFrame) -> torch.Tensor:
        if item.tensor is not None:
            return item.tensor
        rgb = item.frame.rgb
        return torch.from_numpy(rgb).permute(2, 0, 1) if self.preserve_hdr else rgb_to_tensor(rgb)


@dataclass(frozen=True)
class VideoStepContext:
    """Per-stage position metadata passed to ``VIDEO_STEP_HANDLERS`` factories."""

    shared: VideoChainContext
    stage_index: int
    report_tiles: bool
    is_final_stage: bool


VideoStepHandler = Callable[["VideoFixStage", VideoStepContext], VideoStreamStage]


def _build_fix_model_stage(stage: VideoFixStage, step_ctx: VideoStepContext) -> VideoStreamStage:
    """Build a per-frame 1x restoration model stage (used before or after Colour)."""
    if not stage.model_path or stage.model_info is None:
        raise ValueError(f"Video fix stage {stage.step_id!r} requires a model path and model info.")
    ctx = step_ctx.shared
    halo = max(ctx.config.halo, stage.halo)

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        float_options = (
            {"float_output": True} if (step_ctx.report_tiles and ctx.preserve_hdr) else {}
        )
        for item in frames:
            if step_ctx.report_tiles:
                ctx.check_cancelled()
            ctx.engine.switch_model(stage.model_path)
            if step_ctx.report_tiles:
                source_rgb = item.source_rgb if item.source_rgb is not None else item.frame.rgb
                tile_cb = ctx.make_tile_callback(
                    item.frame_index,
                    step_ctx.stage_index,
                    source_rgb,
                    forward_tiles=step_ctx.is_final_stage,
                )
                tensor = ctx.working_tensor(item)
                progress_cb = _noop_tile_progress
            else:
                tile_cb = None
                tensor = rgb_to_tensor(item.frame.rgb)
                progress_cb = lambda *_: None  # noqa: E731
            try:
                output = ctx.engine.process_frame(
                    img_tensor=tensor,
                    model_info=stage.model_info,
                    tile_size=ctx.config.tile_size,
                    halo=halo,
                    cancel_event=ctx.cancel_event,
                    progress_callback=progress_cb,
                    safe_memory=ctx.config.safe_memory,
                    **({"tile_callback": tile_cb} if tile_cb is not None else {}),
                    whole_frame=True,
                    **float_options,
                )
            except UnstableRestorationError as error:
                ctx.warn_unstable(step_ctx.stage_index, stage, error)
                yield item
                continue

            if step_ctx.report_tiles:
                next_tensor = (
                    torch.from_numpy(np.array(output, copy=True)).float()
                    if ctx.preserve_hdr
                    else torch.from_numpy(np.array(output, copy=True)).float().div_(255.0)
                )
                yield replace(item, out_chw=output, tensor=next_tensor)
            else:
                yield item.with_pixels(uint8_chw_to_rgb_hwc(output))

    return _stage


def _build_colour_stage(stage: VideoFixStage, step_ctx: VideoStepContext) -> VideoStreamStage:
    """Build the temporal keyframe-propagated Colour stage."""
    if not stage.model_path or stage.model_info is None:
        raise ValueError("Video colour stage requires a model path and model info.")
    ctx = step_ctx.shared
    interval = ctx.colour_keyframe_interval
    halo = max(ctx.config.halo, stage.halo)

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        pending: deque[StreamFrame] = deque()
        prepared = 0
        first_output = False
        first_batch = min(interval + 1, ctx.total_frames or interval + 1)

        def input_pixels() -> Iterator[np.ndarray]:
            nonlocal prepared
            for item in frames:
                pending.append(item)
                # Colour needs a batch of frames before the first one can be finished;
                # until then the frame progress cannot move, so the batch is counted.
                prepared += 1
                if not first_output and ctx.stage_cb is not None:
                    ctx.stage_cb("colour_preparing", prepared, first_batch)
                yield item.frame.rgb

        def colourise(pixels: np.ndarray) -> np.ndarray:
            ctx.engine.switch_model(stage.model_path)
            result = ctx.engine.process_frame(
                img_tensor=rgb_to_tensor(pixels),
                model_info=stage.model_info,
                tile_size=ctx.config.tile_size,
                halo=halo,
                cancel_event=ctx.cancel_event,
                progress_callback=lambda *_: None,
                safe_memory=ctx.config.safe_memory,
                whole_frame=True,
            )
            return uint8_chw_to_rgb_hwc(result)

        for coloured in colour_frames(input_pixels(), colourise, interval=interval):
            first_output = True
            origin = pending.popleft()
            yield origin.with_pixels(coloured)

    return _stage


def _build_deinterlace_stage(stage: VideoFixStage, step_ctx: VideoStepContext) -> VideoStreamStage:
    ctx = step_ctx.shared
    mode = stage.params.get("mode", "frame")
    parity = stage.params.get("parity", "auto")
    field_order = getattr(ctx, "field_order", "unknown")
    if parity == "auto" and field_order == "progressive":

        def passthrough(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
            for item in frames:
                ctx.check_cancelled()
                yield item

        return passthrough
    if parity == "auto":
        parity = field_order if field_order in {"tff", "bff"} else "auto"

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        graph = None
        pending: deque[StreamFrame] = deque()
        emitted = 0

        def drain():
            nonlocal emitted
            while True:
                try:
                    filtered = graph.pull()
                except (av.error.BlockingIOError, av.error.EOFError):
                    break
                ctx.check_cancelled()
                origin = pending[0]
                rgb = filtered.to_ndarray(format=pixel_format)
                if is_float:
                    rgb = rgb.astype(np.float32) / 65535.0
                half = origin.frame.duration / 2
                timing = replace(
                    origin.frame,
                    rgb=rgb,
                    timestamp=origin.frame.timestamp
                    + (half if mode == "field" and emitted % 2 else 0),
                    duration=half if mode == "field" else origin.frame.duration,
                    time_base=origin.frame.time_base / 2
                    if mode == "field"
                    else origin.frame.time_base,
                )
                yield replace(origin, frame=timing, tensor=None, out_chw=None)
                emitted += 1
                if mode == "frame" or emitted % 2 == 0:
                    pending.popleft()

        for item in frames:
            ctx.check_cancelled()
            rgb = item.frame.rgb
            is_float = rgb.dtype == np.float32
            high_depth = rgb.dtype == np.uint16 or is_float
            pixel_format = "rgb48le" if high_depth else "rgb24"
            planar = "gbrp16le" if high_depth else "gbrp"
            if is_float:
                rgb = np.rint(np.clip(rgb, 0, 1) * 65535).astype(np.uint16)
            frame = av.VideoFrame.from_ndarray(rgb, format=pixel_format)
            frame.pts = item.frame.index
            frame.time_base = item.frame.duration
            if graph is None:
                graph = av.filter.Graph()
                source = graph.add_buffer(template=frame)
                convert = graph.add("format", f"pix_fmts={planar}")
                deinterlace = graph.add(
                    "bwdif",
                    f"mode=send_{'field' if mode == 'field' else 'frame'}:parity={parity}:deint=all",
                )
                sink = graph.add("buffersink")
                source.link_to(convert)
                convert.link_to(deinterlace)
                deinterlace.link_to(sink)
                graph.configure()
            pending.append(item)
            graph.push(frame)
            yield from drain()
        if graph is not None:
            graph.push(None)
            yield from drain()

    return _stage


# Dispatch video preprocessing/fix/grade steps by registry step id.
VIDEO_STEP_HANDLERS: dict[str, VideoStepHandler] = {
    "deinterlace": _build_deinterlace_stage,
    "blur": _build_fix_model_stage,
    "jpeg": _build_fix_model_stage,
    "noise": _build_fix_model_stage,
    "colour": _build_colour_stage,
    "restore": _build_fix_model_stage,
}


def _interpolate_before_stage(ctx: VideoChainContext) -> VideoStreamStage:
    assert ctx.interpolation is not None
    target = ctx.interpolation

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        # On this thread, not the decoder's: one thread drives the GPU.
        timed = interpolate_frames(
            (item.frame for item in frames), target, ctx.synthesizer, ctx.cancel_event
        )
        for frame in timed:
            yield StreamFrame(frame=frame)

    return _stage


def _capture_source_side_data_stage(ctx: VideoChainContext) -> VideoStreamStage:
    """Capture 640 px preview and de-flicker analysis before Colour / pre-Colour fixes."""

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        for item in frames:
            ctx.check_cancelled()
            ctx.mark_inference_started()
            rgb = item.frame.rgb
            preview = None
            if ctx.source_frame_cb is not None:
                stride = max(1, int(np.ceil(max(rgb.shape[:2]) / 640)))
                preview = rgb[::stride, ::stride].copy()
            analysis = prepare_analysis_frame(rgb) if ctx.config.deflicker else None
            yield replace(item, source_preview=preview, analysis=analysis)

    return _stage


def _frame_begin_stage(ctx: VideoChainContext) -> VideoStreamStage:
    """Fire frame-start/source callbacks and initialize the per-frame working state."""

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        for output_frame_index, item in enumerate(frames):
            ctx.check_cancelled()
            ctx.mark_inference_started()
            rgb = item.frame.rgb
            if ctx.frame_started_cb is not None:
                ctx.frame_started_cb(output_frame_index, ctx.total_frames)
            if ctx.source_frame_cb is not None:
                source = item.source_preview if item.source_preview is not None else rgb
                ctx.source_frame_cb(output_frame_index, ctx.total_frames, source)
            analysis = item.analysis
            if ctx.config.deflicker and analysis is None:
                analysis = prepare_analysis_frame(rgb)
            tensor = (
                torch.from_numpy(rgb).permute(2, 0, 1) if ctx.preserve_hdr else rgb_to_tensor(rgb)
            )
            out_chw = rgb.transpose(2, 0, 1) if ctx.colour_index is not None else None
            yield replace(
                item,
                frame_index=output_frame_index,
                source_rgb=rgb if (ctx.preserve_hdr or ctx.use_face_aware) else None,
                analysis=analysis,
                tensor=tensor,
                out_chw=out_chw,
            )

    return _stage


def _primary_upscale_stage(ctx: VideoChainContext) -> VideoStreamStage:
    """Run the primary SR/restoration checkpoint (general or face-aware)."""

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        float_options = {"float_output": True} if ctx.preserve_hdr else {}
        for item in frames:
            source_rgb = item.source_rgb if item.source_rgb is not None else item.frame.rgb
            tensor = ctx.working_tensor(item)
            observed_tile = ctx.make_tile_callback(
                item.frame_index,
                ctx.primary_stage_index,
                source_rgb,
                forward_tiles=True,
            )
            if ctx.use_face_aware:
                if (
                    ctx.cached_face_mask is None
                    or ctx.frames_since_detection >= ctx.config.face_detection_interval
                ):
                    detect_input = (
                        tone_map_to_sdr(source_rgb, ctx.transfer, 9)
                        if ctx.preserve_hdr
                        else source_rgb
                    )
                    ctx.cached_face_mask = ctx.detect_faces_fn(
                        detect_input, cancel_event=ctx.cancel_event
                    )
                    ctx.frames_since_detection = 0
                else:
                    ctx.frames_since_detection += 1

                if ctx.cached_face_mask.has_faces:
                    out_chw = ctx.process_face_aware_fn(
                        engine=ctx.engine,
                        img_tensor=tensor,
                        face_mask=ctx.cached_face_mask,
                        general_model_path=ctx.config.model_path,
                        face_model_path=ctx.config.face_model_path,
                        general_model_info=ctx.model_info,
                        face_model_info=ctx.config.face_model_info,
                        device_str=ctx.config.device_str,
                        precision_str=ctx.precision_str,
                        tile_size=ctx.config.tile_size,
                        halo=ctx.config.halo,
                        cancel_event=ctx.cancel_event,
                        safe_memory=ctx.config.safe_memory,
                        face_fidelity=ctx.config.face_fidelity,
                        tile_callback=observed_tile,
                        **float_options,
                    )
                else:
                    # No faces detected — use the general model. The face model was
                    # loaded last, and a frame with faces leaves either one active.
                    ctx.engine.switch_model(ctx.config.model_path)
                    out_chw = ctx.engine.process_frame(
                        img_tensor=tensor,
                        model_info=ctx.model_info,
                        tile_size=ctx.config.tile_size,
                        halo=ctx.config.halo,
                        cancel_event=ctx.cancel_event,
                        progress_callback=_noop_tile_progress,
                        safe_memory=ctx.config.safe_memory,
                        tile_callback=observed_tile,
                        whole_frame=True,
                        **float_options,
                    )
            else:
                if ctx.has_preprocess_stages:
                    ctx.engine.switch_model(ctx.config.model_path)
                out_chw = ctx.engine.process_frame(
                    img_tensor=tensor,
                    model_info=ctx.model_info,
                    tile_size=ctx.config.tile_size,
                    halo=ctx.config.halo,
                    cancel_event=ctx.cancel_event,
                    progress_callback=_noop_tile_progress,
                    safe_memory=ctx.config.safe_memory,
                    tile_callback=observed_tile,
                    whole_frame=True,
                    **float_options,
                )
            yield replace(item, out_chw=out_chw)

    return _stage


def _resize_and_anchor_stage(ctx: VideoChainContext) -> VideoStreamStage:
    """Convert model output to target RGB, resize when requested, and anchor HDR detail."""

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        for item in frames:
            source_rgb = item.source_rgb if item.source_rgb is not None else item.frame.rgb
            out_chw = item.out_chw
            if ctx.skip_primary_sr:
                if ctx.preserve_hdr:
                    if out_chw is not None and item.tensor is not None:
                        out_rgb = item.tensor.permute(1, 2, 0).clamp(0, 1).numpy().copy()
                        out_rgb = anchor_hdr_detail(source_rgb, out_rgb, ctx.transfer)
                    else:
                        out_rgb = item.frame.rgb
                else:
                    out_rgb = (
                        uint8_chw_to_rgb_hwc(out_chw) if out_chw is not None else item.frame.rgb
                    )
            elif ctx.preserve_hdr:
                assert out_chw is not None
                out_rgb = out_chw.transpose(1, 2, 0)
                if ctx.output_scale != ctx.native_scale:
                    out_rgb = (
                        torch.nn.functional.interpolate(
                            torch.from_numpy(out_chw).unsqueeze(0),
                            size=(ctx.output_height, ctx.output_width),
                            mode="bicubic",
                            align_corners=False,
                            antialias=True,
                        )[0]
                        .permute(1, 2, 0)
                        .clamp(0, 1)
                        .numpy()
                        .copy()
                    )
                out_rgb = anchor_hdr_detail(source_rgb, out_rgb, ctx.transfer)
            else:
                assert out_chw is not None
                out_rgb = uint8_chw_to_rgb_hwc(out_chw)
                if ctx.output_scale != ctx.native_scale:
                    out_rgb = np.array(
                        Image.fromarray(out_rgb).resize(
                            (ctx.output_width, ctx.output_height), Image.Resampling.LANCZOS
                        ),
                        dtype=np.uint8,
                    )
            yield replace(item.with_pixels(out_rgb), source_rgb=None, out_chw=None)

    return _stage


def _frame_complete_stage(ctx: VideoChainContext) -> VideoStreamStage:
    """Record frame completion, preview, and progress/ETA updates."""

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        for item in frames:
            out_rgb = item.frame.rgb
            ctx.frames_processed += 1
            if ctx.enhanced_frame_cb is not None:
                ctx.enhanced_frame_cb(item.frame_index, ctx.total_frames, out_rgb)
            if ctx.frame_completed_cb is not None:
                ctx.frame_completed_cb(item.frame_index, ctx.total_frames, "")
            if ctx.progress_cb is not None:
                now = ctx.now_fn()
                if ctx.remaining_time is not None:
                    ctx.remaining_time.update(now, ctx.frames_processed)
                ctx.progress_cb(
                    min(ctx.frames_processed, max(0, ctx.total_frames - 1))
                    if ctx.interpolation is not None
                    and not ctx.before_upscale
                    and ctx.total_frames > 0
                    else ctx.frames_processed,
                    ctx.total_frames,
                    now - ctx.job_started_at,
                    ctx.remaining_time.remaining() if ctx.remaining_time else None,
                )
            yield item

    return _stage


def _deflicker_stage(ctx: VideoChainContext) -> VideoStreamStage:
    """Apply temporal source-driven de-flicker using attached AnalysisFrame side data."""

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        def report_deflicker(index: int) -> None:
            if ctx.stage_cb is not None:
                ctx.stage_cb("deflickering", index + 1, ctx.total_frames)

        def paired():
            for item in frames:
                assert item.analysis is not None
                yield (item.frame, item.analysis)

        smoothed = ctx.deflicker_fn(
            paired(),
            ctx.config.deflicker_window,
            ctx.cancel_event,
            report_deflicker,
            keep=lambda: ctx.should_keep_partial_fn(
                ctx.config.keep_partial if ctx.keep_partial is None else ctx.keep_partial
            ),
            device_str=ctx.config.device_str,
        )
        for frame in smoothed:
            yield StreamFrame(frame=frame)

    return _stage


def _interpolate_after_stage(ctx: VideoChainContext) -> VideoStreamStage:
    assert ctx.interpolation is not None
    target = ctx.interpolation

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        timed = interpolate_frames(
            (item.frame for item in frames), target, ctx.synthesizer, ctx.cancel_event
        )
        for frame in timed:
            yield StreamFrame(frame=frame)
        if ctx.progress_cb is not None:
            ctx.progress_cb(
                ctx.frames_processed,
                ctx.total_frames,
                ctx.now_fn() - ctx.job_started_at,
                ctx.remaining_time.remaining() if ctx.remaining_time else None,
            )

    return _stage


def _slow_motion_stage(ctx: VideoChainContext) -> VideoStreamStage:
    """Play the frames the chain made over ``factor`` times as long, at their own rate."""
    assert ctx.interpolation is not None
    factor = ctx.interpolation.multiplier

    def _stage(frames: Iterator[StreamFrame]) -> Iterator[StreamFrame]:
        origin: Fraction | None = None
        for item in frames:
            frame = item.frame
            if origin is None:
                origin = frame.timestamp
            stretched = replace(
                frame,
                timestamp=origin + (frame.timestamp - origin) * factor,
                duration=frame.duration * factor,
            )
            yield replace(item, frame=stretched)

    return _stage


def _dispatch_preprocess_stage(
    stage: VideoFixStage,
    ctx: VideoChainContext,
    stage_index: int,
    *,
    report_tiles: bool,
    is_final_stage: bool,
) -> VideoStreamStage:
    handler = VIDEO_STEP_HANDLERS.get(stage.step_id)
    if handler is None:
        raise ValueError(f"No video handler registered for step {stage.step_id!r}.")
    step_ctx = VideoStepContext(
        shared=ctx,
        stage_index=stage_index,
        report_tiles=report_tiles,
        is_final_stage=is_final_stage,
    )
    return handler(stage, step_ctx)


def build_video_stages(ctx: VideoChainContext) -> list[VideoStreamStage]:
    """Build the ordered stream stages from ``ctx.config`` without executing them."""
    stages: list[VideoStreamStage] = []
    if ctx.colour_index is None:
        stages.append(lambda s: ctx.prefetch_fn(s, 4))
    for idx, stage in enumerate(ctx.config.preprocess_stages):
        if stage.step_id == "deinterlace":
            stages.append(
                _dispatch_preprocess_stage(
                    stage, ctx, idx, report_tiles=False, is_final_stage=False
                )
            )
    if ctx.before_upscale and ctx.interpolation is not None:
        stages.append(_interpolate_before_stage(ctx))

    preprocess = ctx.config.preprocess_stages
    if ctx.colour_index is not None:
        stages.append(_capture_source_side_data_stage(ctx))
        for idx, stage in enumerate(preprocess[: ctx.colour_index]):
            if stage.step_id == "deinterlace":
                continue
            stages.append(
                _dispatch_preprocess_stage(
                    stage, ctx, idx, report_tiles=False, is_final_stage=False
                )
            )
        colour_stage = preprocess[ctx.colour_index]
        stages.append(
            _dispatch_preprocess_stage(
                colour_stage, ctx, ctx.colour_index, report_tiles=False, is_final_stage=False
            )
        )
        after_colour_start = ctx.colour_index + 1
    else:
        after_colour_start = 0

    stages.append(_frame_begin_stage(ctx))

    for idx in range(after_colour_start, len(preprocess)):
        stage = preprocess[idx]
        if stage.step_id == "deinterlace":
            continue
        is_final = ctx.skip_primary_sr and idx == len(preprocess) - 1
        stages.append(
            _dispatch_preprocess_stage(stage, ctx, idx, report_tiles=True, is_final_stage=is_final)
        )

    if not ctx.skip_primary_sr:
        stages.append(_primary_upscale_stage(ctx))
    stages.append(_resize_and_anchor_stage(ctx))
    stages.append(_frame_complete_stage(ctx))
    stages.append(lambda s: ctx.release_after_last_fn(s, ctx.engine, ctx.config.device_str))

    if ctx.config.deflicker:
        stages.append(_deflicker_stage(ctx))
    if ctx.interpolation is not None and not ctx.before_upscale:
        stages.append(_interpolate_after_stage(ctx))
    if ctx.config.slow_motion:
        stages.append(_slow_motion_stage(ctx))
    if ctx.colour_index is None:
        stages.append(lambda s: ctx.prefetch_fn(s, 2))
    return stages


def run_video_chain(
    source_frames: Iterator[TimedVideoFrame],
    ctx: VideoChainContext,
    stages: list[VideoStreamStage] | None = None,
) -> Iterator[TimedVideoFrame]:
    """Compose and run the full video stream chain, yielding frames for the encoder."""
    built = build_video_stages(ctx) if stages is None else stages
    stream: Iterator[StreamFrame] = (StreamFrame(frame=frame) for frame in source_frames)
    for stage in built:
        stream = stage(stream)
    for item in stream:
        yield item.frame
