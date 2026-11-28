from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from localsr.core import image_edit
from localsr.core.edit_memory import (
    GIB,
    EditMemory,
    EditPlan,
    check_edit_pressure,
    plan_edit,
    reserve_for,
)
from localsr.protocol.messages import EditJobRequest
from localsr.worker import edit_guard


def model(quant="Q2_K", family="qwen-image-edit-2511"):
    return next(
        m for m in image_edit.edit_catalog() if m["family"] == family and m["quantization"] == quant
    )


MAC_TIERS = (16, 24, 32, 36, 48, 64)
GPU_TIERS = (8, 12, 16, 20, 24, 32)


@pytest.mark.parametrize("selected", image_edit.edit_catalog(), ids=lambda m: m["model_id"])
def test_mac_profiles_admit_at_their_tier_and_refuse_below_it(selected):
    capacity = selected["min_unified_memory_gb"]
    memory = EditMemory(capacity * GIB, int(capacity * 0.70 * GIB), unified=True)
    plan = plan_edit(selected, memory)
    assert plan.required_ram + plan.reserve_ram <= memory.free_ram
    assert plan.reserve_ram >= 2 * GIB
    assert plan.gpu_budget <= memory.total_ram * 0.60
    assert plan.process_limit >= plan.required_ram
    assert plan.max_dimension == 512
    smaller = [tier for tier in MAC_TIERS if tier < capacity]
    if smaller:
        with pytest.raises(MemoryError, match="smaller"):
            plan_edit(selected, EditMemory(smaller[-1] * GIB, smaller[-1] * GIB, unified=True))


@pytest.mark.parametrize("capacity,limit", [(24, 512), (36, 768), (48, 1024), (64, 1024)])
def test_mac_edit_size_grows_with_unified_memory(capacity, limit):
    memory = EditMemory(capacity * GIB, int(capacity * 0.85 * GIB), unified=True)
    assert plan_edit(model("Q2_K"), memory, 1024).max_dimension == limit


def test_mac_tiers_are_sensible_for_the_bigger_models():
    tiers = {m["model_id"]: m["min_unified_memory_gb"] for m in image_edit.edit_catalog()}
    assert tiers["qwen_edit_2511_q8_0"] <= 48, "a 48 GB Mac holds the 26 GiB Q8 bundle"
    assert tiers["qwen_edit_2511_q4_k_m"] <= 32, "common 32 GB Macs get a Q4 editor"
    assert tiers["flux2_klein_4b_q8_0"] == 16
    assert tiers["mage_flow_edit_turbo_q6_k"] == 16
    assert tiers["qwen_edit_2511_q2_k"] > 16, "the 20B editor stays off 16 GB Macs"


@pytest.mark.parametrize("selected", image_edit.edit_catalog(), ids=lambda m: m["model_id"])
def test_gpu_profiles_allow_driver_overhead_and_keep_vram_reserve(selected):
    capacity = selected["min_vram_gb"]
    memory = EditMemory(
        64 * GIB, 60 * GIB, gpu_total=capacity * GIB - 32 * 1024**2, gpu_free=(capacity - 1) * GIB
    )
    plan = plan_edit(selected, memory, 1024)
    gpu_reserve = max(1 * GIB, int(memory.gpu_total * 0.08))
    assert 4 * GIB <= plan.gpu_budget <= memory.gpu_free - gpu_reserve
    assert plan.max_dimension in (512, 768, 1024)
    smaller = [tier for tier in GPU_TIERS if tier < capacity]
    if smaller:
        with pytest.raises(MemoryError, match="smaller"):
            plan_edit(
                selected,
                EditMemory(
                    64 * GIB, 60 * GIB, gpu_total=smaller[-1] * GIB, gpu_free=smaller[-1] * GIB
                ),
            )


def test_advertised_total_ram_does_not_authorize_loading_on_a_busy_mac(monkeypatch):
    memory = EditMemory(26 * GIB, 9 * GIB, unified=True)
    monkeypatch.setattr(
        image_edit, "check_runtime", lambda _: pytest.fail("Runtime must not start")
    )
    request = {"model_id": model()["model_id"], "prompt": "Change the background.", "device": "mps"}
    with pytest.raises(MemoryError, match="currently available"):
        image_edit.run_edit_job(
            request, threading.Event(), lambda _: None, sampler=lambda _: memory
        )


# Measured on the 16 GB M1 Pro (2026-10-08) with other apps open: psutil reported
# 5.5 GiB available while macOS reported 71% free; the FLUX.2 klein 4B Q4_0 edit at
# 512 px ran in 128 s with a 2.6 GiB footprint, no pressure and no swap, and the
# free level bottomed out at 40% (psutil: 3.2 GiB).
BUSY_16GB_MAC = EditMemory(
    16 * GIB,
    int(5.5 * GIB),
    int(0.34 * GIB),
    unified=True,
    pressure="low",
    obtainable_ram=16 * GIB * 71 // 100,
)


def test_busy_16gb_mac_admits_flux_klein_when_macos_reports_it_free():
    plan = plan_edit(model("Q4_0", "flux2-klein-4b"), BUSY_16GB_MAC)
    assert plan.reserve_ram == 2 * GIB
    assert plan.gpu_budget == plan.required_ram
    # Without the macOS free level, psutil's figure alone stays conservative.
    with pytest.raises(MemoryError, match="currently available"):
        plan_edit(model("Q4_0", "flux2-klein-4b"), replace(BUSY_16GB_MAC, obtainable_ram=0))


def test_measured_flux_klein_run_does_not_trip_the_supervisor():
    plan = plan_edit(model("Q4_0", "flux2-klein-4b"), BUSY_16GB_MAC)
    lowest = replace(
        BUSY_16GB_MAC, available_ram=int(3.16 * GIB), obtainable_ram=16 * GIB * 40 // 100
    )
    check_edit_pressure(lowest, plan, BUSY_16GB_MAC.swap_used, int(2.57 * GIB))


