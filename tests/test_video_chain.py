"""Byte-identity, callback-trace, and dispatch tests for the video stream chain."""

from __future__ import annotations

import hashlib
import json
import threading
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest
import torch
from test_frame_interpolation import RecordingSynthesizer

import localsr.core.video_pipeline as video_pipeline
from localsr.core.face_detection import FaceBox, FaceMask
from localsr.core.inference import InferenceEngine, UnstableRestorationError
from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.tiling import generate_tiles
from localsr.core.video_io import decode_timed_frames
from localsr.core.video_pipeline import VideoFixStage, VideoJobConfig, run_video_job

BASELINE_PATH = Path(__file__).parent / "fixtures" / "video_chain_baseline.json"


class _ReliefSynthesizer(RecordingSynthesizer):
    def __init__(self) -> None:
        super().__init__()
        self.memory_relief = None

    def synthesize(self, first, second, timesteps, cancel_event=None):
        if self.memory_relief is not None:
            self.memory_relief()
        return super().synthesize(first, second, timesteps, cancel_event)


def _hash_array(arr: np.ndarray | None) -> str | None:
    if arr is None:
        return None
    contiguous = np.ascontiguousarray(arr)
    digest = hashlib.sha256(contiguous.tobytes()).hexdigest()[:16]
    return f"{contiguous.dtype.name}:{contiguous.shape}:{digest}"


def _make_clip(
    path: Path,
    *,
    frames: int = 6,
    width: int = 32,
    height: int = 24,
    fps: int = 12,
    grayscale: bool = False,
    flicker: bool = False,
) -> Path:
    with av.open(str(path), mode="w", format="mp4") as container:
        stream = container.add_stream("libvpx-vp9", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"lossless": "1", "lag-in-frames": "0"}
        for idx in range(frames):
            rgb = np.empty((height, width, 3), dtype=np.uint8)
            if grayscale:
                base = 70 + idx * 15
                rgb[...] = base
                rgb[4:12, 6:18, :] = min(255, base + 40)
            else:
                rgb[:, :, 0] = 60 + idx * 20
                rgb[:, :, 1] = 200 - idx * 15
                rgb[:, :, 2] = 100 + idx * 10
                rgb[4:12, 6:18] = [220, 40 + idx * 20, 30]
            if flicker:
                gain = 1.0 + (0.08 if idx % 2 == 0 else -0.08)
                rgb = np.clip(rgb.astype(np.float32) * gain, 0, 255).round().astype(np.uint8)
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = idx
            frame.time_base = Fraction(1, fps)
            for pkt in stream.encode(frame):
                container.mux(pkt)
        for pkt in stream.encode():
            container.mux(pkt)
    return path


