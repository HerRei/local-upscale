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

from localsr.core.child_processes import native_environment, stop_process, track
from localsr.core.disk_space import edit_bytes, require_room
from localsr.core.edit_memory import (
    GIB,
    PLACEMENT_CHOICES,
    EditPlan,
    minimum_unified_memory_gb,
    minimum_vram_gb,
    plan_edit,
    read_edit_memory,
)
from localsr.core.image_formats import PILLOW_INPUT_FORMATS
from localsr.core.image_io import open_photo
from localsr.core.memory_errors import (
    EDIT_OUT_OF_MEMORY_MESSAGE,
    DeviceOutOfMemoryError,
    EditMemoryStop,
    is_edit_out_of_memory,
)
from localsr.core.output_publish import publish_output, require_output_name
from localsr.protocol.messages import JobCompleted, LogMessage, ProgressUpdate, StageStarted

CATALOG_PATH = Path(__file__).with_name("edit_catalog.json")
REQUIRED_FLAGS = ("--max-vram", "--mmap", "--model-args", "--backend", "--vae-tiling")
CUSTOM_EDIT_MODEL_ID = "__custom__"
CUSTOM_EDIT_FAMILIES: dict[str, dict] = {
    "qwen-image-edit-2511": {
        "roles": ("diffusion", "text_encoder", "vision", "vae"),
        "default_steps": 40,
        "cfg_scale": 2.5,
    },
    "qwen-image-2.1": {
        "roles": ("diffusion", "text_encoder", "vision", "vae"),
        "default_steps": 40,
        "cfg_scale": 6.0,
    },
    "flux2-klein": {
        "roles": ("diffusion", "text_encoder", "vae"),
        "default_steps": 4,
        "cfg_scale": 1.0,
    },
    "mage-flow-edit-turbo": {
        "roles": ("diffusion", "text_encoder", "vision", "vae"),
        "default_steps": 4,
        "cfg_scale": 1.0,
    },
}


PROBE_TIMEOUTS = {"--help": 90, "--list-devices": 120}
# Runtimes whose --help already passed, keyed by path, size and modification time.
_CHECKED_RUNTIMES: set[tuple[str, int, int]] = set()
# Bundle files whose checksum passed, keyed by resolved path. The worker is long-lived, so a
# 8-21 GB bundle is hashed once instead of on every edit.
_VERIFIED_BUNDLE_FILES: dict[str, tuple[tuple[int, int, int, int, int], str]] = {}


def probe_runtime(runtime: Path, option: str):
    command = [str(runtime), option]
    if os.name == "nt" and getattr(sys, "frozen", False):
        # Isolate PyInstaller's DLL-search reset from the long-lived worker.
        command = [sys.executable, "--edit-probe", *command]
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        # The first start of a CUDA sd-cli on Windows loads large DLLs that the antivirus
        # scans, and the frozen worker that wraps it starts cold: allow for that (#160).
        timeout=PROBE_TIMEOUTS[option],
        env=native_environment(),
        creationflags=0x08000000 if os.name == "nt" else 0,
    )


SOFTWARE_VULKAN_DEVICES = ("llvmpipe", "lavapipe", "swiftshader")
GPU_VENDORS = {
    "amd": "amd",
    "radeon": "amd",
    "nvidia": "nvidia",
    "geforce": "nvidia",
    "intel": "intel",
}


def match_vulkan_device(
    devices: str, selected_name: str, *, visible_devices_set: bool = False
) -> str:
    """The Vulkan id of the GPU PyTorch selected, never a different card.

    PyTorch and the Vulkan driver often name the same AMD GPU differently: ROCm may
    report "AMD Radeon Graphics" where Mesa says "AMD Radeon 780M (RADV PHOENIX)", or
    the other way round (issue #131). When the names differ, the only real Vulkan GPU
    is still taken if it comes from the same vendor and one of the two names carries no
    model number, or if the user narrowed Vulkan to it with GGML_VK_VISIBLE_DEVICES.
    """

    def normalize(name):
        # Mesa may append a driver description to the actual card name.
        name = re.sub(r"\([^)]*\)", "", name)
        return " ".join(re.findall(r"[a-z0-9]+", name.lower()))

    def vendor(name):
        return next(
            (GPU_VENDORS[word] for word in normalize(name).split() if word in GPU_VENDORS), None
        )

    def generic(name):
        return not re.search(r"\d", normalize(name))

    gpus = []
    for line in devices.splitlines():
        identifier, separator, description = line.partition("\t")
        if separator and re.fullmatch(r"Vulkan\d+", identifier):
            gpus.append((identifier, description))
    named = [
        identifier
        for identifier, description in gpus
        if normalize(description) == normalize(selected_name)
    ]
    if len(named) == 1:
        return named[0]
    hardware = [
        (identifier, description)
        for identifier, description in gpus
        if not any(word in description.lower() for word in SOFTWARE_VULKAN_DEVICES)
    ]
    if not named and len(hardware) == 1:
        identifier, description = hardware[0]
        same_gpu = vendor(description) is not None and vendor(description) == vendor(selected_name)
        if visible_devices_set or (same_gpu and (generic(description) or generic(selected_name))):
            return identifier
    listed = "; ".join(f"{index}: {description}" for index, (_id, description) in enumerate(gpus))
    raise ValueError(
        f"The Vulkan runtime cannot uniquely identify the selected GPU ({selected_name}). "
        f"Vulkan lists: {listed or 'no GPU'}. Use a CUDA/ROCm native runtime, or start "
        "LocalSR with GGML_VK_VISIBLE_DEVICES set to that GPU's number. No model was loaded."
    )