def test_host_memory_reads_the_macos_free_level(monkeypatch):
    from localsr.core import edit_memory

    values = {"kern.memorystatus_vm_pressure_level": 2, "kern.memorystatus_level": 71}
    monkeypatch.setattr(edit_memory, "_sysctl_int", values.get)
    memory = edit_memory.read_host_memory(unified=True)
    assert memory.pressure == "warning"
    assert memory.obtainable_ram == memory.total_ram * 71 // 100 == memory.free_ram

    monkeypatch.setattr(edit_memory, "_sysctl_int", lambda _: None)
    memory = edit_memory.read_host_memory(unified=True)
    assert memory.obtainable_ram == 0 and memory.free_ram == memory.available_ram


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sysctl")
def test_sysctl_reads_the_real_free_level():
    from localsr.core import edit_memory

    assert 0 <= edit_memory._sysctl_int("kern.memorystatus_level") <= 100
    assert edit_memory._sysctl_int("kern.memorystatus_vm_pressure_level") in {1, 2, 4}


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS footprint")
def test_supervisor_measures_the_macos_footprint():
    import psutil

    process = psutil.Process()
    actual = edit_guard.process_memory(process)
    report = subprocess.check_output(
        ["/usr/bin/footprint", "--noCategories", "-f", "bytes", str(process.pid)], text=True
    )
    match = re.search(r"^\s*phys_footprint:\s*(\d+) B", report, re.MULTILINE)
    assert match is not None
    reference = int(match.group(1))
    assert actual > 0 and abs(actual - reference) <= max(16 * 1024**2, reference // 10)


@pytest.mark.parametrize(
    "memory",
    [
        EditMemory(24 * GIB, 23 * GIB, unified=True, pressure="critical"),
        EditMemory(64 * GIB, 60 * GIB, gpu_total=16 * GIB, gpu_free=3 * GIB),
    ],
)
def test_pressure_or_busy_gpu_refuses_loading(memory):
    with pytest.raises(MemoryError):
        plan_edit(model(), memory)


MAC_PLAN = EditPlan(16 * GIB, 2 * GIB, 10 * GIB, 512, 16 * GIB)
MAC_RUNNING = EditMemory(16 * GIB, 4 * GIB, 0, unified=True, pressure="low", obtainable_ram=6 * GIB)


@pytest.mark.parametrize(
    "change,used,warning_seconds,critical_samples,reason",
    [
        ({"pressure": "critical"}, 0, 1.0, 2, "critical memory pressure"),
        ({"swap_used": int(8.5 * GIB)}, 0, 0.0, 0, "swapped 8.5 GiB to disk, more than half"),
        (
            {"swap_used": 3 * GIB, "disk_free": 6_000_000_000, "disk_reserve": 8_000_000_000},
            0,
            0.0,
            0,
            "only 6.0 GB of disk space was free",
        ),
        ({"pressure": "warning", "swap_used": int(2.5 * GIB)}, 0, 90.0, 0, "stayed high for 90 s"),
        ({}, 17 * GIB, 0.0, 0, "more than its 16.0 GiB limit"),
    ],
)
def test_mac_supervisor_stops_only_when_the_system_is_in_trouble(
    change, used, warning_seconds, critical_samples, reason
):
    with pytest.raises(MemoryError, match=reason):
        check_edit_pressure(
            replace(MAC_RUNNING, **change),
            MAC_PLAN,
            0,
            used,
            warning_seconds=warning_seconds,
            critical_samples=critical_samples,
        )


@pytest.mark.parametrize(
    "change,warning_seconds,critical_samples",
    [
        # Compression and some swap are how a unified-memory Mac makes room.
        ({"pressure": "warning"}, 120.0, 0),
        ({"swap_used": GIB}, 0.0, 0),
        # The owner's 16 GB Mac: a model load pushed 2.2 GiB of other apps to swap.
        ({"pressure": "warning", "swap_used": int(2.2 * GIB)}, 40.0, 0),
        ({"pressure": "warning", "swap_used": 3 * GIB}, 89.0, 0),
        ({"swap_used": 3 * GIB, "disk_free": 9_000_000_000, "disk_reserve": 8_000_000_000}, 0.0, 0),
        ({"pressure": "critical"}, 0.0, 1),
        ({"obtainable_ram": GIB // 2, "available_ram": GIB // 4}, 0.0, 0),
    ],
)
def test_mac_supervisor_lets_macos_compress_and_swap_a_little(
    change, warning_seconds, critical_samples
):
    check_edit_pressure(
        replace(MAC_RUNNING, **change),
        MAC_PLAN,
        0,
        4 * GIB,
        warning_seconds=warning_seconds,
        critical_samples=critical_samples,
    )


def test_mac_swap_bound_is_half_of_memory_but_at_least_4_gib():
    big = EditMemory(64 * GIB, 8 * GIB, 0, unified=True, pressure="low", obtainable_ram=8 * GIB)
    plan = EditPlan(16 * GIB, 2 * GIB, 10 * GIB, 512, 16 * GIB)
    check_edit_pressure(replace(big, swap_used=31 * GIB), plan, 0, 0)
    with pytest.raises(MemoryError, match="swapped 33.0 GiB"):
        check_edit_pressure(replace(big, swap_used=33 * GIB), plan, 0, 0)
    small = EditMemory(4 * GIB, GIB, 0, unified=True, pressure="low", obtainable_ram=GIB)
    check_edit_pressure(replace(small, swap_used=3 * GIB), plan, 0, 0)
    with pytest.raises(MemoryError, match="swapped 5.0 GiB"):
        check_edit_pressure(replace(small, swap_used=5 * GIB), plan, 0, 0)


def test_sustained_warning_counts_swap_growth_inside_the_warning_period():
    warm = replace(MAC_RUNNING, pressure="warning", swap_used=3 * GIB)
    # Swap grew a lot before the warning began and only 0.5 GiB since: not thrashing.
    check_edit_pressure(warm, MAC_PLAN, 0, 0, warning_seconds=200.0, warning_swap=int(2.5 * GIB))


def test_host_memory_reads_system_disk_room(monkeypatch):
    from localsr.core import disk_space, edit_memory

    volume = disk_space.Volume(1, 5_000_000_000, 100_000_000_000, True)
    monkeypatch.setattr(disk_space, "volume_of", lambda folder: volume)
    memory = edit_memory.read_host_memory(unified=True)
    assert memory.disk_free == 5_000_000_000
    assert memory.disk_reserve == disk_space.reserve_for(volume)
    assert edit_memory.read_host_memory(unified=False).disk_free == 0


@pytest.mark.parametrize(
    "change",
    [{"available_ram": 2 * GIB}, {"pressure": "warning"}],
)
def test_gpu_host_supervisor_keeps_its_reserve(change):
    initial = EditMemory(64 * GIB, 60 * GIB, gpu_total=16 * GIB, gpu_free=12 * GIB)
    plan = EditPlan(16 * GIB, 4 * GIB, 10 * GIB, 512, 16 * GIB)
    with pytest.raises(MemoryError, match="protect system memory"):
        check_edit_pressure(replace(initial, **change), plan, 0, 0)


# Two stops on a Linux PC (Fedora 44, 8 GB zram at swappiness 60, 16 GB RX 9060 XT,
# Qwen Image Edit 2511 Q4_K_M at 512 px, sd-cli RSS 17.7 GiB, mostly mapped weights):
# "the system swapped 0.5 GiB" while 18 GiB and more was still available, after a cold
# page cache and beside an app using 9-10 GB. The same edit then finished in 27 s.
GPU_HOST = EditMemory(64 * GIB, 18 * GIB, 0, gpu_total=16 * GIB, gpu_free=12 * GIB)
GPU_PLAN = EditPlan(16 * GIB, reserve_for(64 * GIB), 10 * GIB, 512, 22 * GIB)


@pytest.mark.parametrize(
    "change",
    [
        {"swap_used": int(0.6 * GIB), "compressed_swap": int(0.6 * GIB)},
        {"swap_used": int(0.6 * GIB)},
        {"swap_used": 3 * GIB},
    ],
    ids=["zram", "disk", "disk-3-gib"],
)
def test_gpu_host_lets_the_system_swap_cold_pages_while_memory_is_available(change):
    check_edit_pressure(replace(GPU_HOST, **change), GPU_PLAN, 0, int(17.7 * GIB))


@pytest.mark.parametrize(
    "change,tight_swap",
    [
        # Within two reserves of the stop line, but the swap that grew is compressed RAM.
        ({"available_ram": 10 * GIB, "swap_used": 3 * GIB, "compressed_swap": 3 * GIB}, 0),
        # Only 0.4 GiB went to disk since memory became tight.
        ({"available_ram": 10 * GIB, "swap_used": 3 * GIB}, int(2.6 * GIB)),
        # Not tight: the swap that grew earlier does not count.
        ({"available_ram": 13 * GIB, "swap_used": 3 * GIB}, None),
    ],
)
def test_gpu_host_counts_only_disk_swap_while_memory_is_tight(change, tight_swap):
    check_edit_pressure(replace(GPU_HOST, **change), GPU_PLAN, 0, 0, tight_swap=tight_swap)


def test_gpu_host_stops_when_it_swaps_to_disk_with_little_memory_available():
    memory = replace(GPU_HOST, available_ram=10 * GIB, swap_used=int(1.5 * GIB))
    with pytest.raises(
        MemoryError,
        match=r"swapped 1\.0 GiB to disk while only 10\.0 GiB of memory was available",
    ):
        check_edit_pressure(memory, GPU_PLAN, 0, 0, tight_swap=GIB // 2)


@pytest.mark.parametrize(
    "stall,seconds,stops",
    [(35.0, 12.0, True), (35.0, 4.0, False), (15.0, 60.0, False), (None, 60.0, False)],
)
def test_gpu_host_stops_when_linux_reports_a_sustained_memory_stall(stall, seconds, stops):
    memory = replace(GPU_HOST, memory_stall=stall)
    if not stops:
        check_edit_pressure(memory, GPU_PLAN, 0, 0, stall_seconds=seconds)
        return
    with pytest.raises(MemoryError, match="waited for memory 35% of the time for 12 s"):
        check_edit_pressure(memory, GPU_PLAN, 0, 0, stall_seconds=seconds)


def test_windows_host_stops_on_low_available_memory_and_page_file_growth_only_when_tight():
    # psutil on Windows: available is free and standby RAM, swap is page file use; Windows
    # reports neither zram nor PSI.
    windows = EditMemory(32 * GIB, 20 * GIB, int(1.2 * GIB), gpu_total=16 * GIB, gpu_free=12 * GIB)
    plan = EditPlan(8 * GIB, reserve_for(32 * GIB), 10 * GIB, 512, 16 * GIB)
    with pytest.raises(MemoryError, match=r"only 3\.0 GiB of memory was left"):
        check_edit_pressure(replace(windows, available_ram=3 * GIB), plan, int(1.2 * GIB), 0)
    check_edit_pressure(replace(windows, swap_used=3 * GIB), plan, int(1.2 * GIB), 0)
    with pytest.raises(MemoryError, match=r"swapped 1\.0 GiB to disk while only 6\.0 GiB"):
        check_edit_pressure(
            replace(windows, available_ram=6 * GIB, swap_used=int(2.2 * GIB)),
            plan,
            int(1.2 * GIB),
            0,
            tight_swap=int(1.2 * GIB),
        )


PROC_SWAPS = (
    "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
    "/dev/zram0                              partition\t8388604\t\t614400\t\t100\n"
    "/var/swap\\040file                       file\t\t4194300\t\t2048\t\t-2\n"
)
PROC_PRESSURE = (
    "some avg10=3.10 avg60=1.00 avg300=0.20 total=123456\n"
    "full avg10=1.25 avg60=0.50 avg300=0.10 total=45678\n"
)


def test_linux_snapshot_reads_zram_swap_and_the_memory_stall(monkeypatch, tmp_path):
    from localsr.core import edit_memory

    swaps, pressure = tmp_path / "swaps", tmp_path / "memory"
    swaps.write_text(PROC_SWAPS)
    pressure.write_text(PROC_PRESSURE)
    monkeypatch.setattr(edit_memory, "PROC_SWAPS", swaps)
    monkeypatch.setattr(edit_memory, "PROC_MEMORY_PRESSURE", pressure)
    monkeypatch.setattr(edit_memory.sys, "platform", "linux")
    memory = edit_memory.read_host_memory(unified=False)
    assert memory.compressed_swap == 614400 * 1024
    assert memory.memory_stall == 1.25
    # Kernels without PSI (or booted with psi=0) have no pressure file.
    swaps.unlink()
    pressure.unlink()
    memory = edit_memory.read_host_memory(unified=False)
    assert memory.compressed_swap == 0 and memory.memory_stall is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc")
def test_linux_snapshot_reads_the_real_kernel_files():
    from localsr.core import edit_memory

    memory = edit_memory.read_host_memory(unified=False)
    assert 0 <= memory.compressed_swap
    assert memory.memory_stall is None or 0.0 <= memory.memory_stall <= 100.0


def test_mac_admission_accepts_warning_pressure_but_not_critical():
    plan_edit(model("Q4_0", "flux2-klein-4b"), replace(BUSY_16GB_MAC, pressure="warning"))
    with pytest.raises(MemoryError, match="pressure"):
        plan_edit(model("Q4_0", "flux2-klein-4b"), replace(BUSY_16GB_MAC, pressure="critical"))


def test_bundle_verification_is_streamed_and_rejects_corruption(tmp_path):
    import hashlib

    content = b"small fixture, not model weights"
    (tmp_path / "test.gguf").write_bytes(content)
    bundle = {
        "files": [
            {
                "role": "diffusion",
                "filename": "test.gguf",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ]
    }
    assert (
        image_edit.verify_bundle(bundle, tmp_path, threading.Event())["diffusion"].name
        == "test.gguf"
    )
    bundle["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum failed"):
        image_edit.verify_bundle(bundle, tmp_path, threading.Event())
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        image_edit.verify_bundle(bundle, tmp_path, event)


@pytest.fixture
def bundle_files(tmp_path, monkeypatch):
    """A one-file bundle that counts reads, with a clock far enough ahead for caching to apply."""
    import hashlib
    import time

    image_edit._VERIFIED_BUNDLE_FILES.clear()
    real_ns = time.time_ns
    monkeypatch.setattr(image_edit.time, "time_ns", lambda: real_ns() + 3600 * 10**9)
    reads = []
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        if self.suffix == ".gguf" and args[:1] == ("rb",):
            reads.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)

    def write(content: bytes, sha256: str | None = None):
        (tmp_path / "test.gguf").write_bytes(content)
        return {
            "files": [
                {
                    "role": "diffusion",
                    "filename": "test.gguf",
                    "size_bytes": len(content),
                    "sha256": sha256 or hashlib.sha256(content).hexdigest(),
                }
            ]
        }

    yield write, reads
    image_edit._VERIFIED_BUNDLE_FILES.clear()


def test_second_bundle_verification_does_not_reread_unchanged_files(bundle_files, tmp_path):
    write, reads = bundle_files
    bundle = write(b"model bytes")
    first, second = [], []
    image_edit.verify_bundle(bundle, tmp_path, threading.Event(), first.append)
    assert len(reads) == 1
    paths = image_edit.verify_bundle(bundle, tmp_path, threading.Event(), second.append)
    assert len(reads) == 1
    assert paths["diffusion"].name == "test.gguf"
    assert first[-1] == second[-1] == 1.0
    event = threading.Event()
    event.set()
    with pytest.raises(InterruptedError):
        image_edit.verify_bundle(bundle, tmp_path, event)


def test_changed_mtime_or_size_hashes_again(bundle_files, tmp_path):
    import os

    write, reads = bundle_files
    bundle = write(b"model bytes")
    image_edit.verify_bundle(bundle, tmp_path, threading.Event())
    stat = (tmp_path / "test.gguf").stat()
    os.utime(tmp_path / "test.gguf", ns=(stat.st_atime_ns, stat.st_mtime_ns - 5 * 10**9))
    image_edit.verify_bundle(bundle, tmp_path, threading.Event())
    assert len(reads) == 2
    image_edit.verify_bundle(bundle, tmp_path, threading.Event())
    assert len(reads) == 2
    bundle = write(b"longer model bytes")
    image_edit.verify_bundle(bundle, tmp_path, threading.Event())
    assert len(reads) == 3


def test_failed_bundle_verification_is_not_cached(bundle_files, tmp_path):
    write, reads = bundle_files
    bundle = write(b"model bytes", sha256="0" * 64)
    for attempt in (1, 2):
        with pytest.raises(ValueError, match="checksum failed"):
            image_edit.verify_bundle(bundle, tmp_path, threading.Event())
        assert len(reads) == attempt
    # A cached pass must not hide a different expected checksum either.
    bundle = write(b"model bytes")
    image_edit.verify_bundle(bundle, tmp_path, threading.Event())
    bundle["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum failed"):
        image_edit.verify_bundle(bundle, tmp_path, threading.Event())
    # A size mismatch still fails before any read.
    reads.clear()
    bundle["files"][0]["size_bytes"] += 1
    with pytest.raises(FileNotFoundError):
        image_edit.verify_bundle(bundle, tmp_path, threading.Event())
    assert not reads


@pytest.mark.parametrize(
    "family, quant,gpu,backend",
    [
        ("qwen-image-edit-2511", "Q2_K", "mps", "MPS"),
        ("qwen-image-edit-2511", "Q4_K_M", "cuda:0", "CUDA"),
        ("qwen-image-edit-2511", "Q2_K", "cuda:1", "VULKAN"),
        ("flux2-klein-4b", "Q4_0", "mps", "MPS"),
        ("mage-flow-edit-turbo", "Q6_K", "mps", "MPS"),
        ("mage-flow-edit-turbo", "Q6_K", "cuda:0", "CUDA"),
    ],
)
def test_native_command_preserves_quantization_and_model_specific_configuration(
    family, quant, gpu, backend
):
    selected = model(quant, family)
    files = {file["role"]: Path("/model files") / file["filename"] for file in selected["files"]}
    prompt = "Change the sign to '$5' $(echo untouched)."
    plan = EditPlan(16 * GIB, 4 * GIB, 6 * GIB, 512, 16 * GIB)
    command = image_edit.build_edit_command(
        "/runtime/sd-cli",
        selected,
        files,
        "reference.png",
        "output.png",
        prompt,
        plan,
        gpu,
        40,
        42,
        (512, 384),
        backend=backend,
    )
    assert command[command.index("-p") + 1] == prompt
    assert "--type" not in command  # Never expand a GGUF bundle into float weights.
    assert "--disable-prefetch" in command and "--mmap" in command
    assert command[command.index("--max-vram") + 1] == "6.00"
    if family == "mage-flow-edit-turbo" and backend == "MPS":
        assert command[command.index("--backend") + 1] == "cpu"
        assert "--threads" not in command
    else:
        assert command[command.index("--backend") + 1].startswith(
            {"MPS": "diffusion=MTL0", "CUDA": "diffusion=CUDA0", "VULKAN": "diffusion=Vulkan1"}[
                backend
            ]
        )
        assert "--threads" not in command
    if family == "flux2-klein-4b":
        # Distilled: guidance 1, no vision projector, no model arguments.
        assert "--model-args" not in command and "--llm_vision" not in command
        assert command[command.index("--cfg-scale") + 1] == "1"
        assert selected["default_steps"] == 4
    elif family == "mage-flow-edit-turbo":
        # Distilled 4-step editor with Qwen3-VL vision conditioning and automatic preset.
        assert "--llm_vision" in command and "--model-args" not in command
        assert command[command.index("--cfg-scale") + 1] == "1"
        assert selected["default_steps"] == 4
    else:
        assert "--llm_vision" in command
        assert command[command.index("--model-args") + 1] == (
            "qwen_image_zero_cond_t=true"
            if family.endswith("2511")
            else "qwen_image_2_1_prefix_cache=false"
        )


def test_dimensions_are_aligned_bounded_and_never_upscale_a_reference():
    assert image_edit.edit_dimensions(4000, 3000, 512) == (512, 384)
    assert image_edit.edit_dimensions(320, 96, 1024) == (320, 96)


def test_vulkan_mapping_uses_card_identity_not_torch_index():
    listing = "Vulkan0\tIntel Arc A770\nVulkan1\tNVIDIA GeForce RTX 4080\nCPU\tCPU\n"
    assert image_edit.match_vulkan_device(listing, "NVIDIA GeForce RTX 4080") == "Vulkan1"
    assert (
        image_edit.match_vulkan_device(
            "Vulkan0\tAMD Radeon RX 9060 XT (RADV GFX1200)\n", "AMD Radeon RX 9060 XT"
        )
        == "Vulkan0"
    )


@pytest.mark.parametrize(
    "listing",
    [
        "Vulkan0\tIntel Arc A770\n",
        "Vulkan0\tNVIDIA GeForce RTX 4080 SUPER\n",
        "Vulkan0\tNVIDIA GeForce RTX 4080\nVulkan1\tNVIDIA GeForce RTX 4080\n",
    ],
)
def test_vulkan_mapping_refuses_wrong_or_ambiguous_gpu(listing):
    with pytest.raises(ValueError, match="uniquely identify"):
        image_edit.match_vulkan_device(listing, "NVIDIA GeForce RTX 4080")


@pytest.mark.parametrize(
    ("listing", "selected"),
    [
        # Issue #131: a Radeon 780M laptop, named generically on one side.
        ("Vulkan0\tAMD Radeon 780M (RADV PHOENIX)\n", "AMD Radeon Graphics"),
        ("Vulkan0\tAMD Radeon Graphics (RADV GFX1103_R1)\n", "AMD Radeon 780M"),
        # Mesa's software renderer is not a second GPU.
        (
            "Vulkan0\tAMD Radeon 780M (RADV PHOENIX)\nVulkan1\tllvmpipe (LLVM 17.0.6, 256 bits)\n",
            "AMD Radeon Graphics",
        ),
    ],
)
def test_vulkan_mapping_takes_the_only_gpu_when_one_name_is_generic(listing, selected):
    assert image_edit.match_vulkan_device(listing, selected) == "Vulkan0"


def test_vulkan_mapping_follows_gpus_narrowed_with_ggml_vk_visible_devices():
    listing = "Vulkan0\tAMD Radeon RX 7600 (RADV NAVI33)\n"
    with pytest.raises(ValueError, match="uniquely identify"):
        image_edit.match_vulkan_device(listing, "AMD Radeon RX 7900 XTX")
    assert (
        image_edit.match_vulkan_device(listing, "AMD Radeon RX 7900 XTX", visible_devices_set=True)
        == "Vulkan0"
    )


@pytest.mark.parametrize(
    "listing",
    [
        # Another vendor is never taken for a generic name.
        "Vulkan0\tIntel(R) Iris(R) Xe Graphics (TGL GT2)\n",
        # Two AMD GPUs: the generic name does not say which.
        "Vulkan0\tAMD Radeon 780M (RADV PHOENIX)\nVulkan1\tAMD Radeon RX 7600 (RADV NAVI33)\n",
    ],
)
def test_vulkan_mapping_still_refuses_a_gpu_it_cannot_tell_apart(listing):
    with pytest.raises(ValueError, match="Vulkan lists: 0: ") as error:
        image_edit.match_vulkan_device(listing, "AMD Radeon Graphics")
    assert "GGML_VK_VISIBLE_DEVICES" in str(error.value)


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "no_image"])
def test_edit_job_lifecycle_with_tiny_files_and_no_model_runtime(monkeypatch, tmp_path, outcome):
    from PIL import Image

    request = {
        "job_id": "fixture-edit",
        "model_id": model()["model_id"],
        "prompt": "Change the background.",
        "device": "mps",
        "runtime_path": str(tmp_path / "sd-cli"),
        "bundle_dir": str(tmp_path),
        "image_path": str(tmp_path / "source.png"),
        "output_path": str(tmp_path / "output.png"),
        "scratch_directory": str(tmp_path / "scratch"),
    }
    (tmp_path / "scratch").mkdir()
    Image.new("RGB", (128, 96)).save(request["image_path"])
    original = Path(request["image_path"]).read_bytes()
    monkeypatch.setattr(image_edit, "check_runtime", lambda _: tmp_path / "sd-cli")
    monkeypatch.setattr(
        image_edit,
        "verify_bundle",
        lambda *_: {entry["role"]: Path(entry["filename"]) for entry in model()["files"]},
    )
    cancel = threading.Event()
    children = []

    class FixtureProcess:
        def __init__(self, command, **_kwargs):
            spec = json.loads(Path(command[-1]).read_text())
            native = spec["command"]
            if outcome == "success":
                Image.new("RGB", (128, 96), "red").save(native[native.index("-o") + 1])
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"fixture log\n")
            self.returncode = 1 if outcome == "failure" else 130 if outcome == "cancel" else 0
            children.append(self)

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image_edit.subprocess, "Popen", FixtureProcess)
    messages = []
    memory = EditMemory(26 * GIB, 25 * GIB, unified=True)
    if outcome == "success":
        image_edit.run_edit_job(request, cancel, messages.append, sampler=lambda _: memory)
        assert Path(request["output_path"]).is_file()
        assert json.loads(messages[-1].to_json())["type"] == "job_completed"
    else:
        error = InterruptedError if outcome == "cancel" else RuntimeError
        with pytest.raises(error):
            image_edit.run_edit_job(request, cancel, messages.append, sampler=lambda _: memory)
        assert not Path(request["output_path"]).exists()
    assert Path(request["image_path"]).read_bytes() == original
    assert not list((tmp_path / "scratch").iterdir())
    assert all(child.stdin.closed and child.stdout.closed for child in children)