def _make_hdr_clip(
    path: Path, *, frames: int = 4, width: int = 32, height: int = 24, fps: int = 12
) -> Path:
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("ffv1", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p10le"
        stream.codec_context.color_primaries = 9
        stream.codec_context.color_trc = 16
        stream.codec_context.colorspace = 9
        stream.codec_context.color_range = 1
        for idx in range(frames):
            rgb = np.full((height, width, 3), 0.2 + idx * 0.15, dtype=np.float32)
            rgb[4:12, 6:18, 0] = min(0.95, 0.4 + idx * 0.1)
            frame = av.VideoFrame.from_ndarray(rgb, format="gbrpf32le")
            frame = frame.reformat(
                format="yuv420p10le",
                src_colorspace="BT2020",
                dst_colorspace="BT2020",
                src_color_range=2,
                dst_color_range=1,
            )
            frame.color_primaries, frame.color_trc, frame.colorspace, frame.color_range = (
                9,
                16,
                9,
                1,
            )
            frame.pts, frame.time_base = idx, Fraction(1, fps)
            for pkt in stream.encode(frame):
                container.mux(pkt)
        for pkt in stream.encode():
            container.mux(pkt)
    return path


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def perf_counter(self) -> float:
        self.now += 0.25
        return self.now


class _DeterministicTraceEngine:
    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.active = ""
        self.trace: list = []
        self.notes: list[str] = []

    def load_model(self, path, device_str, precision_str, model_info):
        self.active = path
        self.trace.append(
            [
                "engine.load_model",
                path,
                device_str,
                precision_str,
                int(getattr(model_info, "scale", 1)),
            ]
        )

    def switch_model(self, path):
        self.active = path
        self.trace.append(["engine.switch_model", path])

    def park_models(self):
        self.trace.append(["engine.park_models"])
        return True

    def release_model(self, path=None):
        self.trace.append(["engine.release_model", path])

    def add_note(self, note):
        self.notes.append(note)
        self.trace.append(["engine.add_note", note])

    def prepare_tiles(self, tile_size, halo, model_path):
        self.trace.append(["engine.prepare_tiles", tile_size, halo, model_path])

    @property
    def _loaded_device(self):
        return torch.device("cpu")

    @property
    def _loaded_precision(self):
        return torch.float32

    @property
    def active_model(self):
        return self.active

    def _transform(
        self, model_path: str, tensor: torch.Tensor, scale: int, float_output: bool
    ) -> np.ndarray:
        if "unstable" in model_path:
            raise UnstableRestorationError("synthetic instability")
        t = tensor.float()
        if scale > 1:
            t = torch.nn.functional.interpolate(
                t.unsqueeze(0), scale_factor=scale, mode="nearest"
            ).squeeze(0)
        if "noise" in model_path:
            t = (t + 0.04).clamp(0.0, 1.0)
        elif "blur" in model_path:
            t = (t - 0.02).clamp(0.0, 1.0)
        elif "colour" in model_path:
            t = t.clone()
            t[0] = (t[0] + 0.25).clamp(0.0, 1.0)
            t[2] = (t[2] - 0.15).clamp(0.0, 1.0)
        elif "face" in model_path:
            t = (t * 0.9 + 0.08).clamp(0.0, 1.0)
        else:
            t = (t * 0.95 + 0.01).clamp(0.0, 1.0)
        if float_output:
            return t.cpu().numpy().astype(np.float32)
        return (t * 255.0).round().clamp(0, 255).to(torch.uint8).cpu().numpy()

    def run_tile(
        self,
        tile,
        img_tensor,
        model_info,
        device,
        precision,
        halo,
        active_model,
        float_output=False,
    ):
        self.trace.append(
            [
                "engine.run_tile",
                self.active,
                tile.core_x,
                tile.core_y,
                tile.core_w,
                tile.core_h,
                float_output,
            ]
        )
        scale = int(getattr(model_info, "scale", 1))
        full = self._transform(self.active, img_tensor, scale, float_output)
        return full[:, tile.out_y : tile.out_y + tile.out_h, tile.out_x : tile.out_x + tile.out_w]

    def process_frame(
        self,
        img_tensor,
        model_info,
        tile_size,
        halo,
        cancel_event,
        progress_callback,
        safe_memory=True,
        tile_callback=None,
        whole_frame=True,
        float_output=False,
    ):
        self.trace.append(
            [
                "engine.process_frame",
                self.active,
                list(img_tensor.shape),
                tile_size,
                halo,
                whole_frame,
                float_output,
            ]
        )
        scale = int(getattr(model_info, "scale", 1))
        _, h, w = img_tensor.shape
        out = self._transform(self.active, img_tensor, scale, float_output)
        tiles = list(generate_tiles(w, h, tile_size, halo, scale))
        total_tiles = len(tiles)
        for i, tile in enumerate(tiles):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("cancelled")
            if tile_callback is not None:
                tile_callback(
                    "started", tile, None, i, total_tiles, w * scale, h * scale, tile_size
                )
            self.clock.now += 0.5
            core = out[
                :, tile.out_y : tile.out_y + tile.out_h, tile.out_x : tile.out_x + tile.out_w
            ]
            if tile_callback is not None:
                tile_callback(
                    "completed", tile, core, i + 1, total_tiles, w * scale, h * scale, tile_size
                )
            if progress_callback is not None:
                progress_callback(i + 1, total_tiles, tile_size)
        return out


def _make_info(scale: int, arch: str = "SPAN") -> NormalizedModelInfo:
    return NormalizedModelInfo(
        architecture=arch,
        scale=scale,
        in_channels=3,
        out_channels=3,
        tiling_supported=True,
        half_supported=False,
        size_requirements_min=1,
        size_requirements_mult=1,
        filename="fake.pth",
        warnings=[],
    )


def _case_config(case_name: str, tmp: Path):
    info_1x = _make_info(1)
    info_2x = _make_info(2)
    info_4x = _make_info(4)
    colour_info = _make_info(1, "DDColor")
    hat_info = _make_info(2, "HAT")

    builders = {
        "no_fixes": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4")),
                "upscale2x.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
            ),
            None,
        ),
        "two_fixes": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4")),
                "upscale2x.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                preprocess_stages=(
                    VideoFixStage(
                        "fix_noise.pth", info_1x, model_id="n", halo=2, fix="noise", time_factor=1.0
                    ),
                    VideoFixStage(
                        "fix_blur.pth", info_1x, model_id="b", halo=2, fix="blur", time_factor=1.5
                    ),
                ),
            ),
            None,
        ),
        "fix_colour_fix": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4", grayscale=True)),
                "upscale2x.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                preprocess_stages=(
                    VideoFixStage(
                        "fix_noise.pth", info_1x, model_id="n", halo=2, fix="noise", time_factor=1.0
                    ),
                    VideoFixStage(
                        "colour.pth",
                        colour_info,
                        model_id="c",
                        halo=0,
                        fix="colour",
                        time_factor=1.0,
                    ),
                    VideoFixStage(
                        "fix_blur.pth", info_1x, model_id="b", halo=2, fix="blur", time_factor=1.5
                    ),
                ),
            ),
            None,
        ),
        "deflicker": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4", frames=8, flicker=True)),
                "upscale2x.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                32,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                deflicker=True,
            ),
            None,
        ),
        "interp_before": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4", frames=4)),
                "upscale2x.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                32,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                interpolation="x2",
                interpolation_order="before_upscale",
            ),
            _ReliefSynthesizer(),
        ),
        "interp_after": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4", frames=4)),
                "upscale2x.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                32,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                interpolation="x2",
                interpolation_order="after_upscale",
            ),
            _ReliefSynthesizer(),
        ),
        "face_aware": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4")),
                "general.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                face_model_path="face.pth",
                face_model_info=info_2x,
                face_fidelity=0.7,
                face_detection_interval=0,
            ),
            None,
        ),
        "hdr_preserve": lambda: (
            VideoJobConfig(
                str(_make_hdr_clip(tmp / "hdr.mkv")),
                "hat.pth",
                str(tmp / "out.mkv"),
                hat_info,
                "cpu",
                "fp16",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                hdr_mode="preserve",
                start_frame=1,
                end_frame=3,
            ),
            None,
        ),
        "output_scale_differs": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4")),
                "upscale4x.pth",
                str(tmp / "out.mkv"),
                info_4x,
                "cpu",
                "fp32",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                output_scale=2,
            ),
            None,
        ),
        "skip_primary_sr": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4")),
                "upscale4x.pth",
                str(tmp / "out.mkv"),
                info_4x,
                "cpu",
                "fp32",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                output_scale=1,
                preprocess_stages=(
                    VideoFixStage(
                        "fix_noise.pth", info_1x, model_id="n", halo=2, fix="noise", time_factor=1.0
                    ),
                ),
            ),
            None,
        ),
        "colour_skipped": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4", grayscale=False)),
                "upscale2x.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                preprocess_stages=(
                    VideoFixStage(
                        "colour.pth",
                        colour_info,
                        model_id="c",
                        halo=0,
                        fix="colour",
                        time_factor=1.0,
                    ),
                ),
            ),
            None,
        ),
        "unstable_fix": lambda: (
            VideoJobConfig(
                str(_make_clip(tmp / "in.mp4")),
                "upscale2x.pth",
                str(tmp / "out.mkv"),
                info_2x,
                "cpu",
                "fp32",
                16,
                2,
                False,
                container="mkv",
                video_codec="ffv1",
                preprocess_stages=(
                    VideoFixStage(
                        "unstable_noise.pth",
                        info_1x,
                        model_id="u",
                        halo=2,
                        fix="noise",
                        time_factor=1.0,
                    ),
                ),
            ),
            None,
        ),
    }
    return builders[case_name]()


