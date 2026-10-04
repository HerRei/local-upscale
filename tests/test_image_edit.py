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
    assert plan.required_ram + plan.reserve_ram <= memory.available_ram
    assert plan.reserve_ram >= 4 * GIB
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


@pytest.mark.parametrize(
    "change,rss",
    [
        ({"available_ram": 2 * GIB}, 0),
        ({"swap_used": GIB}, 0),
        ({"pressure": "warning"}, 0),
        ({}, 17 * GIB),
    ],
)
def test_runtime_pressure_swap_and_process_growth_stop_editing(change, rss):
    initial = EditMemory(24 * GIB, 23 * GIB, unified=True)
    plan = EditPlan(16 * GIB, 4 * GIB, 10 * GIB, 512, 16 * GIB)
    with pytest.raises(MemoryError, match="protect system memory"):
        check_edit_pressure(replace(initial, **change), plan, 0, rss)


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
