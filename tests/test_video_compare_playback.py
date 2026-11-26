"""Combined Compare playback copy layout, time pairing, kept-frame truncation and CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from localsr.core.video_io import TimedVideoFrame, decode_timed_frames
from localsr.core.video_playback import (
    CompareSource,
    pair_compare_frames,
    plan_compare_layout,
    prepare_compare_playback,
)


def _make_solid_clip(
    path: Path,
    *,
    width: int,
    height: int,
    fps: int,
    frames: int,
    colors: list[tuple[int, int, int]] | None = None,
    audio_seconds: float = 0.0,
) -> Path:
    container = av.open(str(path), mode="w")
    vstream = container.add_stream("vp9", rate=fps)
    vstream.width = width
    vstream.height = height
    vstream.pix_fmt = "yuv420p"
    vstream.options = {"crf": "18", "b": "0", "deadline": "realtime", "cpu-used": "8"}

    astream = None
    if audio_seconds > 0:
        astream = container.add_stream("libopus", rate=48000)
        astream.layout = "mono"

    for index in range(frames):
        color = colors[index] if colors else ((index * 37) % 256, 80, 160)
        rgb = np.full((height, width, 3), color, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        for packet in vstream.encode(frame):
            container.mux(packet)
    for packet in vstream.encode():
        container.mux(packet)

    if astream is not None:
        sr = 48000
        total_samples = int(round(audio_seconds * sr))
        pos = 0
        chunk = 960
        while pos < total_samples:
            n = min(chunk, total_samples - pos)
            t = np.arange(pos, pos + n, dtype=np.float32) / sr
            samples = (0.05 * np.sin(2 * np.pi * 440.0 * t)).reshape(1, -1)
            aframe = av.AudioFrame.from_ndarray(samples, format="flt", layout="mono")
            aframe.sample_rate = sr
            aframe.pts = pos
            for packet in astream.encode(aframe):
                container.mux(packet)
            pos += n
        for packet in astream.encode():
            container.mux(packet)

    container.close()
    return path


def _synthetic_stream(fps: int, count: int, tag: int) -> list[TimedVideoFrame]:
    tb = Fraction(1, fps)
    return [
        TimedVideoFrame(
            index=i,
            rgb=np.full((4, 4, 3), (tag, i, 0), dtype=np.uint8),
            timestamp=Fraction(i, fps),
            duration=tb,
            time_base=tb,
        )
        for i in range(count)
    ]


def test_plan_compare_layout_handles_2_to_4_sides_and_mixed_aspect_ratios():
    # Two landscape 16:9 clips (one upscaled above 1280) stack vertically with even rects.
    two_landscape = plan_compare_layout([(640, 360), (2560, 1440)], max_dimension=1280, gutter=4)
    assert (two_landscape.width, two_landscape.height) == (1280, 360 + 4 + 720)
    assert [(s.x, s.y, s.width, s.height) for s in two_landscape.slots] == [
        (0, 0, 640, 360),
        (0, 364, 1280, 720),
    ]

    # Two portrait clips sit side by side.
    two_portrait = plan_compare_layout([(720, 1280), (1440, 2560)], max_dimension=1280, gutter=4)
    assert (two_portrait.width, two_portrait.height) == (720 + 4 + 720, 1280)
    assert [(s.x, s.y, s.width, s.height) for s in two_portrait.slots] == [
        (0, 0, 720, 1280),
        (724, 0, 720, 1280),
    ]

    # Three and four sides use a 2x2 grid with non-overlapping even coordinates.
    four_mixed = plan_compare_layout(
        [(1920, 1080), (800, 800), (640, 480), (1080, 1920)], max_dimension=1280, gutter=4
    )
    assert four_mixed.width % 2 == 0 and four_mixed.height % 2 == 0
    for i, a in enumerate(four_mixed.slots):
        assert a.x % 2 == 0 and a.y % 2 == 0 and a.width % 2 == 0 and a.height % 2 == 0
        assert a.x + a.width <= four_mixed.width and a.y + a.height <= four_mixed.height
        for b in four_mixed.slots[i + 1 :]:
            separated = (
                a.x + a.width + 4 <= b.x
                or b.x + b.width + 4 <= a.x
                or a.y + a.height + 4 <= b.y
                or b.y + b.height + 4 <= a.y
            )
            assert separated


def test_pair_compare_frames_pairs_different_frame_rates_and_truncates_unfinished_sides():
    # 30 fps (30 frames = 1.0 s) vs 24 fps (24 frames = 1.0 s).
    paired = list(
        pair_compare_frames(
            [iter(_synthetic_stream(30, 30, 1)), iter(_synthetic_stream(24, 24, 2))]
        )
    )
    timestamps = [float(t) for t, _, _ in paired]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)
    # At t = 2.5 / 24 = 0.10417 s: 30 fps is at frame 3, 24 fps is at frame 2.
    at_104ms = max(item for item in paired if item[0] <= Fraction(5, 48))
    assert (at_104ms[2][0].index, at_104ms[2][1].index) == (3, 2)
    # Total duration equals 1.0 s.
    last_t, last_dur, _ = paired[-1]
    assert last_t + last_dur == Fraction(1, 1)

    # Unfinished 24 fps side with 12 kept frames (0.5 s) stops a 3.0 s 30 fps side at 0.5 s.
    truncated = list(
        pair_compare_frames(
            [iter(_synthetic_stream(30, 90, 1)), iter(_synthetic_stream(24, 24, 2))],
            kept_frames=[None, 12],
        )
    )
    end_t = truncated[-1][0] + truncated[-1][1]
    assert end_t == Fraction(1, 2)
    assert max(frames[1].index for _, _, frames in truncated) == 11
    assert max(frames[0].index for _, _, frames in truncated) == 14


def test_prepare_compare_playback_builds_combined_webm_with_trimmed_audio_and_manifest(tmp_path):
    # Slot 0: 30 fps full (2s, 60 frames) with distinct red-ish frames and audio.
    # Slot 1: 24 fps unfinished (24 kept frames = 1.0s) with 1.5s audio overhang, blue-ish frames.
    # Slot 2: 60 fps full (2s, 120 frames) silent.
    s0 = _make_solid_clip(
        tmp_path / "s0.webm",
        width=64,
        height=36,
        fps=30,
        frames=60,
        colors=[(min(255, i * 4), 20, 20) for i in range(60)],
        audio_seconds=2.0,
    )
    s1 = _make_solid_clip(
        tmp_path / "s1.webm",
        width=64,
        height=36,
        fps=24,
        frames=24,
        colors=[(20, 20, min(255, i * 10)) for i in range(24)],
        audio_seconds=1.5,
    )
    s2 = _make_solid_clip(
        tmp_path / "s2.webm",
        width=64,
        height=36,
        fps=60,
        frames=120,
        colors=[(20, min(255, i * 2), 20) for i in range(120)],
        audio_seconds=0.0,
    )
    dest = tmp_path / "combined.webm"
    audio0 = tmp_path / "audio-0.webm"
    audio1 = tmp_path / "audio-1.webm"
    audio2 = tmp_path / "audio-2.webm"

    manifest = prepare_compare_playback(
        [
            CompareSource(s0, audio_path=audio0),
            CompareSource(s1, kept_frames=24, audio_path=audio1),
            CompareSource(s2, audio_path=audio2),
        ],
        dest,
    )

    assert dest.is_file() and dest.stat().st_size > 0
    assert audio0.is_file()
    assert audio1.is_file()
    assert not audio2.exists()
    assert manifest["duration"] == pytest.approx(1.0, abs=0.02)
    assert len(manifest["slots"]) == 3
    assert manifest["slots"][0]["has_audio"] is True
    assert manifest["slots"][1]["has_audio"] is True
    assert manifest["slots"][2]["has_audio"] is False

    # Trimmed audio for slot 0 and slot 1 must not exceed the 1.0 s unfinished limit.
    for audio_file in (audio0, audio1):
        with av.open(str(audio_file)) as container:
            samples = list(container.decode(audio=0))
            audio_dur = sum(f.samples / f.sample_rate for f in samples)
            assert abs(audio_dur - 1.0) < 0.08

    # Decoded combined video stops at ~1.0s and each tile shows the expected frame at ~0.5s.
    decoded = list(decode_timed_frames(str(dest)))
    assert decoded[-1].timestamp + decoded[-1].duration <= Fraction(105, 100)
    mid = min(decoded, key=lambda f: abs(float(f.timestamp) - 0.5))
    for slot_idx, expected_channel in [(0, (60, 20, 20)), (1, (20, 20, 120)), (2, (20, 60, 20))]:
        r = manifest["slots"][slot_idx]
        patch = mid.rgb[r["y"] + 4 : r["y"] + r["height"] - 4, r["x"] + 4 : r["x"] + r["width"] - 4]
        mean = patch.mean(axis=(0, 1))
        assert np.allclose(mean, expected_channel, atol=20), (slot_idx, mean, expected_channel)


def test_compare_playback_helper_runs_without_torch_and_supports_cancellation(tmp_path):
    s0 = _make_solid_clip(tmp_path / "a.webm", width=32, height=32, fps=15, frames=30)
    s1 = _make_solid_clip(tmp_path / "b.webm", width=32, height=32, fps=15, frames=30)
    dest = tmp_path / "out.webm"
    manifest_path = tmp_path / "manifest.json"
    req_path = tmp_path / "req.json"
    req_path.write_text(
        json.dumps(
            {
                "sources": [{"path": str(s0)}, {"path": str(s1)}],
                "output_video": str(dest),
                "output_manifest": str(manifest_path),
            }
        )
    )
    root = Path(__file__).resolve().parents[1]
    launcher = (
        "import runpy,sys; sys.modules['torch']=None; "
        "sys.argv=['localsr.worker']+sys.argv[1:]; "
        "runpy.run_module('localsr.worker',run_name='__main__')"
    )
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    res = subprocess.run(
        [sys.executable, "-c", launcher, "--video-compare-preview", str(req_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert res.returncode == 0, res.stderr
    assert dest.is_file() and manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["slots"]) == 2

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        prepare_compare_playback(
            [CompareSource(s0), CompareSource(s1)], tmp_path / "c.webm", cancel_event=cancel
        )
