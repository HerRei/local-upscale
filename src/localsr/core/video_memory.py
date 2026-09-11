"""Measured memory for a temporal job; never an estimated model fit guarantee."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

from localsr.protocol.messages import VideoMemoryStatus


@dataclass(frozen=True)
class SeedMemoryPlan:
    low_memory: bool
    clip_frames: int
    vae_tile_size: int
    blocks_to_swap: int
    offload_tensors: bool


def seed_memory_plan(device: str, low_memory: bool) -> SeedMemoryPlan:
    separate_gpu = device.startswith("cuda")  # Also PyTorch's ROCm device API.
    shared_gpu = device.startswith("mps")
    return SeedMemoryPlan(
        low_memory=low_memory,
        clip_frames=5 if low_memory or shared_gpu else 9,
        vae_tile_size=128 if low_memory or shared_gpu else 512,
        blocks_to_swap=32 if low_memory and separate_gpu else 0,
        offload_tensors=low_memory and separate_gpu,
    )


def read_memory(device: str) -> dict:
    import psutil
    import torch

    ram = psutil.virtual_memory()
    values = {
        "gpu_sample_available": False,
        "system_ram_available": int(ram.available),
        "process_ram": psutil.Process().memory_info().rss,
    }
    try:
        if device.startswith("cuda") and torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(device)
            values.update(
                device_total_memory=int(total),
                device_free_memory=int(free),
                device_allocated_memory=torch.cuda.memory_allocated(device),
                device_reserved_memory=torch.cuda.memory_reserved(device),
                device_peak_memory=torch.cuda.max_memory_allocated(device),
                gpu_sample_available=True,
            )
        elif device.startswith("mps") and torch.backends.mps.is_available():
            values.update(
                device_allocated_memory=torch.mps.current_allocated_memory(),
                gpu_sample_available=True,
                shared_memory=True,
            )
    except (RuntimeError, AttributeError):
        pass  # A missing measurement stays explicitly unavailable.
    return values


class VideoMemoryMonitor:
    def __init__(self, job_id, device, low_memory, emit, *, sampler=read_memory, interval=1.0):
        self.emit = emit
        self.sampler = sampler
        self.interval = interval
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.started = time.monotonic()
        plan = seed_memory_plan(device, low_memory)
        self.latest = VideoMemoryStatus(
            job_id=job_id,
            device=device,
            low_memory=low_memory,
            stage="loading_model",
            clip_frames=plan.clip_frames,
            vae_tile_size=plan.vae_tile_size,
            blocks_to_swap=plan.blocks_to_swap,
            offload_tensors=plan.offload_tensors,
        )
        self.failure = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        import torch

        try:
            if self.latest.device.startswith("cuda") and torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats(self.latest.device)
        except RuntimeError:
            pass
        self.sample()
        self.thread.start()
        return self

    def configure(self, width, height, clip_frames):
        with self.lock:
            self.latest.output_width, self.latest.output_height = width, height
            self.latest.clip_frames = clip_frames

    def phase(self, stage):
        with self.lock:
            self.latest.stage = stage

    def sample(self):
        try:
            values = self.sampler(self.latest.device)
        except Exception:  # Telemetry must not fail an otherwise valid job.
            values = {"gpu_sample_available": False}
        with self.lock:
            if self.stop_event.is_set() or self.failure is not None:
                return
            previous_peak = self.latest.device_peak_memory
            for key, value in values.items():
                setattr(self.latest, key, value)
            self.latest.device_peak_memory = max(
                previous_peak, self.latest.device_peak_memory, self.latest.device_allocated_memory
            )
            self.latest.elapsed_seconds = time.monotonic() - self.started
            self.emit(replace(self.latest))

    def capture_oom(self):
        """Freeze the last allocation evidence before the model is released."""
        self.sample()
        with self.lock:
            if self.failure is None:
                self.latest.oom = True
                self.failure = replace(self.latest)
                self.emit(replace(self.failure))

    def _run(self):
        while not self.stop_event.wait(self.interval):
            self.sample()

    def oom_message(self):
        self.capture_oom()
        with self.lock:
            info = self.failure
            stage = info.stage.replace("_", " ")
            memory = (
                (
                    f"GPU peak {info.device_peak_memory / 2**30:.2f} GiB allocated; "
                    f"{info.device_reserved_memory / 2**30:.2f} GiB reserved; "
                    f"{info.device_free_memory / 2**30:.2f} GiB free of "
                    f"{info.device_total_memory / 2**30:.2f} GiB. "
                )
                if info.gpu_sample_available and info.device_total_memory
                else ""
            )
            recovery = (
                "Enable Reduce GPU memory (CPU block/tensor offload and smaller VAE tiles). "
                if not info.low_memory
                else "Memory saving is already enabled. "
            )
            return (
                f"SeedVR2 ran out of memory during {stage}: "
                f"{info.output_width} × {info.output_height}, up to {info.clip_frames} frames/clip. "
                f"{memory}{recovery}Choose a smaller Output resolution or use tiled HAT-S. "
                "GPU capacity alone does not guarantee that this output fits."
            )

    def __exit__(self, *_):
        self.sample()
        self.stop_event.set()
        self.thread.join(timeout=2)