def test_an_edit_that_does_not_fit_is_refused_before_anything_is_written(monkeypatch, tmp_path):
    import shutil

    from PIL import Image

    from localsr.core import disk_space

    monkeypatch.delenv("LOCALSR_DISK_RESERVE_BYTES")

    request = {
        "job_id": "fixture-edit",
        "model_id": model()["model_id"],
        "prompt": "Change the background.",
        "device": "mps",
        "runtime_path": str(tmp_path / "sd-cli"),
        "bundle_dir": str(tmp_path),
        "image_path": str(tmp_path / "source.png"),
        "output_path": str(tmp_path / "output.png"),
        "scratch_directory": str(tmp_path / "scratch"),
    }
    (tmp_path / "scratch").mkdir()
    Image.new("RGB", (128, 96)).save(request["image_path"])
    monkeypatch.setattr(image_edit, "check_runtime", lambda _: tmp_path / "sd-cli")
    monkeypatch.setattr(
        image_edit,
        "verify_bundle",
        lambda *_: {entry["role"]: Path(entry["filename"]) for entry in model()["files"]},
    )
    monkeypatch.setattr(
        disk_space,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(100 * 10**9, 99 * 10**9, 10**9),
    )
    monkeypatch.setattr(
        image_edit.subprocess, "Popen", lambda *a, **k: pytest.fail("the editor must not start")
    )
    memory = EditMemory(26 * GIB, 25 * GIB, unified=True)
    with pytest.raises(disk_space.DiskFullError, match="Not enough free disk space"):
        image_edit.run_edit_job(
            request, threading.Event(), lambda _: None, sampler=lambda _: memory
        )
    assert not list((tmp_path / "scratch").iterdir())
    assert not Path(request["output_path"]).exists()