def resolve_edit_gpu(runtime: Path, device: str, backend: str) -> str:
    if device == "mps":
        return "MTL0"
    import torch

    index = int(device.partition(":")[2]) if ":" in device else torch.cuda.current_device()
    if backend == "VULKAN":
        listing = probe_runtime(runtime, "--list-devices")
        if listing.returncode != 0:
            raise ValueError("The native Vulkan runtime cannot enumerate GPUs. Check the driver.")
        return match_vulkan_device(
            listing.stdout,
            torch.cuda.get_device_name(index),
            visible_devices_set=bool(os.environ.get("GGML_VK_VISIBLE_DEVICES", "").strip()),
        )
    return f"{'ROCm' if torch.version.hip else 'CUDA'}{index}"


def edit_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def custom_edit_model(data: dict) -> dict:
    """Build a model descriptor from the user's local files and their real sizes."""
    family = str(data.get("family") or "")
    spec = CUSTOM_EDIT_FAMILIES.get(family)
    if spec is None:
        raise ValueError("Unsupported editing model family.")
    raw_files = data.get("custom_files")
    if not isinstance(raw_files, dict):
        raise ValueError("Choose all required editing model files first.")
    files = []
    for role in spec["roles"]:
        raw_path = raw_files.get(role)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Choose all required editing model files first.")
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"The custom {role.replace('_', ' ')} file is missing. Choose your edit model files again."
            )
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise ValueError(f"The custom {role.replace('_', ' ')} file is empty.")
        files.append(
            {"role": role, "path": str(path), "filename": path.name, "size_bytes": size_bytes}
        )
    model = {
        "model_id": CUSTOM_EDIT_MODEL_ID,
        # Never expose custom filenames or paths in job labels or crash reports.
        "name": "Your edit model",
        "family": family,
        "quantization": "Custom",
        "files": files,
        "default_steps": spec["default_steps"],
        "cfg_scale": spec["cfg_scale"],
        "terms_acceptance_required": False,
    }
    model["min_unified_memory_gb"] = minimum_unified_memory_gb(model)
    model["min_vram_gb"] = minimum_vram_gb(model)
    return model


def validate_request(data: dict) -> dict:
    if data.get("model_id") == CUSTOM_EDIT_MODEL_ID:
        model = custom_edit_model(data)
    else:
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
    for key in ("edit_text_encoder", "edit_vae"):
        if data.get(key, "auto") not in PLACEMENT_CHOICES:
            raise ValueError(f"{key} must be auto, gpu or cpu.")
    vram_limit = data.get("edit_vram_limit_mb", 0)
    if (
        isinstance(vram_limit, bool)
        or not isinstance(vram_limit, int)
        or vram_limit < 0
        or 0 < vram_limit < 1024
    ):
        raise ValueError("edit_vram_limit_mb must be 0 or at least 1024 MiB.")
    if not isinstance(data.get("edit_allow_any_size", False), bool):
        raise ValueError("edit_allow_any_size must be true or false.")
    return model


class EditProgress:
    """Turns the native runtime's log into a phase label and a moving percentage.

    Phase weights allocate progress to loading, sampling and decoding. Phases
    without a counter creep forward with time so the bar never stands still.
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


def verify_custom_files(model: dict, progress=None) -> dict[str, Path]:
    """Verify the user's own model files stay present in place without copying or catalog hashes."""
    paths = {}
    for entry in model["files"]:
        path = Path(entry["path"])
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(
                f"The custom {entry['role'].replace('_', ' ')} file is missing. Choose your edit model files again."
            )
        paths[entry["role"]] = path
    if progress is not None:
        progress(1.0)
    return paths


