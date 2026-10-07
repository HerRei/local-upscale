from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from localsr.core import image_edit
from localsr.core.edit_memory import GIB, EditMemory, EditPlan, check_edit_pressure, plan_edit
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
    assert tiers["qwen_edit_2511_q2_k"] > 16, "the 20B editor stays off 16 GB Macs"


@pytest.mark.parametrize("selected", image_edit.edit_catalog(), ids=lambda m: m["model_id"])
def test_gpu_profiles_allow_driver_overhead_and_keep_vram_reserve(selected):
    capacity = selected["min_vram_gb"]
    memory = EditMemory(
        64 * GIB, 60 * GIB, gpu_total=capacity * GIB - 32 * 1024**2, gpu_free=(capacity - 1) * GIB
    )
    plan = plan_edit(selected, memory, 1024)
    assert 4 * GIB <= plan.gpu_budget <= memory.gpu_free - 2 * GIB
    assert plan.max_dimension == (512 if capacity < 12 else 768 if capacity < 16 else 1024)
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

    assert 0 < edit_guard.process_memory(psutil.Process()) <= psutil.Process().memory_info().rss * 2


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
MAC_RUNNING = EditMemory(24 * GIB, 4 * GIB, 0, unified=True, pressure="low", obtainable_ram=6 * GIB)


@pytest.mark.parametrize(
    "change,used,warning_seconds,critical_samples,reason",
    [
        ({"pressure": "critical"}, 0, 1.0, 2, "critical memory pressure"),
        ({"swap_used": 3 * GIB}, 0, 0.0, 0, "swapped 3.0 GiB"),
        ({"pressure": "warning", "swap_used": GIB}, 0, 31.0, 0, "stayed high for 31 s"),
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


@pytest.mark.parametrize(
    "change",
    [{"available_ram": 2 * GIB}, {"swap_used": GIB}, {"pressure": "warning"}],
)
def test_gpu_host_supervisor_keeps_its_reserve(change):
    initial = EditMemory(64 * GIB, 60 * GIB, gpu_total=16 * GIB, gpu_free=12 * GIB)
    plan = EditPlan(16 * GIB, 4 * GIB, 10 * GIB, 512, 16 * GIB)
    with pytest.raises(MemoryError, match="protect system memory"):
        check_edit_pressure(replace(initial, **change), plan, 0, 0)


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


@pytest.mark.parametrize(
    "family, quant,gpu,backend",
    [
        ("qwen-image-edit-2511", "Q2_K", "mps", "MPS"),
        ("qwen-image-edit-2511", "Q4_K_M", "cuda:0", "CUDA"),
        ("qwen-image-edit-2511", "Q2_K", "cuda:1", "VULKAN"),
        ("flux2-klein-4b", "Q4_0", "mps", "MPS"),
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
    assert command[command.index("--backend") + 1].startswith(
        {"MPS": "diffusion=MTL0", "CUDA": "diffusion=CUDA0", "VULKAN": "diffusion=Vulkan1"}[backend]
    )
    if family == "flux2-klein-4b":
        # Distilled: guidance 1, no vision projector, no model arguments.
        assert "--model-args" not in command and "--llm_vision" not in command
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


def test_catalog_offers_only_openly_licensed_bundles():
    assert {m["license_name"] for m in image_edit.edit_catalog()} == {"Apache-2.0"}
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