def test_a_cancel_during_verification_stops_the_edit_before_it_is_published(monkeypatch, tmp_path):
    from PIL import Image

    request = {
        "job_id": "fixture-edit",
        "model_id": model()["model_id"],
        "prompt": "Change the background.",
        "device": "mps",
        "runtime_path": str(tmp_path / "sd-cli"),
        "bundle_dir": str(tmp_path),
        "image_path": str(tmp_path / "source.png"),
        "output_path": str(tmp_path / "output.png"),
        "scratch_directory": str(tmp_path / "scratch"),
    }
    (tmp_path / "scratch").mkdir()
    Image.new("RGB", (128, 96)).save(request["image_path"])
    monkeypatch.setattr(image_edit, "check_runtime", lambda _: tmp_path / "sd-cli")
    monkeypatch.setattr(
        image_edit,
        "verify_bundle",
        lambda *_: {entry["role"]: Path(entry["filename"]) for entry in model()["files"]},
    )
    cancel = threading.Event()

    class FixtureProcess:
        def __init__(self, command, **_kwargs):
            native = json.loads(Path(command[-1]).read_text())["command"]
            Image.new("RGB", (128, 96), "red").save(native[native.index("-o") + 1])
            self.stdin, self.stdout, self.returncode = io.BytesIO(), io.BytesIO(b""), 0

        def poll(self):
            return self.returncode

    from PIL import PngImagePlugin

    real_verify = PngImagePlugin.PngImageFile.verify

    def verify_then_cancel(image):
        real_verify(image)
        cancel.set()

    monkeypatch.setattr(image_edit.subprocess, "Popen", FixtureProcess)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "verify", verify_then_cancel)
    memory = EditMemory(26 * GIB, 25 * GIB, unified=True)
    with pytest.raises(InterruptedError):
        image_edit.run_edit_job(request, cancel, lambda _: None, sampler=lambda _: memory)
    assert not Path(request["output_path"]).exists()


@pytest.mark.parametrize("outcome", ["success", "cancel", "failure"])
def test_worker_releases_restoration_caches_before_edit_and_clears_active_job(monkeypatch, outcome):
    from types import SimpleNamespace

    from localsr.worker import server as worker

    server = worker.WorkerServer()
    events = []
    server.model_adapter = SimpleNamespace(release=lambda: events.append("adapter released"))
    server.engine = SimpleNamespace(release_model=lambda: events.append("engine released"))
    monkeypatch.setattr(server, "reader_thread_func", lambda: None)
    monkeypatch.setattr(
        worker, "send_message", lambda message: events.append(json.loads(message.to_json())["type"])
    )

    def edit(data, cancel, emit):
        assert events.index("adapter released") < events.index("job_started")
        assert events.index("engine released") < events.index("job_started")
        assert server.active_job_id == "fixture-edit"
        if outcome == "cancel":
            raise InterruptedError("cancelled")
        if outcome == "failure":
            raise ValueError("fixture failure")

    monkeypatch.setattr(image_edit, "run_edit_job", edit)
    server.message_queue.put({"type": "edit_job_request", "data": {"job_id": "fixture-edit"}})
    server.message_queue.put({"type": "shutdown_request"})
    try:
        server.run()
        assert server.active_job_id is None
        if outcome != "success":
            assert ("job_cancelled" if outcome == "cancel" else "job_failed") in events
    finally:
        server.result_previews.close()


def test_edit_refuses_an_output_name_the_file_system_cannot_hold_before_any_work(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        image_edit, "check_runtime", lambda _: pytest.fail("Runtime must not start")
    )
    request = {
        "job_id": "long-name",
        "model_id": model()["model_id"],
        "prompt": "Change the background.",
        "device": "mps",
        "output_path": str(tmp_path / ("y" * 252 + ".png")),
    }
    with pytest.raises(ValueError, match="allows at most"):
        image_edit.run_edit_job(
            request,
            threading.Event(),
            lambda _message: None,
            sampler=lambda _: EditMemory(64 * GIB, 60 * GIB, unified=True),
        )


def test_catalog_offers_only_openly_licensed_bundles():
    assert {m["license_name"] for m in image_edit.edit_catalog()} == {"Apache-2.0", "MIT"}
    assert not any(m["terms_acceptance_required"] for m in image_edit.edit_catalog())


def test_bundle_with_restricted_license_needs_explicit_acceptance(monkeypatch):
    selected = {**model(), "terms_acceptance_required": True}
    monkeypatch.setattr(image_edit, "edit_catalog", lambda: [selected])
    with pytest.raises(ValueError, match="license"):
        image_edit.validate_request({"model_id": selected["model_id"], "prompt": "Edit this."})
    assert (
        image_edit.validate_request(
            {"model_id": selected["model_id"], "prompt": "Edit this.", "accepted_terms": True}
        )
        == selected
    )


def test_protocol_has_a_distinct_edit_request():
    request = EditJobRequest(
        "job",
        "input.png",
        "output.png",
        model()["model_id"],
        "bundle",
        "sd-cli",
        "Replace the background.",
        "mps",
    )
    payload = json.loads(request.to_json())
    assert payload["type"] == "edit_job_request"
    assert payload["data"]["max_dimension"] == 512


def test_guard_terminates_native_child_when_pressure_rises(monkeypatch, tmp_path):
    blocker = threading.Event()
    monkeypatch.setattr(
        sys,
        "stdin",
        type(
            "Input",
            (),
            {"buffer": type("Buffer", (), {"read": lambda self, _: blocker.wait(10)})()},
        )(),
    )
    initial = EditMemory(64 * GIB, 60 * GIB)
    samples = iter([initial, replace(initial, available_ram=GIB)])
    monkeypatch.setattr(edit_guard, "read_host_memory", lambda _: next(samples))
    children = []
    real_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(edit_guard.subprocess, "Popen", spawn)
    spec = {
        "command": [sys.executable, "-c", "import time; time.sleep(20)"],
        "plan": {
            "required_ram": GIB,
            "reserve_ram": 4 * GIB,
            "gpu_budget": 4 * GIB,
            "max_dimension": 512,
            "process_limit": GIB,
        },
        "unified": False,
        "swap_used": 0,
    }
    path = tmp_path / "guard.json"
    path.write_text(json.dumps(spec))
    try:
        assert edit_guard.main([str(path)]) == 75
        assert children and children[0].poll() is not None
    finally:
        blocker.set()