def verify_bundle(
    model: dict, directory: Path, cancel: threading.Event, progress=None
) -> dict[str, Path]:
    paths = {}
    total = sum(entry["size_bytes"] for entry in model["files"]) or 1
    hashed = 0
    for entry in model["files"]:
        path = directory / entry["filename"]
        if not path.is_file() or (stat := path.stat()).st_size != entry["size_bytes"]:
            raise FileNotFoundError(
                "The editing model is incomplete. Download all components again."
            )
        key = os.fspath(path.resolve())
        signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_dev)
        # A rewrite inside the timestamp granularity can keep the same signature, so a
        # verdict only counts once the file has been untouched for a while.
        settled = time.time_ns() - max(stat.st_mtime_ns, stat.st_ctime_ns) > 2_000_000_000
        if settled and _VERIFIED_BUNDLE_FILES.get(key) == (signature, entry["sha256"]):
            if cancel.is_set():
                raise InterruptedError("Editing cancelled.")
            hashed += stat.st_size
            if progress is not None:
                progress(hashed / total)
            paths[entry["role"]] = path
            continue
        _VERIFIED_BUNDLE_FILES.pop(key, None)
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
        if settled:
            _VERIFIED_BUNDLE_FILES[key] = (signature, entry["sha256"])
        paths[entry["role"]] = path
    return paths


def check_runtime(path: str) -> Path:
    runtime = Path(path).resolve(strict=True)
    if not runtime.is_file():
        raise ValueError("Select the native sd-cli editing runtime.")
    stat = runtime.stat()
    key = (str(runtime), stat.st_size, stat.st_mtime_ns)
    if key in _CHECKED_RUNTIMES:
        return runtime
    try:
        help_result = probe_runtime(runtime, "--help")
    except subprocess.TimeoutExpired as error:
        raise ValueError(
            "The editing runtime did not answer in time. Try the edit again; if it keeps "
            "happening, use Help > Report a problem."
        ) from error
    if help_result.returncode != 0 or any(
        flag not in help_result.stdout + help_result.stderr for flag in REQUIRED_FLAGS
    ):
        raise ValueError("The editing runtime is too old. Reinstall LocalSR to restore it.")
    _CHECKED_RUNTIMES.add(key)
    return runtime


def edit_dimensions(width: int, height: int, limit: int) -> tuple[int, int]:
    ratio = min(1.0, limit / max(width, height))
    return (
        max(32, math.floor(width * ratio / 32) * 32),
        max(32, math.floor(height * ratio / 32) * 32),
    )


def placement_note(model: dict, plan: EditPlan, device: str) -> str:
    if model["family"] == "mage-flow-edit-turbo" and device == "mps":
        return "Diffusion model, text encoder, and VAE ran on the CPU."
    if plan.text_encoder == "gpu" and plan.vae == "gpu":
        return "Text encoder and VAE ran on the GPU."
    if plan.text_encoder == "gpu":
        return "Text encoder ran on the GPU; VAE ran on the CPU."
    if plan.vae == "gpu":
        return "VAE ran on the GPU; text encoder ran on the CPU."
    return "Text encoder and VAE ran on the CPU."


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
    # The pinned Metal backend produces blank white output for Mage-Flow's
    # diffusion transformer on Apple Silicon, whereas full CPU with default
    # physical-core threading finishes a 512 px edit in ~96 s.
    use_cpu = model["family"] == "mage-flow-edit-turbo" and device == "mps"
    te_backend = gpu if plan.text_encoder == "gpu" else "cpu"
    vae_backend = gpu if plan.vae == "gpu" else "cpu"
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
        *(["--offload-to-cpu"] if plan.offload else []),
        "--mmap",
        "--disable-prefetch",
        "--fa",
        "--vae-tiling",
        "--vae-tile-size",
        "256",
        "--conditioning-cache-size",
        "0",
        "--backend",
        "cpu" if use_cpu else f"diffusion={gpu},te={te_backend},vae={vae_backend}",
        "--max-vram",
        f"{plan.gpu_budget / GIB:.2f}",
        "--cfg-scale",
        f"{model.get('cfg_scale', 2.5):g}",
    ]
    if model["family"] == "qwen-image-edit-2511":
        args += ["--flow-shift", "3", "--model-args", "qwen_image_zero_cond_t=true"]
    elif model["family"] == "qwen-image-2.1":
        args += ["--model-args", "qwen_image_2_1_prefix_cache=false"]
    # FLUX.2 klein is distilled: guidance 1.0, a handful of steps, no model arguments.
    return args


