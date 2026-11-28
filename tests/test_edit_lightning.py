"""Lightning edits: the 4-step speed add-on of Qwen Image Edit 2511, from catalog to command."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import subprocess
import threading
from pathlib import Path

import pytest

from localsr.core import edit_memory, image_edit
from localsr.core.edit_memory import GIB, EditMemory, estimate_edit_memory, plan_edit
from localsr.protocol.messages import EditJobRequest
from localsr.worker.request_validation import InvalidRequest, validate_edit_job

LORA_NAME = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"


def qwen(quant="Q2_K"):
    return next(
        m
        for m in image_edit.edit_catalog()
        if m["family"] == "qwen-image-edit-2511" and m["quantization"] == quant
    )


def lightning():
    addon = image_edit.speed_addon("qwen-image-edit-2511", "lightning")
    assert addon is not None
    return addon


def test_catalog_lists_the_lora_as_an_add_on_and_never_as_an_edit_model():
    raw = json.loads(image_edit.CATALOG_PATH.read_text(encoding="utf-8"))
    addon = lightning()
    assert addon in raw
    assert addon["model_id"] == "qwen_edit_2511_lightning_4step"
    assert addon["kind"] == "speed_addon" and addon["speed"] == "lightning"
    assert addon["model_id"] not in {m["model_id"] for m in image_edit.edit_catalog()}
    assert all("kind" not in m for m in image_edit.edit_catalog())
    assert len(image_edit.edit_catalog()) == len(raw) - 1
    (lora,) = addon["files"]
    assert lora == {
        "role": "lora",
        "filename": LORA_NAME,
        "size_bytes": 849_608_296,
        "sha256": "22226e8d05d354bb356627d428809f5afd7819399b077238a2b70a82883a904f",
        "download_url": (
            "https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/"
            f"107d238d68b8387e32843d0b4a2e24ceb0d3ad86/{LORA_NAME}"
        ),
    }
    assert (addon["default_steps"], addon["cfg_scale"]) == (4, 1.0)
    assert addon["license_name"] == "Apache-2.0"
    assert addon["commercial_use_allowed"] and addon["automated_download_allowed"]
    assert not addon["terms_acceptance_required"]
    # Only the Qwen 2511 family has one.
    assert image_edit.speed_addon("flux2-klein-4b", "lightning") is None
    assert image_edit.speed_addon("qwen-image-edit-2511", "full") is None


def test_the_add_on_cannot_be_requested_as_the_edit_model():
    with pytest.raises(ValueError, match="Unknown editing model"):
        image_edit.validate_request(
            {"model_id": "qwen_edit_2511_lightning_4step", "prompt": "Brighten it."}
        )


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("Make it sunny <lora:qwen_image_vae:1>", "Make it sunny "),
        ("<LoRA:/etc/passwd:0.5>Brighten", "Brighten"),
        ("Warmer <lora:other:1", "Warmer other:1"),
        # A tag start the removal of another one assembles is removed as well.
        ("<lo<lora:x:1>ra:y:1> sky", "y:1> sky"),
        ("<lor<lora:a:", ""),
        ("Plain text with <b> and lora: words", "Plain text with <b> and lora: words"),
    ],
)
def test_lora_tags_are_stripped_from_the_instruction(prompt, expected):
    cleaned = image_edit.strip_lora_tags(prompt)
    assert cleaned == expected
    assert "<lora:" not in cleaned.lower()


def command(selected, files, prompt, steps, cfg_scale=None):
    plan = edit_memory.EditPlan(16 * GIB, 4 * GIB, 6 * GIB, 512, 16 * GIB)
    return image_edit.build_edit_command(
        "/runtime/sd-cli",
        selected,
        files,
        "reference.png",
        "output.png",
        prompt,
        plan,
        "mps",
        steps,
        42,
        (512, 384),
        backend="MPS",
        cfg_scale=cfg_scale,
    )


def test_lightning_command_adds_the_lora_folder_its_tag_guidance_and_steps():
    selected = qwen()
    folder = Path("/models/qwen-image-edit-2511")
    files = {f["role"]: folder / f["filename"] for f in selected["files"]}
    files["lora"] = folder / LORA_NAME
    args = command(selected, files, "Remove the scratches <lora:qwen_image_vae:1>", 4, 1.0)
    assert args[args.index("-p") + 1] == f"Remove the scratches  <lora:{LORA_NAME}:1>"
    assert args[args.index("--lora-model-dir") + 1] == str(folder)
    assert args[args.index("--lora-apply-mode") + 1] == "auto"
    assert args[args.index("--cfg-scale") + 1] == "1"
    assert args[args.index("--steps") + 1] == "4"
    # The model's own Qwen arguments stay.
    assert args[args.index("--flow-shift") + 1] == "3"
    assert args[args.index("--model-args") + 1] == "qwen_image_zero_cond_t=true"


def test_full_command_has_no_lora_options_and_strips_tags_too():
    selected = qwen()
    files = {f["role"]: Path("/m") / f["filename"] for f in selected["files"]}
    args = command(selected, files, "Remove the scratches <lora:qwen_image_vae:1>", 40)
    assert args[args.index("-p") + 1] == "Remove the scratches "
    assert "--lora-model-dir" not in args and "--lora-apply-mode" not in args
    assert args[args.index("--cfg-scale") + 1] == "2.5"
    assert args[args.index("--steps") + 1] == "40"


def request_data(**changes):
    data = {
        "job_id": "job",
        "image_path": "/in/photo.png",
        "output_path": "/out/photo_edit.png",
        "model_id": "qwen_edit_2511_q2_k",
        "bundle_dir": "/models/qwen-image-edit-2511",
        "runtime_path": "/bin/sd-cli",
        "prompt": "Remove the scratches.",
        "device": "mps",
        "max_dimension": 512,
        "steps": 40,
        "seed": 42,
        "accepted_terms": False,
    }
    data.update(changes)
    return data


def test_requests_without_speed_stay_valid_and_mean_full():
    validate_edit_job(request_data())
    assert EditJobRequest(*["x"] * 8).speed == "full"
    assert json.loads(EditJobRequest(*["x"] * 8).to_json())["data"]["speed"] == "full"
    validate_edit_job(request_data(speed="full"))
    validate_edit_job(
        request_data(speed="lightning", lightning_lora_path=f"/models/qwen/{LORA_NAME}")
    )
    with pytest.raises(InvalidRequest, match="lightning_lora_path"):
        validate_edit_job(request_data(speed="lightning"))
    with pytest.raises(InvalidRequest, match='"speed"'):
        validate_edit_job(request_data(speed="turbo"))
    with pytest.raises(InvalidRequest, match="lightning_lora_path"):
        validate_edit_job(request_data(speed="lightning", lightning_lora_path=""))


def test_worker_refuses_a_lightning_request_it_cannot_run():
    with pytest.raises(ValueError, match="Lightning file, which is not downloaded"):
        image_edit.validate_request(request_data(speed="lightning"))
    with pytest.raises(ValueError, match="Quick is not available"):
        image_edit.validate_request(
            request_data(
                model_id="flux2_klein_4b_q4_0",
                speed="lightning",
                lightning_lora_path=f"/m/{LORA_NAME}",
            )
        )
    with pytest.raises(ValueError, match="speed must be"):
        image_edit.validate_request(request_data(speed="turbo"))
    with pytest.raises(ValueError, match="Describe the edit"):
        image_edit.validate_request(request_data(prompt=" <lora:x:1> "))
    assert image_edit.validate_request(request_data()) == qwen()


def test_engine_lists_the_lightning_feature():
    from localsr.worker.server import _engine_features

    assert "edit_lightning" in _engine_features([])


def test_q2k_estimates_follow_the_measured_peaks_and_count_the_lora_only_with_lightning():
    full = estimate_edit_memory(qwen(), True, 512)
    fast = estimate_edit_memory(edit_memory.with_addon(qwen(), lightning()), True, 512)
    # 7.31 GiB at 40 steps and 9.00 GiB with the LoRA, each plus 2%.
    assert full == int(int(7.31 * GIB) * 1.02)
    assert fast == int(int(9.00 * GIB) * 1.02)
    assert 9.1 * GIB < fast < 9.25 * GIB
    # A 768 px edit on a bigger Mac adds its larger working buffers.
    assert estimate_edit_memory(qwen(), True, 768) == full + int(
        1.1 * (edit_memory.activation_memory(True, 768) - edit_memory.activation_memory(True, 512))
    )


def test_unmeasured_bundles_add_the_lora_footprint_only_with_lightning():
    q4 = qwen("Q4_K_M")
    with_lora = edit_memory.with_addon(q4, lightning())
    extra = estimate_edit_memory(with_lora, True, 512) - estimate_edit_memory(q4, True, 512)
    assert extra == pytest.approx(849_608_296 * edit_memory.LORA_UNIFIED_SHARE * 1.1, abs=2)
    # Beside a dedicated GPU the card holds the LoRA: host memory is unchanged.
    assert estimate_edit_memory(with_lora, False, 512) == estimate_edit_memory(q4, False, 512)
    gpu = EditMemory(64 * GIB, 60 * GIB, gpu_total=16 * GIB, gpu_free=15 * GIB)
    assert plan_edit(with_lora, gpu).process_limit > plan_edit(q4, gpu).process_limit


def test_16gb_mac_admits_q2k_with_and_without_lightning_and_smaller_macs_refuse():
    assert qwen()["min_unified_memory_gb"] == 16
    idle = EditMemory(16 * GIB, int(16 * 0.70 * GIB), unified=True)
    full = plan_edit(qwen(), idle)
    fast = plan_edit(edit_memory.with_addon(qwen(), lightning()), idle)
    assert full.required_ram < fast.required_ram <= idle.free_ram - fast.reserve_ram
    assert fast.max_dimension == 512
    # The measured run's 9.0 GiB footprint stays under the supervisor's process limit.
    assert fast.process_limit > 9.0 * GIB
    with pytest.raises(MemoryError, match="smaller"):
        plan_edit(qwen(), EditMemory(15 * GIB, 15 * GIB, unified=True))
    # The measured batch on the busy 16 GB Mac: 9.1 GiB free was refused at 9.6 GiB.
    busy = EditMemory(16 * GIB, int(11.1 * GIB), unified=True)
    with pytest.raises(MemoryError, match="currently available"):
        plan_edit(edit_memory.with_addon(qwen(), lightning()), busy)


@pytest.mark.parametrize("quant", ["Q2_K", "Q3_K_S", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0"])
def test_every_qwen_bundle_admits_lightning_at_its_mac_tier(quant):
    selected = qwen(quant)
    capacity = selected["min_unified_memory_gb"]
    memory = EditMemory(capacity * GIB, int(capacity * 0.70 * GIB), unified=True)
    plan = plan_edit(edit_memory.with_addon(selected, lightning()), memory)
    assert plan.required_ram + plan.reserve_ram <= memory.free_ram


def write_lora(folder: Path, content: bytes = b"lora weights") -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / LORA_NAME).write_bytes(content)
    addon = dict(lightning())
    addon["files"] = [
        {
            **lightning()["files"][0],
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    ]
    return addon


def test_add_on_file_is_checked_by_name_size_and_checksum(tmp_path):
    addon = write_lora(tmp_path / "models")
    cancel = threading.Event()
    path = tmp_path / "models" / LORA_NAME
    assert image_edit.verify_speed_addon(addon, path, cancel) == path
    other = tmp_path / "models" / "qwen_image_vae.safetensors"
    other.write_bytes(b"lora weights")
    with pytest.raises(ValueError, match="not the catalog's"):
        image_edit.verify_speed_addon(addon, other, cancel)
    with pytest.raises(FileNotFoundError, match="Lightning file is missing"):
        image_edit.verify_speed_addon(addon, tmp_path / "elsewhere" / LORA_NAME, cancel)
    path.write_bytes(b"lora weighty")
    with pytest.raises(ValueError, match="checksum failed"):
        image_edit.verify_speed_addon(addon, path, cancel)


def test_runtime_without_lora_options_cannot_run_lightning(tmp_path, monkeypatch):
    monkeypatch.setattr(image_edit, "_CHECKED_RUNTIMES", set())
    monkeypatch.setattr(image_edit, "_LORA_RUNTIMES", set())
    runtime = tmp_path / "sd-cli"
    runtime.write_bytes(b"runtime")
    listing = [" ".join(image_edit.REQUIRED_FLAGS)]
    monkeypatch.setattr(
        image_edit,
        "probe_runtime",
        lambda path, option: subprocess.CompletedProcess([str(path), option], 0, listing[0], ""),
    )
    checked = image_edit.check_runtime(str(runtime))
    with pytest.raises(ValueError, match="cannot run Quick"):
        image_edit.require_lora_support(checked)
    runtime.write_bytes(b"runtime with LoRA options")
    listing[0] += " " + " ".join(image_edit.LORA_FLAGS)
    image_edit.require_lora_support(image_edit.check_runtime(str(runtime)))


def run_fixture_edit(monkeypatch, tmp_path, **changes):
    """Run an edit through run_edit_job with a stand-in guard; returns the native command,
    the plan the guard got and the messages."""
    from PIL import Image

    addon = write_lora(tmp_path / "models")
    monkeypatch.setattr(
        image_edit,
        "speed_addon",
        lambda family, speed: addon if family == "qwen-image-edit-2511" else None,
    )
    data = request_data(
        runtime_path=str(tmp_path / "sd-cli"),
        bundle_dir=str(tmp_path / "models"),
        image_path=str(tmp_path / "source.png"),
        output_path=str(tmp_path / "output.png"),
        scratch_directory=str(tmp_path / "scratch"),
        **changes,
    )
    (tmp_path / "scratch").mkdir()
    Image.new("RGB", (128, 96)).save(data["image_path"])
    monkeypatch.setattr(image_edit, "check_runtime", lambda _: tmp_path / "sd-cli")
    supported = []
    monkeypatch.setattr(image_edit, "require_lora_support", supported.append)
    monkeypatch.setattr(
        image_edit,
        "verify_bundle",
        lambda bundle, directory, *_: {
            entry["role"]: Path(directory) / entry["filename"] for entry in bundle["files"]
        },
    )
    seen = {}

    class FixtureProcess:
        def __init__(self, guard_command, **_kwargs):
            spec = json.loads(Path(guard_command[-1]).read_text())
            seen.update(spec)
            native = spec["command"]
            Image.new("RGB", (128, 96), "red").save(native[native.index("-o") + 1])
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"fixture log\n")
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image_edit.subprocess, "Popen", FixtureProcess)
    messages = []
    memory = EditMemory(16 * GIB, int(16 * 0.70 * GIB), unified=True)
    image_edit.run_edit_job(data, threading.Event(), messages.append, sampler=lambda _: memory)
    return seen, messages, supported


def test_lightning_edit_runs_four_steps_with_the_host_named_lora(monkeypatch, tmp_path):
    lora = tmp_path / "models" / LORA_NAME
    seen, messages, supported = run_fixture_edit(
        monkeypatch,
        tmp_path,
        speed="lightning",
        lightning_lora_path=str(lora),
        prompt="Remove the scratches <lora:qwen_image_vae:1>",
    )
    native = seen["command"]
    assert native[native.index("--steps") + 1] == "4"
    assert native[native.index("--cfg-scale") + 1] == "1"
    assert native[native.index("--lora-model-dir") + 1] == str(lora.parent)
    assert native[native.index("-p") + 1] == f"Remove the scratches <lora:{LORA_NAME}:1>"
    assert supported == [tmp_path / "sd-cli"]
    # Admission counted the LoRA: the measured Q2_K peak with Lightning, plus 2%.
    assert seen["plan"]["required_ram"] == int(int(9.00 * GIB) * 1.02)
    logs = [m.message for m in messages if type(m).__name__ == "LogMessage"]
    assert any("Lightning runs 4 steps" in log and "40 steps" in log for log in logs)
    assert any("LoRA tags were removed" in log for log in logs)
    progress = [m for m in messages if type(m).__name__ == "ProgressUpdate"]
    assert progress and all(m.total_tiles == 4 for m in progress)
    done = json.loads(messages[-1].to_json())
    assert done["type"] == "job_completed"
    assert done["data"]["notes"].endswith("Lightning: 4 steps.")


def test_request_without_speed_runs_the_full_bundle(monkeypatch, tmp_path):
    seen, messages, supported = run_fixture_edit(monkeypatch, tmp_path)
    native = seen["command"]
    assert native[native.index("--steps") + 1] == "40"
    assert native[native.index("--cfg-scale") + 1] == "2.5"
    assert "--lora-model-dir" not in native
    assert all(LORA_NAME not in part for part in native)
    assert supported == []
    assert seen["plan"]["required_ram"] == int(int(7.31 * GIB) * 1.02)
    assert not [m for m in messages if type(m).__name__ == "LogMessage"]


def test_lightning_without_a_named_lora_fails_before_any_work(monkeypatch, tmp_path):
    monkeypatch.setattr(
        image_edit, "check_runtime", lambda _: pytest.fail("Runtime must not start")
    )
    with pytest.raises(ValueError, match="Lightning file, which is not downloaded"):
        image_edit.run_edit_job(
            request_data(speed="lightning", output_path=str(tmp_path / "out.png")),
            threading.Event(),
            lambda _: None,
            sampler=lambda _: pytest.fail("Memory must not be sampled"),
        )
    assert not (tmp_path / "out.png").exists()


def test_protocol_dataclass_carries_the_lightning_fields():
    names = {field.name for field in dataclasses.fields(EditJobRequest)}
    assert {"speed", "lightning_lora_path"} <= names