def test_guard_treats_worker_pipe_eof_as_cancellation(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": io.BytesIO()})())
    monkeypatch.setattr(edit_guard, "read_host_memory", lambda _: EditMemory(64 * GIB, 60 * GIB))
    spec = {
        "command": [sys.executable, "-c", "import time; time.sleep(20)"],
        "plan": {
            "required_ram": GIB,
            "reserve_ram": 4 * GIB,
            "gpu_budget": 4 * GIB,
            "max_dimension": 512,
            "process_limit": GIB,
        },
        "unified": False,
        "swap_used": 0,
    }
    path = tmp_path / "guard.json"
    path.write_text(json.dumps(spec))
    assert edit_guard.main([str(path)]) == 130


def run_guard(monkeypatch, tmp_path, samples, child_seconds):
    """The guard's exit code for a child that sleeps, with ``samples`` as the snapshots.

    The first sample is the check before the child starts; the last one repeats.
    """
    blocker = threading.Event()
    monkeypatch.setattr(
        sys,
        "stdin",
        type(
            "Input",
            (),
            {"buffer": type("Buffer", (), {"read": lambda self, _: blocker.wait(30)})()},
        )(),
    )
    feed, last = iter(samples), [samples[0]]

    def sample(_unified):
        last[0] = next(feed, last[0])
        return last[0]

    monkeypatch.setattr(edit_guard, "read_host_memory", sample)
    spec = {
        "command": [sys.executable, "-c", f"import time; time.sleep({child_seconds})"],
        "plan": {
            "required_ram": GIB,
            "reserve_ram": GPU_PLAN.reserve_ram,
            "gpu_budget": 4 * GIB,
            "max_dimension": 512,
            "process_limit": 2 * GIB,
        },
        "unified": False,
        "swap_used": 0,
    }
    path = tmp_path / "guard.json"
    path.write_text(json.dumps(spec))
    try:
        return edit_guard.main([str(path)])
    finally:
        blocker.set()


@pytest.mark.parametrize("compressed", [True, False], ids=["zram", "disk"])
def test_guard_lets_an_edit_finish_while_cold_pages_go_to_swap(monkeypatch, tmp_path, compressed):
    swapped = int(0.6 * GIB)
    grown = replace(GPU_HOST, swap_used=swapped)
    if compressed:
        grown = replace(grown, compressed_swap=swapped)
    assert run_guard(monkeypatch, tmp_path, [GPU_HOST, grown], 1.5) == 0


def test_guard_ignores_swap_that_grew_before_memory_became_tight(monkeypatch, tmp_path):
    early = replace(GPU_HOST, swap_used=GIB)
    tight = replace(early, available_ram=10 * GIB)
    assert run_guard(monkeypatch, tmp_path, [GPU_HOST, early, tight], 1.5) == 0


def test_guard_stops_when_the_system_swaps_to_disk_while_memory_is_tight(monkeypatch, tmp_path):
    tight = replace(GPU_HOST, available_ram=10 * GIB)
    swapping = replace(tight, swap_used=GIB)
    assert run_guard(monkeypatch, tmp_path, [GPU_HOST, tight, swapping], 20) == 75


def test_guard_stops_on_a_sustained_memory_stall_but_not_on_one_spike(monkeypatch, tmp_path):
    from localsr.core import edit_memory

    monkeypatch.setattr(edit_memory, "MEMORY_STALL_SECONDS", 0.5)
    stalled = replace(GPU_HOST, memory_stall=40.0)
    calm = replace(GPU_HOST, memory_stall=0.5)
    assert run_guard(monkeypatch, tmp_path, [GPU_HOST, stalled, calm], 1.5) == 0
    assert run_guard(monkeypatch, tmp_path, [GPU_HOST, stalled], 20) == 75


# The phase markers of a real FLUX.2 klein 4B run (sd-cli 3f8527a, 512 px).
FLUX_LOG = """[INFO   ] diffusion_engine.cpp:732  - loading diffusion model from 'flux-2-klein-4b-Q4_0.gguf'
  |###############################################   | 100/108 - 552.20MB/s
[INFO   ] model_loader.cpp:1383 - loading tensors completed, taking 0.41s
  |========>                                         | 1/6 - 3.90s/it
[INFO   ] image.cpp:398  - encode_first_stage completed, taking 21.16s
  |##################################################| 298/298 - 0.00MB/s
[INFO   ] model_loader.cpp:1383 - loading tensors completed, taking 0.21s
[INFO   ] image.cpp:529  - get_learned_condition completed, taking 21.99s
[INFO   ] image.cpp:866  - generating image: 1/1 - seed 42
  |##################################################| 149/149 - 0.07MB/s
  |============>                                     | 1/4 - 13.32s/it
  |=========================>                        | 2/4 - 7.40s/it
  |==================================================| 4/4 - 7.40s/it
[INFO   ] image.cpp:899  - sampling completed, taking 35.53s
[INFO   ] image.cpp:554  - decoding 1 latents
  |========>                                         | 1/6 - 8.53s/it
  |==================================================| 6/6 - 7.88s/it
[INFO   ] image.cpp:624  - decode_first_stage completed, taking 48.45s
[INFO   ] main.cpp:497  - save result image 0 to 'result.png'"""


def test_edit_progress_follows_the_runtime_log_and_never_stands_still():
    now = [0.0]
    tracker = image_edit.EditProgress(4, clock=lambda: now[0])
    seen = []

    def look():
        seen.append(tracker.snapshot())
        return seen[-1]

    tracker.checked(0.5)
    assert look()[1] == "Checking the model files"
    tracker.enter(1)
    assert look()[1] == "Loading the model"
    for line in FLUX_LOG.splitlines():
        before = look()[0]
        now[0] += 10  # time passes inside every phase
        assert look()[0] >= before, "the bar never moves backwards"
        tracker.feed(line)
    labels = [label for _, label in seen]
    for expected in (
        "Reading the photo",
        "Reading your instruction",
        "Editing · step 2 of 4",
        "Finishing the image · part 1 of 6",
        "Saving",
    ):
        assert expected in labels
    assert tracker.completed_steps == 4
    assert not any("149" in label or "298" in label for label in labels)
    # Phases without a counter still creep forward while time passes.
    stuck = image_edit.EditProgress(4, clock=lambda: now[0])
    stuck.enter(2)
    first = stuck.snapshot()[0]
    now[0] += 20
    assert stuck.snapshot()[0] > first
    assert all(percentage <= 99 for percentage, _ in seen)


def test_custom_edit_memory_estimate_uses_real_file_sizes(tmp_path):
    from localsr.core.edit_memory import (
        estimate_edit_memory,
        minimum_unified_memory_gb,
        minimum_vram_gb,
    )

    diff = tmp_path / "diff.gguf"
    te = tmp_path / "te.gguf"
    vis = tmp_path / "mmproj.gguf"
    vae = tmp_path / "vae.safetensors"
    for path, size in (
        (diff, 12 * GIB),
        (te, 4 * GIB),
        (vis, 1 * GIB),
        (vae, 256 * 1024**2),
    ):
        with path.open("wb") as handle:
            handle.truncate(size)

    model_cfg = image_edit.validate_request(
        {
            "model_id": "__custom__",
            "family": "qwen-image-2.1",
            "custom_files": {
                "diffusion": str(diff),
                "text_encoder": str(te),
                "vision": str(vis),
                "vae": str(vae),
            },
            "prompt": "Change the sky.",
        }
    )
    assert model_cfg["name"] == "Your edit model"
    assert model_cfg["cfg_scale"] == 6.0
    assert model_cfg["default_steps"] == 40
    expected_mac = int((12 * GIB + 256 * 1024**2 + int(1.5 * GIB)) * 1.1)
    assert estimate_edit_memory(model_cfg, True, 512) == expected_mac
    assert minimum_unified_memory_gb(model_cfg) == 32
    assert minimum_vram_gb(model_cfg) == 16

    # Guard refuses when Mac RAM is below the real custom requirement, and admits when it fits.
    with pytest.raises(MemoryError, match="smaller"):
        plan_edit(model_cfg, EditMemory(16 * GIB, 14 * GIB, unified=True))
    plan = plan_edit(model_cfg, EditMemory(32 * GIB, 24 * GIB, unified=True))
    assert plan.required_ram == expected_mac

    # FLUX.2 klein custom works without a vision projector and uses 4 steps / CFG 1.
    with diff.open("wb") as handle:
        handle.truncate(2 * GIB)
    klein_cfg = image_edit.validate_request(
        {
            "model_id": "__custom__",
            "family": "flux2-klein",
            "custom_files": {
                "diffusion": str(diff),
                "text_encoder": str(te),
                "vae": str(vae),
            },
            "prompt": "Warm lighting.",
        }
    )
    assert [entry["role"] for entry in klein_cfg["files"]] == ["diffusion", "text_encoder", "vae"]
    assert klein_cfg["default_steps"] == 4 and klein_cfg["cfg_scale"] == 1.0
    assert minimum_unified_memory_gb(klein_cfg) == 16
    assert minimum_vram_gb(klein_cfg) == 8


def test_custom_edit_command_and_lifecycle_keep_files_in_place_and_omit_filenames(
    monkeypatch, tmp_path
):
    from PIL import Image

    diff = tmp_path / "secret-qwen21.gguf"
    te = tmp_path / "secret-te.gguf"
    vis = tmp_path / "secret-mmproj.gguf"
    vae = tmp_path / "secret-vae.safetensors"
    for path in (diff, te, vis, vae):
        path.write_bytes(b"weights")

    request = {
        "job_id": "custom-edit-job",
        "model_id": "__custom__",
        "family": "qwen-image-2.1",
        "custom_files": {
            "diffusion": str(diff),
            "text_encoder": str(te),
            "vision": str(vis),
            "vae": str(vae),
        },
        "prompt": "Make it winter.",
        "device": "mps",
        "runtime_path": str(tmp_path / "sd-cli"),
        "bundle_dir": "",
        "image_path": str(tmp_path / "source.png"),
        "output_path": str(tmp_path / "output.png"),
        "scratch_directory": str(tmp_path / "scratch"),
    }
    (tmp_path / "scratch").mkdir()
    Image.new("RGB", (128, 96)).save(request["image_path"])
    monkeypatch.setattr(image_edit, "check_runtime", lambda _: tmp_path / "sd-cli")

    captured = []

    class FixtureProcess:
        def __init__(self, command, **_kwargs):
            native = json.loads(Path(command[-1]).read_text())["command"]
            captured.append(native)
            Image.new("RGB", (128, 96), "blue").save(native[native.index("-o") + 1])
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"")
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image_edit.subprocess, "Popen", FixtureProcess)
    messages = []
    memory = EditMemory(32 * GIB, 28 * GIB, unified=True)
    image_edit.run_edit_job(request, threading.Event(), messages.append, sampler=lambda _: memory)
    assert Path(request["output_path"]).is_file()
    native = captured[0]
    assert native[native.index("--diffusion-model") + 1] == str(diff)
    assert native[native.index("--llm_vision") + 1] == str(vis)
    assert native[native.index("--cfg-scale") + 1] == "6"
    assert native[native.index("--steps") + 1] == "40"
    assert native[native.index("--model-args") + 1] == "qwen_image_2_1_prefix_cache=false"
    started = json.loads(messages[0].to_json())
    assert started["data"]["model_id"] == "Your edit model"
    assert "secret-" not in json.dumps([json.loads(m.to_json()) for m in messages])


def test_selected_runtime_requires_current_cli_controls(tmp_path, monkeypatch):
    runtime = tmp_path / "sd-cli"
    runtime.touch()
    monkeypatch.setattr(
        image_edit,
        "probe_runtime",
        lambda path, option: subprocess.CompletedProcess([str(path), option], 0, "old runtime", ""),
    )
    with pytest.raises(ValueError, match="too old"):
        image_edit.check_runtime(str(runtime))
    monkeypatch.setattr(
        image_edit,
        "probe_runtime",
        lambda path, option: subprocess.CompletedProcess(
            [str(path), option], 0, " ".join(image_edit.REQUIRED_FLAGS), ""
        ),
    )
    assert image_edit.check_runtime(str(runtime)) == runtime.resolve()


def test_runtime_probe_runs_once_per_runtime_and_timeouts_are_explained(tmp_path, monkeypatch):
    # Issue #160: a cold Windows start of the CUDA sd-cli outlived the old 10 s probe.
    assert image_edit.PROBE_TIMEOUTS["--help"] >= 60
    assert image_edit.PROBE_TIMEOUTS["--list-devices"] >= 90
    monkeypatch.setattr(image_edit, "_CHECKED_RUNTIMES", set())
    runtime = tmp_path / "sd-cli"
    runtime.write_bytes(b"runtime")

    def slow(path, option):
        raise subprocess.TimeoutExpired([str(path), option], image_edit.PROBE_TIMEOUTS[option])

    monkeypatch.setattr(image_edit, "probe_runtime", slow)
    with pytest.raises(ValueError, match="did not answer in time"):
        image_edit.check_runtime(str(runtime))

    calls = []

    def ok(path, option):
        calls.append(option)
        return subprocess.CompletedProcess(
            [str(path), option], 0, " ".join(image_edit.REQUIRED_FLAGS), ""
        )

    monkeypatch.setattr(image_edit, "probe_runtime", ok)
    assert image_edit.check_runtime(str(runtime)) == runtime.resolve()
    assert image_edit.check_runtime(str(runtime)) == runtime.resolve()
    assert calls == ["--help"]
    # A replaced runtime is probed again.
    runtime.write_bytes(b"runtime, newer build")
    image_edit.check_runtime(str(runtime))
    assert calls == ["--help", "--help"]