@pytest.mark.parametrize(
    "case_name",
    [
        "no_fixes",
        "two_fixes",
        "fix_colour_fix",
        "deflicker",
        "interp_before",
        "interp_after",
        "face_aware",
        "hdr_preserve",
        "output_scale_differs",
        "skip_primary_sr",
        "colour_skipped",
        "unstable_fix",
    ],
)
def test_video_chain_byte_and_callback_identity(
    case_name: str, tmp_path: Path, monkeypatch
) -> None:
    baselines = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    expected = baselines[case_name]

    torch.manual_seed(0)
    clock = _FakeClock()
    monkeypatch.setattr(video_pipeline, "time", SimpleNamespace(perf_counter=clock.perf_counter))
    face_calls: list[int] = []

    def fake_detect(rgb, cancel_event=None):
        idx = len(face_calls)
        face_calls.append(idx)
        mask = np.zeros(rgb.shape[:2], dtype=bool)
        if idx % 2 == 0:
            mask[:12, :16] = True
            return FaceMask(mask=mask, boxes=(FaceBox(0, 0, 16, 12, 0.99),))
        return FaceMask(mask=mask, boxes=())

    monkeypatch.setattr(video_pipeline, "detect_faces", fake_detect)

    engine = _DeterministicTraceEngine(clock)
    config, synthesizer = _case_config(case_name, tmp_path)
    trace = engine.trace

    def round_opt(val):
        return None if val is None else round(float(val), 6)

    result = run_video_job(
        config,
        engine,  # type: ignore[arg-type]
        threading.Event(),
        frame_started_cb=lambda idx, total: trace.append(["frame_started", idx, total]),
        source_frame_cb=lambda idx, total, rgb: trace.append(
            ["source_frame", idx, total, _hash_array(rgb)]
        ),
        tile_callback=lambda phase, tile, pixels, done, count, iw, ih, sz: trace.append(
            [
                "tile",
                phase,
                tile.core_x,
                tile.core_y,
                tile.core_w,
                tile.core_h,
                _hash_array(pixels),
                done,
                count,
                iw,
                ih,
                sz,
            ]
        ),
        tile_progress_cb=lambda frame, total, done, count, elapsed, rem, sz: trace.append(
            ["tile_progress", frame, total, done, count, round_opt(elapsed), round_opt(rem), sz]
        ),
        enhanced_frame_cb=lambda idx, total, rgb: trace.append(
            ["enhanced_frame", idx, total, _hash_array(rgb)]
        ),
        frame_completed_cb=lambda idx, total, b64: trace.append(
            ["frame_completed", idx, total, b64]
        ),
        progress_cb=lambda done, total, elapsed, rem: trace.append(
            ["progress", done, total, round_opt(elapsed), round_opt(rem)]
        ),
        warning_callback=lambda msg: trace.append(["warning", msg]),
        stage_cb=lambda stage, *counts: trace.append(["stage", stage, *counts]),
        synthesizer=synthesizer,
    )
    decoded_hashes = [
        [
            frame.index,
            str(frame.timestamp),
            str(frame.duration),
            str(frame.time_base),
            _hash_array(frame.rgb),
        ]
        for frame in decode_timed_frames(
            str(result.output_path),
            hdr_mode="preserve" if config.hdr_mode == "preserve" else "reject",
        )
    ]

    assert result.frames_processed == expected["frames_processed"]
    assert decoded_hashes == expected["decoded_hashes"]
    assert trace == expected["trace"]


