#!/usr/bin/env python3
"""Generate small H.264/AAC MP4 fixtures with burned-in frame counters for e2e sync tests."""

from __future__ import annotations

import math
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent / "video-fixtures"


def make_video(
    path: Path,
    *,
    fps: int,
    frames: int,
    audio_seconds: float,
    bg: tuple[int, int, int],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w", options={"movflags": "+faststart"})
    vstream = container.add_stream("libx264", rate=fps)
    vstream.width = 128
    vstream.height = 72
    vstream.pix_fmt = "yuv420p"
    vstream.options = {"crf": "24", "preset": "fast", "g": str(max(1, fps // 2))}

    astream = None
    if audio_seconds > 0:
        astream = container.add_stream("aac", rate=44100)
        astream.layout = "mono"

    for i in range(frames):
        img = Image.new("RGB", (128, 72), bg)
        draw = ImageDraw.Draw(img)
        draw.text((8, 10), f"F{i:03d} @{fps}", fill=(240, 240, 240))
        # 8-bit luma barcode across the bottom (y=48..63, 16px wide per bit, MSB left).
        for bit in range(8):
            val = 235 if ((i >> (7 - bit)) & 1) else 20
            x0 = bit * 16
            draw.rectangle([x0, 48, x0 + 15, 63], fill=(val, val, val))
        frame = av.VideoFrame.from_ndarray(np.array(img), format="rgb24")
        for packet in vstream.encode(frame):
            container.mux(packet)
    for packet in vstream.encode():
        container.mux(packet)

    if astream is not None:
        sr = 44100
        total_samples = int(round(audio_seconds * sr))
        chunk = 1024
        pos = 0
        while pos < total_samples:
            n = min(chunk, total_samples - pos)
            t = (np.arange(pos, pos + n) / sr).astype(np.float32)
            samples = (0.05 * np.sin(2 * math.pi * 440.0 * t)).reshape(1, -1)
            aframe = av.AudioFrame.from_ndarray(samples, format="fltp", layout="mono")
            aframe.sample_rate = sr
            aframe.pts = pos
            for packet in astream.encode(aframe):
                container.mux(packet)
            pos += n
        for packet in astream.encode():
            container.mux(packet)

    container.close()


def main() -> None:
    specs = [
        ("orig_30.mp4", 30, 90, 3.0, (24, 38, 60)),
        ("res_30.mp4", 30, 90, 3.0, (48, 28, 52)),
        ("res_24.mp4", 24, 72, 3.0, (24, 54, 40)),
        ("res_60.mp4", 60, 180, 3.0, (56, 40, 24)),
        ("part_30_1s.mp4", 30, 30, 1.0, (48, 28, 52)),
        ("part_30_overhang.mp4", 30, 30, 1.5, (52, 24, 32)),
        ("part_24_1s.mp4", 24, 24, 1.0, (24, 54, 40)),
    ]
    for name, fps, frames, audio_seconds, bg in specs:
        out = OUT_DIR / name
        make_video(out, fps=fps, frames=frames, audio_seconds=audio_seconds, bg=bg)
        with av.open(str(out)) as c:
            vs = c.streams.video[0]
            print(
                f"{name}: {out.stat().st_size} B, "
                f"dur={c.duration / 1_000_000:.3f}s, "
                f"frames={vs.frames}, fps={float(vs.average_rate):.2f}"
            )


if __name__ == "__main__":
    main()
