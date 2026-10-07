"""Image editing in a disposable native GGUF process, with guarded admission."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageOps

from localsr.core.edit_memory import GIB, plan_edit, read_edit_memory
from localsr.core.image_io import open_image
from localsr.protocol.messages import JobCompleted, ProgressUpdate, StageStarted
from localsr.worker.edit_guard import native_environment, stop_process

CATALOG_PATH = Path(__file__).with_name("edit_catalog.json")
REQUIRED_FLAGS = ("--max-vram", "--mmap", "--model-args", "--backend", "--vae-tiling")


def probe_runtime(runtime: Path, option: str):
    command = [str(runtime), option]
    if os.name == "nt" and getattr(sys, "frozen", False):
        # Isolate PyInstaller's DLL-search reset from the long-lived worker.
        command = [sys.executable, "--edit-probe", *command]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30 if option == "--list-devices" else 10,
        env=native_environment(),
        creationflags=0x08000000 if os.name == "nt" else 0,
    )


def match_vulkan_device(devices: str, selected_name: str) -> str:
    def normalize(name):
        # Mesa may append a driver description to the actual card name.
        name = re.sub(r"\([^)]*\)", "", name)
        return " ".join(re.findall(r"[a-z0-9]+", name.lower()))

    matches = []
    for line in devices.splitlines():
        identifier, separator, description = line.partition("\t")
        if separator and re.fullmatch(r"Vulkan\d+", identifier):
            if normalize(description) == normalize(selected_name):
                matches.append(identifier)
    if len(matches) != 1:
        raise ValueError(
            "The Vulkan runtime cannot uniquely identify the selected GPU. "
            "Use a CUDA/ROCm native runtime, or expose only that Vulkan GPU "
            "with GGML_VK_VISIBLE_DEVICES. No model was loaded."
        )
    return matches[0]


def resolve_edit_gpu(runtime: Path, device: str, backend: str) -> str:
    if device == "mps":
        return "MTL0"
    import torch

    index = int(device.partition(":")[2]) if ":" in device else torch.cuda.current_device()
    if backend == "VULKAN":
        listing = probe_runtime(runtime, "--list-devices")
        if listing.returncode != 0:
            raise ValueError("The native Vulkan runtime cannot enumerate GPUs. Check the driver.")
        return match_vulkan_device(listing.stdout, torch.cuda.get_device_name(index))
    return f"{'ROCm' if torch.version.hip else 'CUDA'}{index}"


def edit_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def validate_request(data: dict) -> dict:
    model = next((m for m in edit_catalog() if m["model_id"] == data.get("model_id")), None)
    if model is None:
        raise ValueError("Unknown editing model.")
    prompt = data.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000 or "\0" in prompt:
        raise ValueError("Describe the edit in 1–4000 characters.")
    if data.get("max_dimension", 512) not in (512, 768, 1024):
        raise ValueError("Edit size must be 512, 768 or 1024 pixels.")
    steps = data.get("steps") or model.get("default_steps", 40)
    if not isinstance(steps, int) or not 1 <= steps <= 60:
        raise ValueError("Editing steps must be between 1 and 60.")
    seed = data.get("seed", 42)
    if not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
        raise ValueError("Seed must be between 0 and 2147483647.")
    if model["terms_acceptance_required"] and data.get("accepted_terms") is not True:
        raise ValueError("Review and accept this model's license before editing.")
    return model


class EditProgress:
    """Turns the native runtime's log into a phase label and a moving percentage.

    Measured with FLUX.2 klein 4B at 512 px on a 16 GB M1 Pro (127 s): the photo
    and the instruction take about 20 s each without a counter, the four
    sampling steps 35 s, and the tiled decode 48 s in six parts. Phases without a
    counter creep forward with time so the bar never stands still.
    """

    PHASES = (
        ("Checking the model files", 0.04),
        ("Loading the model", 0.03),
        ("Reading the photo", 0.14),
        ("Reading your instruction", 0.14),
        ("Editing", 0.38),
        ("Finishing the image", 0.24),
        ("Saving", 0.03),
    )
    # Log markers that start each phase after the first two.
    MARKERS = (
        (2, "loading tensors completed"),
        (3, "encode_first_stage completed"),
        (4, "get_learned_condition completed"),
        (5, "sampling completed"),
        (6, "decode_first_stage completed"),
    )

    def __init__(self, steps: int, clock=time.monotonic):
        self.steps = steps
        self.clock = clock
        self.phase = 0
        self.since = clock()
        self.count = (0, 0)
        self.completed_steps = 0
        self.fraction = 0.0
        self.shown = 0.0

    def enter(self, phase: int) -> None:
        if phase > self.phase:
            self.phase, self.since, self.count = phase, self.clock(), (0, 0)

    def checked(self, fraction: float) -> None:
        self.fraction = max(self.fraction, min(1.0, fraction))

    def feed(self, line: str) -> None:
        for phase, marker in self.MARKERS:
            if marker in line and (phase != 2 or self.phase == 1):
                self.enter(phase)
        # Compute bars end in s/it or it/s (sampling steps, VAE tiles); tensor
        # loading prints its own "149/149 - 0.07MB/s" bars, which are not progress.
        match = re.search(r"\|\s*(\d+)\s*/\s*(\d+)\s*-\s*[\d.]+\s*(?:s/it|it/s)", line)
        if not match or int(match[2]) <= 0:
            return
        done, total = int(match[1]), int(match[2])
        if self.phase == 4 and total == self.steps:
            self.count = (done, total)
            self.completed_steps = done
        elif self.phase in (2, 5):
            self.count = (done, total)

    def snapshot(self) -> tuple[float, str]:
        """(percentage, label) for the current moment."""
        label, share = self.PHASES[self.phase]
        done, total = self.count
        if self.phase == 0:
            within = self.fraction
        elif total:
            within = done / total
        else:
            # About two thirds of the phase after 15 s, never quite all of it.
            within = 0.9 * (1 - math.exp(-(self.clock() - self.since) / 15))
        before = sum(weight for _, weight in self.PHASES[: self.phase])
        # Never move the bar backwards.
        self.shown = max(self.shown, min(99.0, 100 * (before + share * within)))
        percentage = self.shown
        if self.phase == 4 and total:
            label = f"Editing · step {done} of {total}"
        elif self.phase == 5 and total:
            label = f"Finishing the image · part {done} of {total}"
        return percentage, label


def verify_bundle(
    model: dict, directory: Path, cancel: threading.Event, progress=None
) -> dict[str, Path]:
    paths = {}
    total = sum(entry["size_bytes"] for entry in model["files"]) or 1
    hashed = 0
    for entry in model["files"]:
        path = directory / entry["filename"]
        if not path.is_file() or path.stat().st_size != entry["size_bytes"]:
            raise FileNotFoundError(
                "The editing model is incomplete. Download all components again."
            )
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024**2):
                if cancel.is_set():
                    raise InterruptedError("Editing cancelled.")
                digest.update(block)
                hashed += len(block)
                if progress is not None:
                    progress(hashed / total)
        if digest.hexdigest() != entry["sha256"]:
            raise ValueError(f"Editing model checksum failed: {path.name}. Download it again.")
        paths[entry["role"]] = path
    return paths


def check_runtime(path: str) -> Path:
    runtime = Path(path).resolve(strict=True)
    if not runtime.is_file():
        raise ValueError("Select the native sd-cli editing runtime.")
    help_result = probe_runtime(runtime, "--help")
    if help_result.returncode != 0 or any(
        flag not in help_result.stdout + help_result.stderr for flag in REQUIRED_FLAGS
    ):
        raise ValueError("The editing runtime is too old. Reinstall LocalSR to restore it.")
    return runtime


def edit_dimensions(width: int, height: int, limit: int) -> tuple[int, int]:
    ratio = min(1.0, limit / max(width, height))
    return (
        max(32, math.floor(width * ratio / 32) * 32),
        max(32, math.floor(height * ratio / 32) * 32),
    )


def build_edit_command(
    runtime,
    model,
    files,
    reference,
    output,
    prompt,
    plan,
    device,
    steps,
    seed,
    dimensions,
    rocm=False,
    backend="",
    gpu_override=None,
):
    gpu_kind = "Vulkan" if backend == "VULKAN" else "ROCm" if rocm else "CUDA"
    gpu = gpu_override or (
        "MTL0" if device == "mps" else f"{gpu_kind}{device.partition(':')[2] or '0'}"
    )
    width, height = dimensions
    args = [
        str(runtime),
        "--diffusion-model",
        str(files["diffusion"]),
        "--llm",
        str(files["text_encoder"]),
        *(["--llm_vision", str(files["vision"])] if "vision" in files else []),
        "--vae",
        str(files["vae"]),
        "-r",
        str(reference),
        "-o",
        str(output),
        "-p",
        prompt,
        "--steps",
        str(steps),
        "--seed",
        str(seed),
        "-W",
        str(width),
        "-H",
        str(height),
        "--sampling-method",
        "euler",
        "--offload-to-cpu",
        "--mmap",
        "--disable-prefetch",
        "--fa",
        "--vae-tiling",
        "--vae-tile-size",
        "256",
        "--threads",
        "2",
        "--conditioning-cache-size",
        "0",
        "--backend",
        f"diffusion={gpu},te=cpu,vae=cpu",
        "--max-vram",
        f"{plan.gpu_budget / GIB:.2f}",
        "--cfg-scale",
        f"{model.get('cfg_scale', 2.5):g}",
    ]
    if model["family"] == "qwen-image-edit-2511":
        args += ["--flow-shift", "3", "--model-args", "qwen_image_zero_cond_t=true"]
    # FLUX.2 klein is distilled: guidance 1.0, a handful of steps, no model arguments.
    return args


def run_edit_job(data: dict, cancel: threading.Event, emit, *, sampler=read_edit_memory):
    started = time.monotonic()
    model = validate_request(data)
    steps = int(data.get("steps") or model.get("default_steps", 40))
    device = str(data.get("device", "mps"))
    initial = sampler(device)
    plan = plan_edit(model, initial, data.get("max_dimension", 512))
    if cancel.is_set():
        raise InterruptedError("Editing cancelled.")
    runtime = check_runtime(str(data.get("runtime_path", "")))
    job_id = str(data["job_id"])
    emit(StageStarted(job_id, 0, 1, "edit", model["name"]))
    tracker = EditProgress(steps)
    last_report = [0.0]

    def report(force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - last_report[0] < 0.5:
            return
        last_report[0] = now
        percentage, label = tracker.snapshot()
        emit(
            ProgressUpdate(
                job_id,
                tracker.completed_steps,
                steps,
                percentage,
                now - started,
                0.0,
                0,
                system_ram_available=sampler(device).available_ram,
                unit="steps",
                detail=label,
            )
        )

    report(force=True)

    def checked(fraction: float) -> None:
        tracker.checked(fraction)
        report()

    files = verify_bundle(model, Path(data["bundle_dir"]), cancel, checked)
    tracker.enter(1)
    # Re-admit after the potentially long checksum pass, without trusting stale free RAM.
    initial = sampler(device)
    plan = plan_edit(model, initial, data.get("max_dimension", 512))
    output = Path(data["output_path"])
    if output.exists():
        raise FileExistsError("The edit result already exists; choose a new output filename.")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = data.get("output_temporary_directory") or data.get("scratch_directory")
    with tempfile.TemporaryDirectory(prefix="localsr-edit-", dir=scratch or output.parent) as work:
        work = Path(work)
        reference, result = work / "reference.png", work / "result.png"
        with open_image(data["image_path"]) as source:
            if source.width * source.height > 100_000_000:
                raise ValueError("The source image is too large for a safe edit.")
            image = ImageOps.exif_transpose(source)
            dimensions = edit_dimensions(image.width, image.height, plan.max_dimension)
            small = image.resize(dimensions, Image.Resampling.LANCZOS).convert("RGB")
            small.save(reference)
            small.close()
            image.close()
        metadata = runtime.parent / "runtime.json"
        backend = (
            json.loads(metadata.read_text(encoding="utf-8")).get("backend", "")
            if metadata.is_file()
            else ""
        )
        command = build_edit_command(
            runtime,
            model,
            files,
            reference,
            result,
            data["prompt"].strip(),
            plan,
            device,
            steps,
            data.get("seed", 42),
            dimensions,
            backend=backend,
            gpu_override=resolve_edit_gpu(runtime, device, backend),
        )
        specification = work / "guard.json"
        specification.write_text(
            json.dumps(
                {
                    "command": command,
                    "plan": asdict(plan),
                    "unified": initial.unified,
                    "swap_used": initial.swap_used,
                }
            ),
            encoding="utf-8",
        )
        guard_command = [sys.executable]
        if not getattr(sys, "frozen", False):
            guard_command += ["-m", "localsr.worker"]
        guard_command += ["--edit-guard", str(specification)]
        logs = deque(maxlen=12)
        process = subprocess.Popen(
            guard_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )

        def read_logs():
            buffer = ""
            while chunk := process.stdout.read(1):
                character = chunk.decode("utf-8", errors="replace")
                if character in "\r\n":
                    if buffer:
                        logs.append(buffer[-400:])
                        tracker.feed(buffer)
                    buffer = ""
                elif len(buffer) < 4096:
                    buffer += character

        reader = threading.Thread(target=read_logs, daemon=True)
        reader.start()
        try:
            while process.poll() is None:
                if cancel.wait(0.5):
                    process.stdin.close()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        stop_process(process)
                    raise InterruptedError("Editing cancelled.")
                report(force=True)
            reader.join(timeout=1)
            if process.returncode == 130 or cancel.is_set():
                raise InterruptedError("Editing cancelled.")
            if process.returncode != 0:
                # The guard explains a memory stop in its last line; show that, not
                # the runtime's log, which only says where the model had got to.
                guard = [line for line in logs if line.startswith("Editing stopped")]
                if process.returncode == 75 and guard:
                    raise MemoryError(guard[-1])
                detail = "\n".join(logs)
                raise RuntimeError(f"Editing stopped (exit {process.returncode}). {detail}")
            if not result.is_file():
                raise RuntimeError("The editing runtime returned no image.")
            with Image.open(result) as image:
                image.verify()
            # Publish without overwriting an existing result, even in a filename race.
            os.link(result, output)
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    stop_process(process)
            process.stdout.close()
        emit(
            JobCompleted(
                job_id, elapsed_seconds=time.monotonic() - started, output_path=str(output)
            )
        )
