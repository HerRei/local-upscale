#!/usr/bin/env python3
"""Bounded source-worker playback conversion acceptance; no model or package build."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import av
import psutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("ffmpeg is needed to generate the synthetic acceptance recording")
    root = Path(__file__).resolve().parents[1]
    options.report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="legacy-acceptance-", dir=options.report.parent
    ) as owned:
        directory = Path(owned)
        source, output = directory / "holiday.avi", directory / "playback.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=720x480:rate=25",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000",
                "-t",
                "60",
                "-c:v",
                "mpeg4",
                "-q:v",
                "5",
                "-threads",
                "2",
                "-c:a",
                "pcm_s16le",
                str(source),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        started, peak = time.monotonic(), 0
        with (
            (directory / "progress.jsonl").open("w") as progress,
            (directory / "stderr.txt").open("w") as errors,
        ):
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "localsr.worker",
                    "--video-playback-preview",
                    str(source),
                    str(output),
                ],
                env={**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
                stdout=progress,
                stderr=errors,
            )
            monitored = psutil.Process(process.pid)
            try:
                while process.poll() is None:
                    try:
                        peak = max(
                            peak,
                            sum(
                                p.memory_info().rss
                                for p in [monitored, *monitored.children(recursive=True)]
                            ),
                        )
                    except psutil.NoSuchProcess:
                        pass
                    if time.monotonic() - started > 120:
                        raise TimeoutError(
                            "60-second playback conversion exceeded the 120-second acceptance budget"
                        )
                    time.sleep(0.1)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
        assert process.returncode == 0, (directory / "stderr.txt").read_text()
        elapsed = time.monotonic() - started
        stamps = []
        with av.open(str(output)) as container:
            assert container.streams.video[0].codec_context.name == "h264"
            for frame in container.decode(video=0):
                stamps.append(float(frame.pts * frame.time_base))
        with av.open(str(output)) as container:
            assert container.streams.audio[0].codec_context.name == "aac"
            audio_seconds = sum(
                frame.samples / frame.sample_rate for frame in container.decode(audio=0)
            )
        assert len(stamps) == 1500
        assert all(abs(stamp - index / 25) < 0.0001 for index, stamp in enumerate(stamps))
        assert abs(audio_seconds - 60) < 0.03
        assert peak < 768 * 1024 * 1024
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        events = [
            json.loads(line) for line in (directory / "progress.jsonl").read_text().splitlines()
        ]
        assert events[-1]["stage"] == "Playback copy ready"
        report = {
            "result": "passed",
            "scope": "macOS source-worker codec helper, no AI inference or installed GUI",
            "input": "720x480 MPEG-4 Part 2 / PCM in AVI",
            "output": "720x480 H.264 / AAC in MP4",
            "frames": len(stamps),
            "duration_seconds": 60,
            "audio_seconds": audio_seconds,
            "elapsed_seconds": elapsed,
            "peak_worker_tree_rss_bytes": peak,
            "source_unchanged": True,
            "progress_events": len(events),
            "av_version": av.__version__,
            "synthetic_media_retained": False,
        }
    options.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