@pytest.mark.parametrize(
    "model_id,gpu_gb,max_dim,expected",
    [
        ("flux2_klein_4b_q4_0", 8, 512, ("gpu", "gpu", False)),
        ("flux2_klein_4b_q4_0", 12, 768, ("gpu", "gpu", False)),
        ("flux2_klein_4b_q4_0", 24, 1024, ("gpu", "gpu", False)),
        ("flux2_klein_4b_q8_0", 8, 512, ("cpu", "gpu", True)),
        ("flux2_klein_4b_q8_0", 12, 512, ("gpu", "gpu", False)),
        ("flux2_klein_4b_q8_0", 12, 768, ("gpu", "gpu", False)),
        ("flux2_klein_4b_q8_0", 24, 1024, ("gpu", "gpu", False)),
        ("mage_flow_edit_turbo_q6_k", 8, 512, ("cpu", "gpu", True)),
        ("mage_flow_edit_turbo_q6_k", 12, 768, ("gpu", "gpu", False)),
        ("mage_flow_edit_turbo_q6_k", 24, 1024, ("gpu", "gpu", False)),
        ("qwen_edit_2511_q2_k", 8, 512, ("cpu", "cpu", True)),
        ("qwen_edit_2511_q2_k", 12, 768, ("cpu", "gpu", True)),
        ("qwen_edit_2511_q2_k", 24, 1024, ("gpu", "gpu", False)),
        ("qwen_edit_2511_q3_k_s", 12, 768, ("cpu", "gpu", True)),
        ("qwen_edit_2511_q3_k_s", 24, 1024, ("gpu", "gpu", False)),
        ("qwen_edit_2511_q4_k_m", 24, 512, ("gpu", "gpu", False)),
        ("qwen_edit_2511_q4_k_m", 24, 1024, ("cpu", "gpu", True)),
        ("qwen_edit_2511_q5_k_m", 24, 512, ("gpu", "gpu", False)),
        ("qwen_edit_2511_q8_0", 24, 512, ("cpu", "gpu", True)),
    ],
)
def test_plan_edit_places_parts_by_discrete_gpu_memory(model_id, gpu_gb, max_dim, expected):
    selected = next(m for m in image_edit.edit_catalog() if m["model_id"] == model_id)
    memory = EditMemory(64 * GIB, 60 * GIB, gpu_total=gpu_gb * GIB, gpu_free=gpu_gb * GIB)
    plan = plan_edit(selected, memory, max_dim)
    assert (plan.text_encoder, plan.vae, plan.offload) == expected


@pytest.mark.parametrize(
    "model_id,ram_gb",
    [
        ("flux2_klein_4b_q4_0", 16),
        ("flux2_klein_4b_q8_0", 16),
        ("mage_flow_edit_turbo_q6_k", 16),
        ("qwen_edit_2511_q2_k", 24),
        ("qwen_edit_2511_q4_k_m", 32),
        ("qwen_edit_2511_q8_0", 48),
    ],
)
def test_plan_edit_keeps_unified_memory_on_cpu_for_every_family(model_id, ram_gb):
    selected = next(m for m in image_edit.edit_catalog() if m["model_id"] == model_id)
    memory = EditMemory(ram_gb * GIB, int(ram_gb * 0.85 * GIB), unified=True)
    plan = plan_edit(selected, memory, 1024)
    assert (plan.text_encoder, plan.vae, plan.offload) == ("cpu", "cpu", True)


def test_build_edit_command_exact_arguments_for_unified_and_discrete_placements():
    def run_cmd(selected, plan, device, backend="", rocm=False, gpu_override=None):
        files = {entry["role"]: Path("/models") / entry["filename"] for entry in selected["files"]}
        return image_edit.build_edit_command(
            str(Path("/runtime/sd-cli")),
            selected,
            files,
            "ref.png",
            "out.png",
            "Add snow",
            plan,
            device,
            selected["default_steps"],
            42,
            (512, 384),
            rocm=rocm,
            backend=backend,
            gpu_override=gpu_override,
        )

    # Unified memory commands remain byte-identical to today across all families.
    qwen = model("Q2_K", "qwen-image-edit-2511")
    qwen_mac = plan_edit(qwen, EditMemory(24 * GIB, 20 * GIB, unified=True))
    assert run_cmd(qwen, qwen_mac, "mps") == [
        str(Path("/runtime/sd-cli")),
        "--diffusion-model",
        str(Path("/models/qwen-image-edit-2511-Q2_K.gguf")),
        "--llm",
        str(Path("/models/Qwen2.5-VL-7B-Instruct.Q2_K.gguf")),
        "--llm_vision",
        str(Path("/models/Qwen2.5-VL-7B-Instruct.mmproj-Q8_0.gguf")),
        "--vae",
        str(Path("/models/qwen_image_vae.safetensors")),
        "-r",
        "ref.png",
        "-o",
        "out.png",
        "-p",
        "Add snow",
        "--steps",
        "40",
        "--seed",
        "42",
        "-W",
        "512",
        "-H",
        "384",
        "--sampling-method",
        "euler",
        "--offload-to-cpu",
        "--mmap",
        "--disable-prefetch",
        "--fa",
        "--vae-tiling",
        "--vae-tile-size",
        "256",
        "--conditioning-cache-size",
        "0",
        "--backend",
        "diffusion=MTL0,te=cpu,vae=cpu",
        "--max-vram",
        f"{qwen_mac.gpu_budget / GIB:.2f}",
        "--cfg-scale",
        "2.5",
        "--flow-shift",
        "3",
        "--model-args",
        "qwen_image_zero_cond_t=true",
    ]

    mage = model("Q6_K", "mage-flow-edit-turbo")
    mage_mac = plan_edit(mage, EditMemory(16 * GIB, 14 * GIB, unified=True))
    assert run_cmd(mage, mage_mac, "mps") == [
        str(Path("/runtime/sd-cli")),
        "--diffusion-model",
        str(Path("/models/mage-flow-edit-turbo-Q6_K.gguf")),
        "--llm",
        str(Path("/models/Qwen3VL-4B-Instruct-Q4_K_M.gguf")),
        "--llm_vision",
        str(Path("/models/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf")),
        "--vae",
        str(Path("/models/mage_flow_vae_bf16.safetensors")),
        "-r",
        "ref.png",
        "-o",
        "out.png",
        "-p",
        "Add snow",
        "--steps",
        "4",
        "--seed",
        "42",
        "-W",
        "512",
        "-H",
        "384",
        "--sampling-method",
        "euler",
        "--offload-to-cpu",
        "--mmap",
        "--disable-prefetch",
        "--fa",
        "--vae-tiling",
        "--vae-tile-size",
        "256",
        "--conditioning-cache-size",
        "0",
        "--backend",
        "cpu",
        "--max-vram",
        f"{mage_mac.gpu_budget / GIB:.2f}",
        "--cfg-scale",
        "1",
    ]

    # Discrete GPU: all fit -> no --offload-to-cpu, te=CUDA0, vae=CUDA0.
    flux_q4 = model("Q4_0", "flux2-klein-4b")
    flux_24 = plan_edit(
        flux_q4, EditMemory(64 * GIB, 60 * GIB, gpu_total=24 * GIB, gpu_free=24 * GIB)
    )
    assert run_cmd(flux_q4, flux_24, "cuda:0", backend="CUDA") == [
        str(Path("/runtime/sd-cli")),
        "--diffusion-model",
        str(Path("/models/flux-2-klein-4b-Q4_0.gguf")),
        "--llm",
        str(Path("/models/Qwen3-4B-Q4_K_M.gguf")),
        "--vae",
        str(Path("/models/flux2-vae.safetensors")),
        "-r",
        "ref.png",
        "-o",
        "out.png",
        "-p",
        "Add snow",
        "--steps",
        "4",
        "--seed",
        "42",
        "-W",
        "512",
        "-H",
        "384",
        "--sampling-method",
        "euler",
        "--mmap",
        "--disable-prefetch",
        "--fa",
        "--vae-tiling",
        "--vae-tile-size",
        "256",
        "--conditioning-cache-size",
        "0",
        "--backend",
        "diffusion=CUDA0,te=CUDA0,vae=CUDA0",
        "--max-vram",
        f"{flux_24.gpu_budget / GIB:.2f}",
        "--cfg-scale",
        "1",
    ]

    # Discrete GPU: VAE fits, TE does not -> keep --offload-to-cpu, te=cpu, vae=CUDA0.
    qwen_12 = plan_edit(qwen, EditMemory(64 * GIB, 60 * GIB, gpu_total=12 * GIB, gpu_free=12 * GIB))
    cmd_12 = run_cmd(qwen, qwen_12, "cuda:0", backend="CUDA")
    assert "--offload-to-cpu" in cmd_12
    assert cmd_12[cmd_12.index("--backend") + 1] == "diffusion=CUDA0,te=cpu,vae=CUDA0"

    # Discrete GPU: neither fits -> keep --offload-to-cpu, te=cpu, vae=cpu.
    qwen_8 = plan_edit(qwen, EditMemory(64 * GIB, 60 * GIB, gpu_total=8 * GIB, gpu_free=8 * GIB))
    cmd_8 = run_cmd(qwen, qwen_8, "cuda:0", backend="CUDA")
    assert "--offload-to-cpu" in cmd_8
    assert cmd_8[cmd_8.index("--backend") + 1] == "diffusion=CUDA0,te=cpu,vae=cpu"

    # Vulkan & ROCm follow the selected GPU identifier for placed components.
    cmd_vk = run_cmd(flux_q4, flux_24, "cuda:1", backend="VULKAN")
    assert cmd_vk[cmd_vk.index("--backend") + 1] == "diffusion=Vulkan1,te=Vulkan1,vae=Vulkan1"
    cmd_rocm = run_cmd(flux_q4, flux_24, "cuda:0", rocm=True)
    assert cmd_rocm[cmd_rocm.index("--backend") + 1] == "diffusion=ROCm0,te=ROCm0,vae=ROCm0"


