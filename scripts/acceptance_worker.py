"""Real-model acceptance against a packaged worker, with disposable fixtures.

Run explicitly on the target OS/hardware. This is not part of the offline suite.
Only the small, redistributable Quick model is downloaded when absent.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import av
import numpy as np
from PIL import Image

from localsr.core.model_catalog import CATALOG_BY_ID, ModelStore, download_model
from localsr.protocol.messages import JobRequest, VideoJobRequest


class Worker:
    def __init__(self, executable: Path, root: Path, *, source_root: Path | None = None):
        env = dict(os.environ, LOCALSR_WORK_DIR=str(root / "scratch"), PYTHONUTF8="1")
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        command = [str(executable.resolve())]
        if source_root is not None:
            env["PYTHONPATH"] = str(source_root.resolve() / "src")
            command.extend(["-m", "localsr.worker.server"])
        self.errors = (root / "worker.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.errors,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.messages = queue.Queue()
        self.seen = []
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue
        self.messages.put({"type": "process_exit", "data": {}})

    def send(self, kind, data):
        self.process.stdin.write(json.dumps({"type": kind, "data": data}) + "\n")
        self.process.stdin.flush()

    def until(self, kind, *, cancel_job=None, timeout=180):
        started = time.monotonic()
        self.seen = []
        while True:
            event = self.messages.get(timeout=max(0.01, timeout - time.monotonic() + started))
            self.seen.append(event)
            if cancel_job and event["type"] == "job_started":
                self.send("cancel_request", {"job_id": cancel_job})
            if event["type"] == kind:
                return event["data"]
            if event["type"] in {"process_exit", "job_failed", "protocol_error"}:
                raise AssertionError(event)
            if time.monotonic() - started >= timeout:
                raise TimeoutError(kind)

    def close(self):
        if self.process.poll() is None:
            try:
                self.send("shutdown_request", {})
            except (BrokenPipeError, OSError):
                pass
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    # A Windows venv launcher can own a second interpreter.
                    # Stop only this worker's tree, including that child.
                    subprocess.run(
                        ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                        check=True,
                        capture_output=True,
                        timeout=15,
                    )
                else:
                    self.process.kill()
                self.process.wait(timeout=15)
        self.reader.join(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()
        self.errors.close()


def make_video(path):
    with av.open(str(path), "w") as container:
        video = container.add_stream("libx264", rate=12)
        video.width, video.height, video.pix_fmt = 96, 64, "yuv420p"
        audio = container.add_stream("aac", rate=48000)
        for index in range(24):
            rgb = np.zeros((64, 96, 3), dtype=np.uint8)
            rgb[:, :, 0] = 30 + index * 7
            rgb[:, :, 1] = np.arange(96)[None, :] * 2
            rgb[16:48, index : index + 20, 2] = 220
            for packet in video.encode(av.VideoFrame.from_ndarray(rgb, format="rgb24")):
                container.mux(packet)
            samples = np.sin(np.arange(4000) * (2 * np.pi * 440 / 48000)) * 8000
            frame = av.AudioFrame.from_ndarray(
                samples.astype(np.int16)[None, :], format="s16", layout="mono"
            )
            frame.sample_rate = 48000
            for packet in audio.encode(frame):
                container.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode():
                container.mux(packet)


def run(worker, root, model_path, device):
    results = {"device": device, "checks": []}

    def passed(name, **details):
        results["checks"].append({"name": name, "passed": True, **details})
        print(json.dumps(results["checks"][-1]), flush=True)

    worker.until("worker_ready")
    broken = root / "broken.mov"
    broken.write_bytes(b"not a movie")
    worker.send("media_probe_request", {"media_path": str(broken)})
    worker.until("media_probe_failed")
    passed("corrupt-media-rejected")

    image = root / "portrait ü transparent.png"
    rgba = np.zeros((97, 129, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.arange(129)[None, :, None] + 50
    rgba[:, :, 3] = np.linspace(0, 255, 129, dtype=np.uint8)[None, :]
    Image.fromarray(rgba).save(image)
    worker.send("media_probe_request", {"media_path": str(image)})
    info = worker.until("media_info")
    assert (info["width"], info["height"]) == (129, 97), info
    passed("valid-media-after-corrupt-file")
    image_output = root / "enhanced ü.png"
    image_job = JobRequest(
        job_id="acceptance-image",
        image_path=str(image),
        model_path=str(model_path),
        output_path=str(image_output),
        output_format="PNG",
        device=device,
        tile_size=64,
        halo=16,
        precision="fp32",
        jpeg_quality=95,
        preserve_metadata=True,
        safe_memory=True,
        output_scale=2,
    )
    worker.send("job_request", json.loads(image_job.to_json())["data"])
    worker.until("job_completed")
    with Image.open(image_output) as result:
        assert result.size == (258, 194) and result.mode == "RGBA", (result.size, result.mode)
        alpha = np.asarray(result)[:, :, 3]
        assert alpha.min() == 0 and alpha.max() == 255
    passed("odd-size-transparent-image-unicode-path", dimensions=[258, 194])

    video = root / "moving colours ü.mov"
    make_video(video)
    output = root / "trimmed ü.mp4"
    job = VideoJobRequest(
        job_id="acceptance-video",
        video_path=str(video),
        model_path=str(model_path),
        output_video_path=str(output),
        container="mp4",
        crf=18,
        fps=None,
        device=device,
        tile_size=64,
        halo=16,
        precision="fp32",
        safe_memory=True,
        output_scale=2,
        start_frame=3,
        end_frame=18,
    )
    data = json.loads(job.to_json())["data"]
    worker.send("video_job_request", data)
    worker.until("video_job_completed")
    events = worker.seen
    with av.open(str(output)) as container:
        frames = list(container.decode(video=0))
        assert len(frames) == 16, len(frames)
        assert (frames[0].width, frames[0].height) == (192, 128)
        assert all(b.time > a.time for a, b in zip(frames, frames[1:], strict=False))
        assert abs(frames[0].time) < 0.01
        means = [frame.to_ndarray(format="rgb24")[:, :, 0].mean() for frame in frames]
        assert all(b > a for a, b in zip(means, means[1:], strict=False)), means
        assert len(container.streams.audio) == 1
    with av.open(str(output)) as container:
        audio = list(container.decode(audio=0))
        duration = sum(frame.samples / frame.sample_rate for frame in audio)
        assert abs(duration - 16 / 12) < 0.1, duration
    assert any(event["type"] == "tile_update" for event in events)
    assert any(event["type"] == "live_preview_frame" for event in events)
    assert any((event["data"].get("estimated_remaining_seconds") or 0) > 0 for event in events)
    passed("trimmed-video-audio-timing-real-tiles-eta", frames=16, audio_seconds=duration)

    cancelled = root / "cancelled.mp4"
    data.update(job_id="acceptance-cancel", output_video_path=str(cancelled))
    worker.send("video_job_request", data)
    worker.until("job_cancelled", cancel_job=data["job_id"])
    assert not cancelled.exists()
    assert not list(root.glob("*.tmp*"))
    passed("cancel-cleans-output")
    data.update(job_id="acceptance-retry", output_video_path=str(root / "retry.mp4"), end_frame=4)
    worker.send("video_job_request", data)
    worker.until("video_job_completed")
    with av.open(data["output_video_path"]) as container:
        assert len(list(container.decode(video=0))) == 2
    passed("successful-video-after-cancellation")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worker", type=Path)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--source-root",
        type=Path,
        help="Test source through the supplied Python executable; does not count as packaged acceptance",
    )
    args = parser.parse_args()
    report = {"passed": False, "packaged_worker": args.source_root is None}
    with tempfile.TemporaryDirectory(prefix="localsr-acceptance-") as directory:
        root = Path(directory)
        model = CATALOG_BY_ID["span_photo_x4"]
        store = ModelStore()
        model_path = store.path_for(model)
        if not store.is_installed(model):
            model_path = download_model(model, root / model.filename)
        worker = Worker(args.worker, root, source_root=args.source_root)
        try:
            report.update(run(worker, root, model_path, args.device), passed=True)
        except Exception as error:
            report["error"] = repr(error)
            raise
        finally:
            worker.close()
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