def run_edit_job(data: dict, cancel: threading.Event, emit, *, sampler=read_edit_memory):
    started = time.monotonic()
    model = validate_request(data)
    steps = int(data.get("steps") or model.get("default_steps", 40))
    device = str(data.get("device", "mps"))
    edit_text_encoder = str(data.get("edit_text_encoder", "auto"))
    edit_vae = str(data.get("edit_vae", "auto"))
    edit_vram_limit_mb = int(data.get("edit_vram_limit_mb", 0))
    edit_allow_any_size = bool(data.get("edit_allow_any_size", False))
    initial = sampler(device)
    if initial.unified and edit_vram_limit_mb:
        emit(
            LogMessage(
                level="info",
                message=f"Ignoring edit_vram_limit_mb={edit_vram_limit_mb} MiB on unified memory.",
            )
        )
    plan = plan_edit(
        model,
        initial,
        data.get("max_dimension", 512),
        text_encoder=edit_text_encoder,
        vae=edit_vae,
        vram_limit_mb=edit_vram_limit_mb,
        allow_any_size=edit_allow_any_size,
    )
    if cancel.is_set():
        raise InterruptedError("Editing cancelled.")
    require_output_name(data["output_path"])
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

    files = (
        verify_custom_files(model, checked)
        if model["model_id"] == CUSTOM_EDIT_MODEL_ID
        else verify_bundle(model, Path(data["bundle_dir"]), cancel, checked)
    )
    tracker.enter(1)
    # Re-admit after the potentially long checksum pass, without trusting stale free RAM.
    initial = sampler(device)
    plan = plan_edit(
        model,
        initial,
        data.get("max_dimension", 512),
        text_encoder=edit_text_encoder,
        vae=edit_vae,
        vram_limit_mb=edit_vram_limit_mb,
        allow_any_size=edit_allow_any_size,
    )
    output = Path(data["output_path"])
    if output.exists():
        raise FileExistsError("The edit result already exists; choose a new output filename.")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = data.get("output_temporary_directory") or data.get("scratch_directory")
    require_room([(scratch or output.parent, edit_bytes(plan.max_dimension))])
    with tempfile.TemporaryDirectory(prefix="localsr-edit-", dir=scratch or output.parent) as work:
        work = Path(work)
        reference, result = work / "reference.png", work / "result.png"
        with open_photo(data["image_path"]) as source:
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
        payload = json.dumps(
            {
                "command": command,
                "plan": asdict(plan),
                "unified": initial.unified,
                "swap_used": initial.swap_used,
            }
        ).encode("utf-8")
        specification.write_bytes(payload)
        guard_command = [sys.executable]
        if not getattr(sys, "frozen", False):
            guard_command += ["-m", "localsr.worker"]
        guard_command += [
            "--edit-guard",
            hashlib.sha256(payload).hexdigest(),
            str(specification),
        ]
        logs = deque(maxlen=12)
        process = track(
            subprocess.Popen(
                guard_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
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
                detail = "\n".join(logs)
                # The guard uses exit 75 when memory runs short. A stop while the editor ran
                # says what the guard saw; sd-cli's own out-of-memory failures, and the
                # guard's check before loading, get the edit size/placement advice.
                guard = [line for line in logs if line.startswith("Editing stopped")]
                if process.returncode == 75 and guard:
                    raise EditMemoryStop(
                        guard[-1],
                        device_type="cpu" if initial.unified else "cuda",
                        step="edit",
                    )
                if process.returncode == 75:
                    cause = MemoryError(detail)
                    raise DeviceOutOfMemoryError(
                        EDIT_OUT_OF_MEMORY_MESSAGE,
                        device_type="cpu" if initial.unified else "cuda",
                        step="edit",
                    ) from cause
                if is_edit_out_of_memory(detail):
                    raise DeviceOutOfMemoryError(
                        EDIT_OUT_OF_MEMORY_MESSAGE,
                        device_type="cpu" if initial.unified else "cuda",
                        step="edit",
                    ) from RuntimeError(f"Editing stopped (exit {process.returncode}). {detail}")
                raise RuntimeError(f"Editing stopped (exit {process.returncode}). {detail}")
            if not result.is_file():
                raise RuntimeError("The editing runtime returned no image.")
            with Image.open(result, formats=PILLOW_INPUT_FORMATS) as image:
                image.verify()
            # Publish without overwriting an existing result, even in a filename race.
            publish_output(result, output, cancel)
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
                job_id,
                elapsed_seconds=time.monotonic() - started,
                output_path=str(output),
                notes=placement_note(model, plan, device),
            )
        )