def test_plan_edit_placement_overrides_and_refusals():
    flux_q4 = model("Q4_0", "flux2-klein-4b")
    gpu_24 = EditMemory(64 * GIB, 60 * GIB, gpu_total=24 * GIB, gpu_free=24 * GIB)

    p_te_cpu = plan_edit(flux_q4, gpu_24, text_encoder="cpu")
    assert (p_te_cpu.text_encoder, p_te_cpu.vae, p_te_cpu.offload) == ("cpu", "gpu", True)

    p_vae_cpu = plan_edit(flux_q4, gpu_24, vae="cpu")
    assert (p_vae_cpu.text_encoder, p_vae_cpu.vae, p_vae_cpu.offload) == ("gpu", "cpu", True)

    p_both_cpu = plan_edit(flux_q4, gpu_24, text_encoder="cpu", vae="cpu")
    assert (p_both_cpu.text_encoder, p_both_cpu.vae, p_both_cpu.offload) == ("cpu", "cpu", True)

    # Forced GPU that does not fit refuses clearly.
    qwen_q2 = model("Q2_K", "qwen-image-edit-2511")
    gpu_8 = EditMemory(64 * GIB, 60 * GIB, gpu_total=8 * GIB, gpu_free=8 * GIB)
    with pytest.raises(MemoryError, match="VAE"):
        plan_edit(qwen_q2, gpu_8, vae="gpu")
    gpu_12 = EditMemory(64 * GIB, 60 * GIB, gpu_total=12 * GIB, gpu_free=12 * GIB)
    with pytest.raises(MemoryError, match="text encoder"):
        plan_edit(qwen_q2, gpu_12, text_encoder="gpu")

    # Forced GPU on Mage-Flow + unified memory refuses (Metal output is blank).
    mage = model("Q6_K", "mage-flow-edit-turbo")
    mac_16 = EditMemory(16 * GIB, 14 * GIB, unified=True)
    with pytest.raises(MemoryError, match="Mage-Flow"):
        plan_edit(mage, mac_16, vae="gpu")

    # Forced GPU on unified memory that exceeds working set refuses.
    flux_q8 = model("Q8_0", "flux2-klein-4b")
    with pytest.raises(MemoryError, match="text encoder"):
        plan_edit(flux_q8, mac_16, text_encoder="gpu", vae="gpu")

    # Invalid override values fail validation.
    with pytest.raises(ValueError, match="edit_text_encoder"):
        image_edit.validate_request(
            {"model_id": flux_q4["model_id"], "prompt": "Edit.", "edit_text_encoder": "tpu"}
        )
    with pytest.raises(ValueError, match="edit_vae"):
        image_edit.validate_request(
            {"model_id": flux_q4["model_id"], "prompt": "Edit.", "edit_vae": "tpu"}
        )


def test_discrete_gpu_host_admission_accounts_for_gpu_placement():
    # A machine with 8 GiB system RAM (6 GiB free, 4 GiB reserve = 2 GiB headroom)
    # and a 24 GiB GPU can run Qwen Q4_K_M when TE and VAE run on the GPU.
    qwen_q4 = model("Q4_K_M", "qwen-image-edit-2511")
    tight_host_24g_gpu = EditMemory(8 * GIB, 6 * GIB, gpu_total=24 * GIB, gpu_free=24 * GIB)
    plan = plan_edit(qwen_q4, tight_host_24g_gpu, 512)
    assert (plan.text_encoder, plan.vae, plan.offload) == ("gpu", "gpu", False)
    assert plan.required_ram <= 2 * GIB
    # If forced to CPU, the 5.15 GiB text encoder exceeds the 2 GiB host headroom.
    with pytest.raises(MemoryError, match="currently available"):
        plan_edit(qwen_q4, tight_host_24g_gpu, 512, text_encoder="cpu")


def test_run_edit_job_refuses_forced_gpu_before_run_and_emits_placement_notes(
    monkeypatch, tmp_path
):
    from PIL import Image

    image_path = tmp_path / "source.png"
    Image.new("RGB", (128, 96)).save(image_path)
    monkeypatch.setattr(image_edit, "check_runtime", lambda _: tmp_path / "sd-cli")
    monkeypatch.setattr(
        image_edit,
        "verify_bundle",
        lambda *_: {entry["role"]: Path(entry["filename"]) for entry in model()["files"]},
    )
    monkeypatch.setattr(image_edit, "resolve_edit_gpu", lambda _r, _d, _b: "CUDA0")

    # Refusal happens before starting the runtime or writing output.
    monkeypatch.setattr(
        image_edit.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("the editor must not start on refused placement"),
    )
    bad_req = {
        "job_id": "refuse-forced-gpu",
        "model_id": "qwen_edit_2511_q2_k",
        "prompt": "Brighten",
        "device": "cuda:0",
        "runtime_path": str(tmp_path / "sd-cli"),
        "bundle_dir": str(tmp_path),
        "image_path": str(image_path),
        "output_path": str(tmp_path / "refused.png"),
        "edit_vae": "gpu",
    }
    mem_8g = EditMemory(64 * GIB, 60 * GIB, gpu_total=8 * GIB, gpu_free=8 * GIB)
    with pytest.raises(MemoryError, match="VAE"):
        image_edit.run_edit_job(
            bad_req, threading.Event(), lambda _: None, sampler=lambda _: mem_8g
        )

    class FixtureProcess:
        def __init__(self, command, **_kwargs):
            native = json.loads(Path(command[-1]).read_text())["command"]
            Image.new("RGB", (128, 96), "green").save(native[native.index("-o") + 1])
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"")
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image_edit.subprocess, "Popen", FixtureProcess)
    (tmp_path / "scratch").mkdir()

    for idx, (mem, expected_note) in enumerate(
        [
            (
                EditMemory(64 * GIB, 60 * GIB, gpu_total=24 * GIB, gpu_free=24 * GIB),
                "Text encoder and VAE ran on the GPU.",
            ),
            (
                EditMemory(64 * GIB, 60 * GIB, gpu_total=12 * GIB, gpu_free=12 * GIB),
                "VAE ran on the GPU; text encoder ran on the CPU.",
            ),
            (
                EditMemory(64 * GIB, 60 * GIB, gpu_total=8 * GIB, gpu_free=8 * GIB),
                "Text encoder and VAE ran on the CPU.",
            ),
        ]
    ):
        messages = []
        req = {
            "job_id": f"ok-{idx}",
            "model_id": "qwen_edit_2511_q2_k",
            "prompt": "Brighten",
            "device": "cuda:0",
            "runtime_path": str(tmp_path / "sd-cli"),
            "bundle_dir": str(tmp_path),
            "image_path": str(image_path),
            "output_path": str(tmp_path / f"out-{idx}.png"),
            "scratch_directory": str(tmp_path / "scratch"),
        }
        image_edit.run_edit_job(
            req, threading.Event(), messages.append, sampler=lambda _d, m=mem: m
        )
        completed = json.loads(messages[-1].to_json())
        assert completed["type"] == "job_completed"
        assert completed["data"]["notes"] == expected_note


def test_engine_features_advertise_edit_placement():
    from localsr.worker.server import _engine_features

    features = _engine_features()
    assert "edit_placement" in features
    assert "edit_expert" in features


def test_12gb_card_runs_flux_klein_q4_at_1024_all_on_gpu():
    flux_q4 = model("Q4_0", "flux2-klein-4b")
    memory = EditMemory(64 * GIB, 60 * GIB, gpu_total=12 * GIB, gpu_free=int(10.66 * GIB))
    plan = plan_edit(flux_q4, memory, 1024)
    assert plan.gpu_budget == int(10.66 * GIB) - 1 * GIB
    assert plan.max_dimension == 1024
    assert (plan.text_encoder, plan.vae, plan.offload) == ("gpu", "gpu", False)


@pytest.mark.parametrize(
    "model_id,gpu_free_gib,requested,expected_dim,expected_place",
    [
        ("flux2_klein_4b_q4_0", 8.0, 1024, 1024, ("cpu", "gpu", True)),
        ("flux2_klein_4b_q4_0", 7.0, 1024, 768, ("cpu", "gpu", True)),
        ("flux2_klein_4b_q4_0", 5.5, 1024, 512, ("cpu", "gpu", True)),
        ("flux2_klein_4b_q4_0", 8.0, 768, 768, ("cpu", "gpu", True)),
        ("flux2_klein_4b_q8_0", 8.0, 1024, 768, ("cpu", "gpu", True)),
        ("flux2_klein_4b_q8_0", 7.0, 1024, 512, ("cpu", "gpu", True)),
        ("mage_flow_edit_turbo_q6_k", 8.0, 1024, 768, ("cpu", "gpu", True)),
        ("qwen_edit_2511_q2_k", 8.0, 1024, 512, ("cpu", "cpu", True)),
    ],
)
def test_8gb_card_picks_largest_size_that_fits(
    model_id, gpu_free_gib, requested, expected_dim, expected_place
):
    selected = next(m for m in image_edit.edit_catalog() if m["model_id"] == model_id)
    memory = EditMemory(64 * GIB, 60 * GIB, gpu_total=8 * GIB, gpu_free=int(gpu_free_gib * GIB))
    plan = plan_edit(selected, memory, requested)
    assert plan.max_dimension == expected_dim
    assert (plan.text_encoder, plan.vae, plan.offload) == expected_place


def test_discrete_gpu_size_fit_also_respects_host_ram():
    flux_q4 = model("Q4_0", "flux2-klein-4b")
    # 8 GiB system RAM (6 GiB free, 4 GiB reserve = 2 GiB headroom) cannot hold 1024 px
    # activations (4.0 GiB * 1.1 = 4.4 GiB) or 768 px (2.25 * 1.1 = 2.475 GiB), so it fits 512 px.
    tight_host = EditMemory(8 * GIB, 6 * GIB, gpu_total=24 * GIB, gpu_free=24 * GIB)
    plan = plan_edit(flux_q4, tight_host, 1024)
    assert plan.max_dimension == 512
    assert (plan.text_encoder, plan.vae, plan.offload) == ("gpu", "gpu", False)


