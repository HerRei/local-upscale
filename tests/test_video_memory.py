"""Memory policy and failure evidence without allocating GPU checkpoints."""

import time

import pytest

from localsr.core.video_memory import VideoMemoryMonitor, seed_memory_plan


@pytest.mark.parametrize(
    "device,low,frames,tile,blocks,tensors",
    [
        ("cuda:0", True, 5, 128, 32, True),  # CUDA and ROCm use this same API.
        ("cuda:0", False, 9, 512, 0, False),
        ("mps", True, 5, 128, 0, False),
        ("mps:0", False, 5, 128, 0, False),
        ("cpu", True, 5, 128, 0, False),
    ],
)
def test_platform_memory_plan(device, low, frames, tile, blocks, tensors):
    plan = seed_memory_plan(device, low)
    assert (plan.clip_frames, plan.vae_tile_size, plan.blocks_to_swap, plan.offload_tensors) == (
        frames,
        tile,
        blocks,
        tensors,
    )


def test_oom_freezes_pre_cleanup_evidence_and_emits_independent_packets():
    packets = []
    measurements = dict(
        gpu_sample_available=True,
        device_total_memory=16 * 2**30,
        device_free_memory=2**30,
        device_allocated_memory=12 * 2**30,
        device_reserved_memory=14 * 2**30,
        device_peak_memory=13 * 2**30,
    )
    monitor = VideoMemoryMonitor(
        "test", "cuda:0", False, packets.append, sampler=lambda _: measurements
    )
    monitor.configure(2160, 3840, 9)
    monitor.phase("decoding")
    monitor.sample()
    monitor.capture_oom()
    measurements.update(
        device_allocated_memory=0,
        device_free_memory=15 * 2**30,
        device_reserved_memory=0,
        device_peak_memory=0,
    )
    monitor.sample()
    message = monitor.oom_message()
    assert "decoding: 2160 × 3840" in message
    assert "13.00 GiB allocated" in message
    assert "1.00 GiB free of 16.00 GiB" in message
    assert "Enable Reduce GPU memory" in message
    assert packets[-1].oom and not packets[0].oom
    assert packets[-1].device_allocated_memory == 12 * 2**30


def test_sampler_failure_is_unavailable_and_monitor_stops_with_job():
    packets = []

    def unavailable(_):
        raise RuntimeError("device disappeared")

    with VideoMemoryMonitor(
        "test", "cpu", True, packets.append, sampler=unavailable, interval=0.01
    ) as monitor:
        assert not packets[-1].gpu_sample_available
    count = len(packets)
    time.sleep(0.03)
    assert len(packets) == count
    assert not monitor.thread.is_alive()


def test_peak_survives_a_later_lower_allocation():
    packets = []
    sample = {"device_allocated_memory": 100}
    monitor = VideoMemoryMonitor("test", "cpu", True, packets.append, sampler=lambda _: sample)
    monitor.sample()
    sample["device_allocated_memory"] = 20
    monitor.sample()
    assert packets[-1].device_peak_memory == 100
    assert packets[0].device_allocated_memory == 100