def test_video_fix_stage_step_id_and_params_defaults() -> None:
    info = _make_info(1)
    colour_info = _make_info(1, "DDColor")

    noise_stage = VideoFixStage("n.pth", info, fix="noise")
    assert noise_stage.step_id == "noise"
    assert noise_stage.params == {}

    colour_stage = VideoFixStage("c.pth", colour_info)
    assert colour_stage.step_id == "colour"

    custom_stage = VideoFixStage("", None, step_id="custom_tint", params={"amount": 0.5})
    assert custom_stage.step_id == "custom_tint"
    assert custom_stage.params == {"amount": 0.5}


def test_video_step_handlers_dispatch_registered_step_with_params(
    tmp_path: Path, monkeypatch
) -> None:
    from localsr.core import video_chain

    assert {"blur", "jpeg", "noise", "colour", "restore"} <= set(video_chain.VIDEO_STEP_HANDLERS)

    source = _make_clip(tmp_path / "in.mp4", frames=3)
    output = tmp_path / "out.mkv"
    seen_params: list[dict] = []

    def build_add_constant_stage(stage: VideoFixStage, _ctx):
        seen_params.append(dict(stage.params))
        delta = int(stage.params["delta"])

        def _stage(frames):
            for item in frames:
                rgb = np.clip(item.frame.rgb.astype(np.int16) + delta, 0, 255).astype(np.uint8)
                yield item.with_pixels(rgb)

        return _stage

    monkeypatch.setitem(video_chain.VIDEO_STEP_HANDLERS, "custom_add", build_add_constant_stage)

    class FailOnLoadAdapter:
        def load(self, path, device, precision):
            raise AssertionError(f"Unexpected model load: {path}")

        def release(self):
            pass

    config = VideoJobConfig(
        video_path=str(source),
        model_path="",
        output_video_path=str(output),
        model_info=None,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=0,
        safe_memory=False,
        container="mkv",
        video_codec="ffv1",
        output_scale=1,
        preprocess_stages=(VideoFixStage("", None, step_id="custom_add", params={"delta": 15}),),
    )
    enhanced: list[np.ndarray] = []
    result = run_video_job(
        config,
        InferenceEngine(FailOnLoadAdapter()),
        threading.Event(),
        enhanced_frame_cb=lambda _idx, _total, rgb: enhanced.append(rgb.copy()),
    )
    assert result.frames_processed == 3
    assert seen_params == [{"delta": 15}]
    src_frames = [f.rgb for f in decode_timed_frames(str(source))]
    for src, out in zip(src_frames, enhanced, strict=True):
        expected = np.clip(src.astype(np.int16) + 15, 0, 255).astype(np.uint8)
        np.testing.assert_array_equal(out, expected)
    assert len(list(decode_timed_frames(str(output)))) == 3