def test_vram_limit_override_used_bounds_checked_and_ignored_on_unified(caplog):
    flux_q4 = model("Q4_0", "flux2-klein-4b")
    gpu_12 = EditMemory(64 * GIB, 60 * GIB, gpu_total=12 * GIB, gpu_free=int(10.66 * GIB))

    # Override replaces rule 1 budget: 6144 MiB (6 GiB) fits 768 px, not 1024 px.
    plan = plan_edit(flux_q4, gpu_12, 1024, vram_limit_mb=6144)
    assert plan.gpu_budget == 6144 * 1024**2
    assert plan.max_dimension == 768
    assert (plan.text_encoder, plan.vae, plan.offload) == ("cpu", "gpu", True)

    # 7680 MiB (7.5 GiB) allows 1024 px with offload; requesting 768 px fits completely on GPU.
    plan_75 = plan_edit(flux_q4, gpu_12, 1024, vram_limit_mb=7680)
    assert plan_75.gpu_budget == 7680 * 1024**2
    assert plan_75.max_dimension == 1024
    assert (plan_75.text_encoder, plan_75.vae, plan_75.offload) == ("cpu", "gpu", True)
    plan_75_768 = plan_edit(flux_q4, gpu_12, 768, vram_limit_mb=7680)
    assert plan_75_768.max_dimension == 768
    assert (plan_75_768.text_encoder, plan_75_768.vae, plan_75_768.offload) == ("gpu", "gpu", False)

    # Forced GPU placement that cannot fit at 1024/768 drops to 512 when 512 fits.
    flux_q8 = model("Q8_0", "flux2-klein-4b")
    assert plan_edit(flux_q8, gpu_12, 1024).max_dimension == 1024
    te_gpu_flux_q8 = plan_edit(flux_q8, gpu_12, 1024, text_encoder="gpu")
    assert te_gpu_flux_q8.max_dimension == 512
    assert (te_gpu_flux_q8.text_encoder, te_gpu_flux_q8.vae) == ("gpu", "gpu")

    # 0 keeps automatic rule 1.
    auto_plan = plan_edit(flux_q4, gpu_12, 1024, vram_limit_mb=0)
    assert auto_plan.max_dimension == 1024

    # Out-of-bounds values fail validation.
    for bad in (-1, 512, 1023, 12 * 1024 + 1, True):
        with pytest.raises(ValueError, match="edit_vram_limit_mb"):
            plan_edit(flux_q4, gpu_12, 1024, vram_limit_mb=bad)

    # Unified memory ignores vram_limit_mb with a log note and keeps the exact Mac plan.
    mac_24 = EditMemory(24 * GIB, 20 * GIB, unified=True)
    expected = plan_edit(flux_q4, mac_24, 1024)
    with caplog.at_level("INFO"):
        overridden = plan_edit(flux_q4, mac_24, 1024, vram_limit_mb=4096)
    assert overridden == expected
    assert "edit_vram_limit_mb" in caplog.text


def test_allow_any_size_keeps_requested_size_and_unified_plans_stay_identical():
    flux_q8 = model("Q8_0", "flux2-klein-4b")
    gpu_8 = EditMemory(64 * GIB, 60 * GIB, gpu_total=8 * GIB, gpu_free=8 * GIB)
    assert plan_edit(flux_q8, gpu_8, 1024).max_dimension == 768
    forced = plan_edit(flux_q8, gpu_8, 1024, allow_any_size=True)
    assert forced.max_dimension == 1024
    assert (forced.text_encoder, forced.vae, forced.offload) == ("cpu", "cpu", True)

    # Unified memory plans remain byte-identical across Mac tiers.
    qwen_q2 = model("Q2_K", "qwen-image-edit-2511")
    mac_24 = EditMemory(24 * GIB, 20 * GIB, unified=True)
    mac_plan = plan_edit(qwen_q2, mac_24, 1024)
    assert mac_plan == EditPlan(
        required_ram=int((7_468_022_368 + 253_806_246 + int(1.5 * GIB)) * 1.1),
        reserve_ram=int(24 * GIB * 0.10),
        gpu_budget=min(
            int((7_468_022_368 + 253_806_246 + int(1.5 * GIB)) * 1.1),
            int(24 * GIB * 0.60),
            20 * GIB - int(24 * GIB * 0.10),
        ),
        max_dimension=512,
        process_limit=int(
            (7_468_022_368 + 3_015_939_072 + 853_119_712 + 253_806_246) * 1.1 + int(1.5 * GIB)
        ),
        text_encoder="cpu",
        vae="cpu",
        offload=True,
    )


EDIT_OUT_OF_MEMORY = (
    "Not enough memory for this size. Choose a smaller size or let LocalSR "
    "place the models automatically."
)


def fail_edit(monkeypatch, tmp_path, returncode, output):
    """The error a CUDA edit raises when its guard exits with ``returncode`` after
    ``output``, and the job's ``error_message`` for it."""
    from PIL import Image

    from localsr.core.disk_space import failure_fields
    from localsr.core.memory_errors import DeviceOutOfMemoryError

    image_path = tmp_path / "source.png"
    Image.new("RGB", (128, 96)).save(image_path)
    monkeypatch.setattr(image_edit, "check_runtime", lambda _: tmp_path / "sd-cli")
    monkeypatch.setattr(
        image_edit,
        "verify_bundle",
        lambda *_: {entry["role"]: Path(entry["filename"]) for entry in model()["files"]},
    )
    monkeypatch.setattr(image_edit, "resolve_edit_gpu", lambda _r, _d, _b: "CUDA0")
    (tmp_path / "scratch").mkdir()

    class OomProcess:
        def __init__(self, command, **_kwargs):
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(output)
            self.returncode = returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image_edit.subprocess, "Popen", OomProcess)
    req = {
        "job_id": "oom-edit",
        "model_id": "flux2_klein_4b_q4_0",
        "prompt": "Brighten",
        "device": "cuda:0",
        "max_dimension": 1024,
        "edit_allow_any_size": True,
        "runtime_path": str(tmp_path / "sd-cli"),
        "bundle_dir": str(tmp_path),
        "image_path": str(image_path),
        "output_path": str(tmp_path / "out.png"),
        "scratch_directory": str(tmp_path / "scratch"),
    }
    mem_8g = EditMemory(64 * GIB, 60 * GIB, gpu_total=8 * GIB, gpu_free=8 * GIB)
    with pytest.raises(DeviceOutOfMemoryError) as caught:
        image_edit.run_edit_job(req, threading.Event(), lambda _: None, sampler=lambda _: mem_8g)
    fields = failure_fields(caught.value, req["output_path"], edit_request=req)
    assert set(fields) == {"error_message"}
    return caught.value, fields["error_message"]


SUPERVISOR_LOW_MEMORY = "Editing stopped to protect system memory: only 1.0 GiB of memory was left."


@pytest.mark.parametrize(
    "returncode,output,expected",
    [
        (1, b"ggml_vulkan: Error OutOfDeviceMemory\n", EDIT_OUT_OF_MEMORY),
        (1, b"CUDA error: out of memory\n", EDIT_OUT_OF_MEMORY),
        (75, SUPERVISOR_LOW_MEMORY.encode() + b"\n", SUPERVISOR_LOW_MEMORY),
        # The guard's own admission check: this size no longer fits.
        (
            75,
            b"Available memory changed before loading. Close apps and retry.\n",
            EDIT_OUT_OF_MEMORY,
        ),
    ],
)
def test_run_edit_job_surfaces_oom_from_sd_cli_and_supervisor(
    monkeypatch, tmp_path, returncode, output, expected
):
    error, message = fail_edit(monkeypatch, tmp_path, returncode, output)
    assert str(error) == expected
    assert message == expected


def supervisor_stop(memory, plan=GPU_PLAN, used=0, **signals):
    with pytest.raises(MemoryError) as caught:
        check_edit_pressure(memory, plan, 0, used, **signals)
    return str(caught.value)


@pytest.mark.parametrize(
    "stop,says",
    [
        (
            lambda: supervisor_stop(replace(GPU_HOST, available_ram=3 * GIB)),
            "only 3.0 GiB of memory",
        ),
        (
            lambda: supervisor_stop(
                replace(GPU_HOST, available_ram=10 * GIB, swap_used=int(1.5 * GIB)),
                tight_swap=GIB // 2,
            ),
            "swapped 1.0 GiB to disk while only 10.0 GiB of memory was available",
        ),
        (
            lambda: supervisor_stop(replace(GPU_HOST, memory_stall=35.0), stall_seconds=12.0),
            "waited for memory 35% of the time for 12 s",
        ),
        (
            lambda: supervisor_stop(GPU_HOST, used=23 * GIB),
            "the editor used 23.0 GiB, more than its 22.0 GiB limit. Choose a smaller size",
        ),
        (
            lambda: supervisor_stop(
                replace(MAC_RUNNING, pressure="critical"), MAC_PLAN, critical_samples=2
            ),
            "macOS reported critical memory pressure",
        ),
        (
            lambda: supervisor_stop(
                replace(
                    MAC_RUNNING, swap_used=3 * GIB, disk_free=6 * 10**9, disk_reserve=8 * 10**9
                ),
                MAC_PLAN,
            ),
            "Free up disk space",
        ),
    ],
    ids=["low-memory", "disk-swap", "memory-stall", "process-limit", "mac-critical", "mac-disk"],
)
def test_each_supervisor_stop_tells_the_user_what_happened(monkeypatch, tmp_path, stop, says):
    from localsr.core.memory_errors import DeviceOutOfMemoryError

    line = stop()
    error, message = fail_edit(monkeypatch, tmp_path, 75, f"sd-cli log\n{line}\n".encode())
    assert isinstance(error, DeviceOutOfMemoryError)
    assert message == line
    assert says in message
    assert "for this size" not in message


def test_run_edit_job_emits_log_note_when_vram_limit_ignored_on_unified(monkeypatch, tmp_path):
    from PIL import Image

    image_path = tmp_path / "source.png"
    Image.new("RGB", (128, 96)).save(image_path)
    monkeypatch.setattr(image_edit, "check_runtime", lambda _: tmp_path / "sd-cli")
    monkeypatch.setattr(
        image_edit,
        "verify_bundle",
        lambda *_: {entry["role"]: Path(entry["filename"]) for entry in model()["files"]},
    )
    (tmp_path / "scratch").mkdir()

    class OkProcess:
        def __init__(self, command, **_kwargs):
            native = json.loads(Path(command[-1]).read_text())["command"]
            Image.new("RGB", (128, 96), "green").save(native[native.index("-o") + 1])
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"")
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image_edit.subprocess, "Popen", OkProcess)
    messages = []
    req = {
        "job_id": "mac-ignore-vram",
        "model_id": "flux2_klein_4b_q4_0",
        "prompt": "Brighten",
        "device": "mps",
        "edit_vram_limit_mb": 4096,
        "runtime_path": str(tmp_path / "sd-cli"),
        "bundle_dir": str(tmp_path),
        "image_path": str(image_path),
        "output_path": str(tmp_path / "out.png"),
        "scratch_directory": str(tmp_path / "scratch"),
    }
    mac_16 = EditMemory(16 * GIB, 14 * GIB, unified=True)
    image_edit.run_edit_job(req, threading.Event(), messages.append, sampler=lambda _: mac_16)
    logs = [
        json.loads(m.to_json())["data"]["message"]
        for m in messages
        if json.loads(m.to_json())["type"] == "log"
    ]
    assert any("edit_vram_limit_mb" in line for line in logs)


def test_allow_any_size_does_not_lift_the_mac_limits():
    # An oversized edit in unified memory would swap the whole Mac, so the limits hold.
    qwen_q2 = model("Q2_K", "qwen-image-edit-2511")
    mac_24 = EditMemory(24 * GIB, 20 * GIB, unified=True)
    assert plan_edit(qwen_q2, mac_24, 1024, allow_any_size=True) == plan_edit(qwen_q2, mac_24, 1024)
    assert plan_edit(qwen_q2, mac_24, 1024, allow_any_size=True).max_dimension == 512
