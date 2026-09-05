#!/usr/bin/env python3
"""Render a local, inspectable video regression report using the verified Quick model.

No downloads or network access. FFmpeg is used only to construct test media;
production processing uses LocalSR's PyAV pipeline. This is not a hardware score.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import av
import numpy as np
import torch
from PIL import Image, ImageDraw

from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import ModelAdapter
from localsr.core.model_catalog import CATALOG_BY_ID, ModelStore
from localsr.core.video_io import decode_timed_frames, probe_video
from localsr.core.video_pipeline import VideoJobConfig, _deflicker_frames, run_video_job
from localsr.worker.server import WorkerServer


def study_frame(index: int) -> np.ndarray:
    """An original geometric motion study with text, curves and fine line detail."""
    image = Image.new("RGB", (256, 160), "#e8e2d4")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 255, 27), fill="#263d36")
    draw.text((12, 8), "LOCALSR / MOTION STUDY", fill="#f2ebdb")
    draw.text((12, 140), f"FRAME {index:02d}    DETAIL / COLOUR / MOTION", fill="#263d36")
    for x in range(13, 240, 6):
        draw.line((x, 104, x, 130), fill="#72796a", width=1)
    draw.rectangle((170, 42, 238, 91), fill="#c3c4ad")
    for n in range(10):
        draw.line((174 + n * 6, 46, 174 + n * 6, 87), fill="#263d36", width=1)
    x = 22 + (index * 6) % 110
    draw.ellipse((x, 43, x + 37, 80), fill="#cc6039")
    draw.ellipse((x + 7, 48, x + 14, 55), fill="#efb87e")
    draw.line((12, 97, 241, 97), fill="#263d36")
    draw.rectangle((12 + index * 8 % 225, 91, 15 + index * 8 % 225, 94), fill="white")
    return np.asarray(image)


def write_fixture(path: Path, timestamps: list[int]) -> None:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=25)
        stream.width, stream.height, stream.pix_fmt = 256, 160, "yuv420p"
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.options = {"crf": "14", "bf": "0"}
        for index, stamp in enumerate(timestamps):
            frame = av.VideoFrame.from_ndarray(study_frame(index), format="rgb24")
            frame.pts, frame.time_base = stamp, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def inspect(path: Path) -> dict:
    with av.open(str(path)) as container:
        audio = [
            {
                "codec": stream.codec_context.name,
                "start": float((stream.start_time or 0) * stream.time_base),
                "duration": float(stream.duration * stream.time_base) if stream.duration else None,
            }
            for stream in container.streams.audio
        ]
    frames = list(decode_timed_frames(str(path)))
    return {
        "probe": asdict(probe_video(str(path))),
        "audio": audio,
        "timestamps": [float(frame.timestamp) for frame in frames],
        "durations": [float(frame.duration) for frame in frames],
    }


def run(output: Path, device: str) -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Install FFmpeg to generate the benchmark fixtures.")
    store = ModelStore()
    model = CATALOG_BY_ID["span_photo_x4"]
    if not store.is_installed(model):
        raise RuntimeError(
            "Download and verify Quick in LocalSR first; this benchmark never downloads weights."
        )
    output.mkdir(parents=True, exist_ok=False)
    assets = Path(__file__).with_name("video_benchmark")
    for asset in assets.iterdir():
        if asset.is_file():
            shutil.copy2(asset, output / asset.name)

    def ff(*args):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", *map(str, args)], check=True)

    cfr, vfr = output / "constant.mp4", output / "variable.mp4"
    write_fixture(cfr, [index * 80 for index in range(24)])
    write_fixture(vfr, [0, 40, 80, 120, 480, 840, 1200, 1560, 1920, 2280])
    for source in (cfr, vfr):
        with_audio = source.with_stem(source.stem + "-audio")
        ff(
            "-i",
            source,
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:duration=3",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            with_audio,
        )
    rotated = output / "portrait.mp4"
    ff("-display_rotation", "90", "-i", output / "constant-audio.mp4", "-c", "copy", rotated)
    adapter = ModelAdapter()
    info = adapter.inspect(str(store.path_for(model)))
    engine = InferenceEngine(adapter)
    report = {
        "suite": "localsr-video-contract-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model.model_id,
        "model_sha256": model.sha256,
        "device": device,
        "torch": torch.__version__,
        "pyav": av.__version__,
        "cases": [],
        "scope": "Small generated SDR clips on the local source runtime. Not installed-platform acceptance or a hardware score.",
        "seedvr2_inference": "not-run",
    }
    cases = [
        (
            "constant",
            "The moving study",
            "Constant-rate video with copied audio.",
            output / "constant-audio.mp4",
            {},
        ),
        (
            "variable",
            "Timing, intact",
            "Uneven frame intervals, preserved through enhancement.",
            output / "variable-audio.mp4",
            {},
        ),
        (
            "portrait",
            "A different orientation",
            "A 90° display transform normalized into upright pixels.",
            rotated,
            {},
        ),
        (
            "trim",
            "Just the selected moment",
            "Frames 2–6 of the variable-rate source, with audio.",
            output / "variable-audio.mp4",
            {"start_frame": 2, "end_frame": 6},
        ),
        (
            "deflicker",
            "Motion stays in the picture",
            "Conservative de-flicker; this optional filter remains Labs.",
            cfr,
            {"deflicker": True},
        ),
    ]
    for case_id, title, description, source, options in cases:
        print(f"Rendering {title} on {device}…", flush=True)
        destination = output / f"{case_id}-enhanced.mp4"
        started = time.monotonic()
        messages = []
        result = run_video_job(
            VideoJobConfig(
                video_path=str(source),
                model_path=str(store.path_for(model)),
                output_video_path=str(destination),
                model_info=info,
                device_str=device,
                precision_str="fp32",
                tile_size=128,
                halo=16,
                safe_memory=False,
                crf=18,
                **options,
            ),
            engine,
            threading.Event(),
            warning_callback=messages.append,
        )
        before, after = inspect(source), inspect(destination)
        selected = list(
            decode_timed_frames(str(source), options.get("start_frame"), options.get("end_frame"))
        )
        expected = [float(frame.timestamp - selected[0].timestamp) for frame in selected]
        shift = max(
            (abs(a - b) for a, b in zip(expected, after["timestamps"], strict=False)), default=0
        )
        expected_duration = float(
            selected[-1].timestamp + selected[-1].duration - selected[0].timestamp
        )
        audio_ok = len(before["audio"]) == len(after["audio"])
        checks = {
            "frame_count": len(selected) == len(after["timestamps"]),
            "timing": shift <= 0.001,
            "duration": abs(expected_duration - after["probe"]["duration_seconds"]) <= 0.06,
            "dimensions": after["probe"]["width"] == selected[0].rgb.shape[1] * 4
            and after["probe"]["height"] == selected[0].rgb.shape[0] * 4,
            "audio": audio_ok,
        }
        source_poster, output_poster = f"{case_id}-source.jpg", f"{case_id}-output.jpg"
        Image.fromarray(selected[0].rgb).save(output / source_poster, quality=95)
        rendered = next(decode_timed_frames(str(destination))).rgb
        Image.fromarray(rendered).save(output / output_poster, quality=95)
        report["cases"].append(
            {
                "id": case_id,
                "title": title,
                "description": description,
                "source": source.name,
                "output": destination.name,
                "source_poster": source_poster,
                "output_poster": output_poster,
                "source_start": float(selected[0].timestamp),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "frames": result.frames_processed,
                "max_timestamp_shift_ms": round(shift * 1000, 6),
                "checks": checks,
                "passed": all(checks.values()),
                "input": before,
                "enhanced": after,
                "warnings": messages,
            }
        )
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")

    moving = []
    for index in range(7):
        frame = np.zeros((20, 50, 3), dtype=np.uint8)
        frame[8:11, 2 + index * 6 : 5 + index * 6] = 255
        moving.append(frame)
    filtered = list(_deflicker_frames(iter(moving), 3, threading.Event()))

    def filmstrip(frames, path):
        Image.fromarray(np.concatenate(frames, axis=1)).resize(
            (1050, 60), Image.Resampling.NEAREST
        ).save(path)

    filmstrip(moving, output / "motion-source.png")
    filmstrip(filtered, output / "motion-output.png")
    report["motion"] = {
        "passed": all(np.array_equal(a, b) for a, b in zip(moving, filtered, strict=True)),
        "source": "motion-source.png",
        "output": "motion-output.png",
    }

    # Isolate request routing from the expensive, separately experimental model.
    class IdentityTemporalEngine:
        def process_frames(self, frames, **kwargs):
            return frames

    temporal_path = output / "temporal-routing.mp4"
    worker = SimpleNamespace(cancel_event=threading.Event(), _emit_live_preview=lambda _: None)
    with patch("localsr.worker.server.send_message", lambda _: None):
        WorkerServer._run_temporal_video_job(
            worker,
            "local-benchmark",
            {
                "video_path": str(output / "variable-audio.mp4"),
                "output_video_path": str(temporal_path),
                "start_frame": 2,
                "end_frame": 4,
                "preview_enabled": False,
            },
            lambda *_: IdentityTemporalEngine(),
        )
    temporal = inspect(temporal_path)
    report["temporal_routing"] = {
        "passed": len(temporal["timestamps"]) == 3 and len(temporal["audio"]) == 1,
        "engine": "identity substitute; actual worker and media I/O",
        "real_seedvr2": False,
        "output": temporal_path.name,
    }
    report["passed"] = (
        all(case["passed"] for case in report["cases"])
        and report["motion"]["passed"]
        and report["temporal_routing"]["passed"]
    )
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    html = (output / "index.html").read_text()
    (output / "index.html").write_text(
        html.replace("/* BENCHMARK_DATA */", json.dumps(report).replace("<", "\\u003c"))
    )
    print(json.dumps({"report": str(output / "index.html"), "passed": report["passed"]}, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", required=True, type=Path, help="A new directory for clips and report"
    )
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "cpu", "cuda"])
    args = parser.parse_args()
    device = args.device
    if device == "auto":
        device = (
            "cuda"
            if torch.cuda.is_available()
            else "mps"
            if torch.backends.mps.is_available()
            else "cpu"
        )
    raise SystemExit(0 if run(args.output.expanduser().resolve(), device)["passed"] else 1)