def test_unknown_video_step_id_raises_value_error(tmp_path: Path) -> None:
    source = _make_clip(tmp_path / "in.mp4", frames=2)
    info = _make_info(1)
    config = VideoJobConfig(
        video_path=str(source),
        model_path="",
        output_video_path=str(tmp_path / "out.mkv"),
        model_info=None,
        device_str="cpu",
        precision_str="fp32",
        tile_size=64,
        halo=0,
        safe_memory=False,
        container="mkv",
        video_codec="ffv1",
        output_scale=1,
        preprocess_stages=(VideoFixStage("x.pth", info, step_id="does_not_exist"),),
    )
    with pytest.raises(ValueError, match="does_not_exist"):
        run_video_job(config, SimpleNamespace(), threading.Event())  # type: ignore[arg-type]


def test_video_job_refuses_stage_with_mask_path(tmp_path: Path, monkeypatch) -> None:
    from localsr.core import steps as steps_mod
    from localsr.worker.server import WorkerServer

    source = _make_clip(tmp_path / "in.mp4", frames=2)
    fix_file = tmp_path / "fix.pth"
    fix_file.write_bytes(b"fix")
    mask_file = tmp_path / "mask.png"
    mask_file.write_bytes(b"mask")

    masked_noise = replace_step_mask_support(steps_mod.STEPS_BY_ID["noise"])
    new_steps = tuple(masked_noise if s.id == "noise" else s for s in steps_mod.STEPS)
    monkeypatch.setattr(steps_mod, "STEPS", new_steps)
    monkeypatch.setattr("localsr.core.pipeline.STEPS", new_steps)

    server = WorkerServer()
    server.engine = InferenceEngine(SimpleNamespace(load=lambda *a: None, release=lambda: None))
    with pytest.raises(ValueError, match="mask"):
        server._run_video_job(
            "masked-video",
            {
                "video_path": str(source),
                "model_path": "",
                "output_video_path": str(tmp_path / "out.mkv"),
                "device": "cpu",
                "precision": "fp32",
                "tile_size": 64,
                "halo": 4,
                "safe_memory": False,
                "output_scale": 1,
                "stages": [
                    {
                        "kind": "restore",
                        "fix": "noise",
                        "model_id": "scunet",
                        "model_path": str(fix_file),
                        "role": "fix",
                        "mask_path": str(mask_file),
                    }
                ],
            },
        )


def replace_step_mask_support(step):
    from dataclasses import replace

    return replace(step, supports_mask=True)
