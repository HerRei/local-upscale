"""Core ML execution on Apple silicon: the Neural Engine first, then the GPU.

A PyTorch checkpoint is converted on first use into an fp16 Core ML program for
one square input shape, compiled, and cached on disk. Every tile is padded to
that shape because the Neural Engine only runs static shapes, and each new shape
costs a conversion and a compile. The first real tile is a gate: Core ML must
reproduce the PyTorch result on the same pixels and must not be meaningfully
slower than the PyTorch MPS path. The verdict is stored per Mac, macOS version
and toolchain, so later jobs skip the gate. Anything that fails keeps PyTorch.
"""

import copy
import functools
import hashlib
import importlib.util
import json
import logging
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

NEURAL_ENGINE = "CPU_AND_NE"
CORE_ML_GPU = "CPU_AND_GPU"

# Measured on an M1 Pro (2026-10-24): every operation of SPAN, RRDBNet and FBCNN
# maps to the Neural Engine, which ran them 2-3x faster than MPS fp32 with a
# fraction of the energy. RealPLKSR's 17x17 partial convolutions fall back to
# the CPU on the Neural Engine, but its Core ML GPU program still beats MPS.
# HAT, SwinIR and NAFNet produce broken fp16 output and SCUNet does not convert,
# so every other architecture keeps PyTorch.
COMPUTE_UNITS = {
    "SPAN": (NEURAL_ENGINE, CORE_ML_GPU),
    "ESRGAN": (NEURAL_ENGINE, CORE_ML_GPU),
    "FBCNN": (NEURAL_ENGINE, CORE_ML_GPU),
    "PLKSR": (CORE_ML_GPU,),
}
# The Neural Engine uses far less energy than the GPU, so keep it unless the
# PyTorch path is clearly faster on this Mac (newer GPUs can be).
NEURAL_ENGINE_SPEED_MARGIN = 1.3
MINIMUM_PSNR = 45.0
# Converting large shapes exhausted 16 GB of memory for RRDBNet (768 px failed,
# 512 px was measured), so larger user tiles keep PyTorch. SPAN is small enough
# to convert whole video frames.
MAXIMUM_PIXELS = 576 * 576
MAXIMUM_PIXELS_BY_ARCHITECTURE = {"ESRGAN": 544 * 544, "SPAN": 2_400_000}
# Every frame of a video has the same size, so these run one call per frame
# instead of overlapping tiles: fewer pixels and calls (SPAN on an M1 Pro: 1.4-2.2x).
WHOLE_FRAME_ARCHITECTURES = {"SPAN"}
SIDE_ALIGNMENT = 16
LOAD_TIMEOUT_SECONDS = 180.0
# Bump when the conversion recipe changes so stale programs are not reused.
RECIPE_VERSION = 1
CACHE_ENV = "LOCALSR_COREML_CACHE"
DISABLE_ENV = "LOCALSR_DISABLE_COREML"

UNIT_LABELS = {NEURAL_ENGINE: "the Neural Engine", CORE_ML_GPU: "the GPU (Core ML)"}


@functools.cache
def coreml_available() -> bool:
    """Whether this process can convert and run Core ML programs."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    # coremltools imports even without its native module and then silently
    # cannot run anything, so look for that module. Finding it does not import
    # the package, which would add most of a second to every worker start.
    spec = importlib.util.find_spec("coremltools")
    locations = spec.submodule_search_locations if spec is not None else None
    return any(any(Path(location).glob("libcoremlpython*.so")) for location in (locations or ()))


def coreml_units_for(architecture: str) -> tuple[str, ...]:
    """Return the Core ML compute units to try for an architecture, if any."""
    if os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return ()
    units = COREML_UNITS_OVERRIDE if COREML_UNITS_OVERRIDE is not None else COMPUTE_UNITS
    if not units.get(architecture) or not coreml_available():
        return ()
    return units[architecture]


# Tests replace the policy without patching the module's constant.
COREML_UNITS_OVERRIDE: dict[str, tuple[str, ...]] | None = None


def cache_directory() -> Path:
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override)
    return Path.home() / "Library" / "Caches" / "LocalSR" / "coreml"


def checkpoint_identity(path: str) -> str:
    """Identify a checkpoint by content, using the catalog digest when known."""
    from .model_catalog import CATALOG_BY_FILENAME

    catalog_model = CATALOG_BY_FILENAME.get(os.path.basename(path))
    if catalog_model is not None and catalog_model.sha256:
        return catalog_model.sha256
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _chip_name() -> str:
    try:
        return subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return platform.processor() or "unknown"


def _psnr(first: np.ndarray, second: np.ndarray) -> float:
    error = float(np.mean((np.clip(first, 0, 1) - np.clip(second, 0, 1)) ** 2))
    return math.inf if error == 0 else 10 * math.log10(1 / error)


def _align(value: int, multiple: int) -> int:
    return (value + multiple - 1) // multiple * multiple


def _move_tensor_attributes(module: torch.nn.Module, device: torch.device) -> None:
    """Move plain tensor attributes that .to() does not see.

    SPAN keeps its RGB mean as an ordinary attribute and moves it to the input's
    device during forward, so a model that already ran on MPS carries an MPS
    tensor into an otherwise CPU copy.
    """
    for child in module.modules():
        for name, value in list(vars(child).items()):
            if isinstance(value, torch.Tensor) and value.device != device:
                setattr(child, name, value.to(device))


_UNIT_CODES = {"CPU_ONLY": 0, "CPU_AND_GPU": 1, "ALL": 2, "CPU_AND_NE": 3}
_ARRAY_TYPES = {65552: np.float16, 65568: np.float32}


class _NativeRunner:
    """Predict through Core ML's Objective-C API (PyObjC).

    coremltools holds the GIL for the whole prediction, so nothing else in the
    worker (decoding, the encoder hand-off) can run while the Neural Engine
    works; Core ML called through PyObjC releases it. It also hands back the
    program's FP16 output directly, which halves the time per call.
    """

    def __init__(self, path: Path, unit: str):
        import CoreML
        import Foundation

        configuration = CoreML.MLModelConfiguration.alloc().init()
        configuration.setComputeUnits_(_UNIT_CODES[unit])
        model, error = CoreML.MLModel.modelWithContentsOfURL_configuration_error_(
            Foundation.NSURL.fileURLWithPath_(str(path)), configuration, None
        )
        if model is None:
            raise RuntimeError(str(error))
        constraint = model.modelDescription().inputDescriptionsByName()["image"]
        shape = [int(value) for value in constraint.multiArrayConstraint().shape()]
        array, error = CoreML.MLMultiArray.alloc().initWithShape_dataType_error_(
            shape, CoreML.MLMultiArrayDataTypeFloat32, None
        )
        if array is None:
            raise RuntimeError(str(error))
        features, error = CoreML.MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(
            {"image": array}, None
        )
        if features is None:
            raise RuntimeError(str(error))
        self.model, self.array, self.features = model, array, features
        self.input = self._view(array, np.float32)

    @staticmethod
    def _view(array, dtype) -> np.ndarray:
        shape = [int(value) for value in array.shape()]
        strides = [int(value) for value in array.strides()]
        item = np.dtype(dtype).itemsize
        raw = np.frombuffer(array.dataPointer().as_buffer(strides[0] * shape[0] * item), dtype)
        return np.lib.stride_tricks.as_strided(
            raw, shape=shape, strides=[stride * item for stride in strides]
        )

    def predict(self, inputs: dict) -> dict:
        import objc

        np.copyto(self.input, inputs["image"])
        # Core ML returns autoreleased outputs (about 100 MB for a whole frame).
        # Worker threads have no run loop to drain them, so drain per call.
        with objc.autorelease_pool():
            output, error = self.model.predictionFromFeatures_error_(self.features, None)
            if output is None:
                raise RuntimeError(str(error))
            result = output.featureValueForName_("restored").multiArrayValue()
            # Copy while Core ML still owns the buffer, keeping its FP16; PyTorch's
            # copy is multithreaded, and the engine casts to FP32 once afterwards.
            view = self._view(result, _ARRAY_TYPES[int(result.dataType())])
            restored = torch.from_numpy(view).clone()
            del view, result, output
        return {"restored": restored}


def _load_compiled(path: Path, unit: str, timeout: float):
    """Load a compiled program; the Neural Engine compiles it on first load.

    That compile can run for minutes on some graphs and cannot be interrupted,
    so it runs on a daemon thread and the job continues on PyTorch on timeout.
    """
    result: dict[str, object] = {}

    def load():
        try:
            try:
                result["model"] = _NativeRunner(path, unit)
            except ImportError:
                import coremltools as ct

                result["model"] = ct.models.CompiledMLModel(
                    str(path), compute_units=getattr(ct.ComputeUnit, unit)
                )
        except Exception as error:  # noqa: BLE001
            result["error"] = error

    thread = threading.Thread(target=load, name="localsr-coreml-load", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"Core ML did not finish compiling for {unit} in {timeout:.0f} s")
    if "error" in result:
        raise RuntimeError(f"Core ML could not load the program: {result['error']}")
    return result["model"]


class _Program:
    """One converted shape of one checkpoint, with its stored verdicts."""

    def __init__(self, root: Path, architecture: str, identity: str, shape: tuple[int, int]):
        import coremltools as ct

        self.architecture = architecture
        self.shape = shape
        self.chip = _chip_name()
        self.macos = platform.mac_ver()[0]
        self.prefix = f"{architecture}-{identity[:16]}-{shape[0]}x{shape[1]}-"
        name = (
            f"{self.prefix}r{RECIPE_VERSION}"
            f"-ct{ct.__version__}-torch{torch.__version__.split('+')[0]}"
        )
        self.directory = root / name
        self.compiled = self.directory / "model.mlmodelc"
        self.verdicts_path = self.directory / "verdicts.json"

    def _verdict_key(self, unit: str) -> str:
        return f"{unit}|{self.chip}|{self.macos}"

    def verdict(self, unit: str) -> dict | None:
        try:
            verdicts = json.loads(self.verdicts_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = verdicts.get(self._verdict_key(unit))
        return value if isinstance(value, dict) else None

    def store_verdict(self, unit: str, verdict: dict) -> None:
        try:
            verdicts = json.loads(self.verdicts_path.read_text(encoding="utf-8"))
            if not isinstance(verdicts, dict):
                verdicts = {}
        except (OSError, ValueError):
            verdicts = {}
        verdicts[self._verdict_key(unit)] = {
            **verdict,
            "unit": unit,
            "shape": list(self.shape),
            "chip": self.chip,
            "macos": self.macos,
            "recorded": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.verdicts_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(verdicts, indent=2), encoding="utf-8")
            os.replace(temporary, self.verdicts_path)
        except OSError:
            logger.warning("Could not store the Core ML verdict", exc_info=True)

    def _prune(self) -> None:
        """Drop this shape's programs from older recipes or toolchains, and
        staging folders a crashed conversion left behind."""
        cutoff = time.time() - 86400
        for entry in self.directory.parent.iterdir():
            stale_program = entry.name.startswith(self.prefix) and entry != self.directory
            abandoned = entry.name.startswith(".staging-") and entry.stat().st_mtime < cutoff
            if entry.is_dir() and (stale_program or abandoned):
                shutil.rmtree(entry, ignore_errors=True)

    def ensure_compiled(self, source: torch.nn.Module, input_channels: int) -> None:
        if (self.compiled / "coremldata.bin").exists():
            return
        import coremltools as ct

        self.directory.parent.mkdir(parents=True, exist_ok=True)
        self._prune()
        import tqdm

        # coremltools reports progress through tqdm, whose default lock is a
        # multiprocessing semaphore. Creating one starts multiprocessing's
        # resource tracker, a child process that a frozen worker would launch
        # as another copy of itself, over and over.
        tqdm.tqdm.set_lock(threading.RLock())
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.directory.parent))
        try:
            started = time.perf_counter()
            _move_tensor_attributes(source, torch.device("cpu"))
            with torch.inference_mode(False), torch.no_grad():
                example = torch.rand(1, input_channels, *self.shape)
                traced = torch.jit.trace(source, example, check_trace=False)
            program = ct.convert(
                traced,
                inputs=[ct.TensorType(name="image", shape=example.shape)],
                outputs=[ct.TensorType(name="restored")],
                convert_to="mlprogram",
                compute_precision=ct.precision.FLOAT16,
                minimum_deployment_target=ct.target.macOS14,
            )
            package = staging / "model.mlpackage"
            program.save(str(package))
            del program, traced
            compiled = Path(ct.models.utils.compile_model(str(package)))
            shutil.move(str(compiled), staging / "model.mlmodelc")
            shutil.rmtree(package, ignore_errors=True)
            if self.directory.exists():
                # A previous attempt left a verdict file but no program.
                shutil.move(str(staging / "model.mlmodelc"), self.compiled)
            else:
                os.replace(staging, self.directory)
            logger.info(
                "Converted %s for Core ML at %sx%s px in %.1f s",
                self.architecture,
                *self.shape,
                time.perf_counter() - started,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)


class _CoreMLImage(torch.nn.Module):
    """Run a Spandrel model through Core ML at one fixed, padded input shape."""

    def __init__(self, descriptor, units: tuple[str, ...], identity: str):
        super().__init__()
        self.fallback = descriptor.model
        self.fallback_call = descriptor._call_fn
        reference = next(descriptor.model.parameters())
        self.fallback_device, self.fallback_dtype = reference.device, reference.dtype
        self.architecture = descriptor.architecture.name
        self.scale = descriptor.scale
        self.input_channels = descriptor.input_channels
        requirements = descriptor.size_requirements
        self.minimum = int(getattr(requirements, "minimum", 0) or 0)
        multiple = int(getattr(requirements, "multiple_of", 1) or 1)
        self.alignment = math.lcm(SIDE_ALIGNMENT, multiple)
        self.maximum_pixels = MAXIMUM_PIXELS_BY_ARCHITECTURE.get(self.architecture, MAXIMUM_PIXELS)
        self.units = units
        self.identity = identity
        self.source_descriptor = descriptor
        # (height, width) of the one input shape Core ML runs at.
        self.shape: tuple[int, int] | None = None
        self.cancel_event: threading.Event | None = None
        self.status_callback: Callable[[str], None] | None = None
        # shape -> {"program", "runner", "unit", "pending", "remaining"}
        self.states: OrderedDict[tuple[int, int], dict] = OrderedDict()
        self.active_unit: str | None = None

    # Engine hooks ---------------------------------------------------------
    def _shape_for(self, height: int, width: int) -> tuple[int, int]:
        return (
            _align(max(height, self.minimum, 1), self.alignment),
            _align(max(width, self.minimum, 1), self.alignment),
        )

    def prepare(self, tile_size: int, halo: int, frame: tuple[int, int] | None = None) -> None:
        """Choose the input shape: a square tile, or a whole (height, width) frame."""
        if frame is not None:
            shape = self._shape_for(frame[0] + 2 * halo, frame[1] + 2 * halo)
        else:
            side = tile_size + 2 * halo
            shape = self._shape_for(side, side)
        current = self.shape
        # Keep a larger existing program for smaller tiles (the OOM retry), but not
        # one so much larger that padding would waste most of the work.
        if (
            current is None
            or shape[0] > current[0]
            or shape[1] > current[1]
            or current[0] * current[1] > 1.5 * shape[0] * shape[1]
        ):
            self.shape = shape

    def whole_frame_shape(self, height: int, width: int, halo: int) -> tuple[int, int] | None:
        """The program shape for one whole frame, if this model can run it."""
        if self.architecture not in WHOLE_FRAME_ARCHITECTURES:
            return None
        shape = self._shape_for(height + 2 * halo, width + 2 * halo)
        return shape if self._usable(shape) else None

    def _usable(self, shape: tuple[int, int]) -> bool:
        if shape[0] * shape[1] > self.maximum_pixels:
            return False
        state = self._state(shape)
        if state["program"] is None:
            return False
        if state["runner"] is not None:
            return True
        program: _Program = state["program"]
        return any(
            (verdict := program.verdict(unit)) is None or verdict.get("accepted")
            for unit in state["remaining"]
        )

    def fixed_shape(self) -> tuple[int, int] | None:
        """The input shape Core ML runs at, or None once it is ruled out."""
        shape = self.shape
        return shape if shape is not None and self._usable(shape) else None

    def _status(self, message: str) -> None:
        logger.info(message)
        if self.status_callback is not None:
            try:
                self.status_callback(message)
            except Exception:  # noqa: BLE001
                logger.debug("Core ML status callback failed", exc_info=True)

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("Cancelled")

    # Execution ------------------------------------------------------------
    def _run_fallback(self, image: torch.Tensor) -> torch.Tensor:
        image = image.to(self.fallback_device, self.fallback_dtype)
        return self.fallback_call(self.fallback, image)

    def _predict(self, runner, image: torch.Tensor) -> torch.Tensor:
        array = image.detach().to("cpu", torch.float32).contiguous().numpy()
        output = runner.predict({"image": array})["restored"]
        if isinstance(output, torch.Tensor):
            return output
        return torch.from_numpy(np.asarray(output, dtype=np.float32))

    def _pad(self, image: torch.Tensor, shape: tuple[int, int]) -> torch.Tensor:
        height, width = image.shape[-2:]
        if (height, width) == shape:
            return image
        # Replicate like LocalSR's own size-multiple padding: right and bottom.
        return F.pad(image, (0, shape[1] - width, 0, shape[0] - height), mode="replicate")

    def _state(self, shape: tuple[int, int]) -> dict:
        state = self.states.get(shape)
        if state is not None:
            self.states.move_to_end(shape)
            return state
        try:
            program = _Program(cache_directory(), self.architecture, self.identity, shape)
        except Exception:  # noqa: BLE001
            # A broken coremltools installation must not fail the job.
            logger.warning("Core ML is unavailable; using PyTorch", exc_info=True)
            program = None
        state = {
            "program": program,
            "runner": None,
            "unit": None,
            "pending": False,
            "remaining": list(self.units) if program is not None else [],
        }
        self.states[shape] = state
        while len(self.states) > 2:
            self.states.popitem(last=False)
        return state

    def _next_runner(self, state: dict) -> None:
        """Load the next candidate unit, honouring stored verdicts."""
        program: _Program = state["program"]
        state["runner"], state["unit"], state["pending"] = None, None, False
        while state["remaining"]:
            unit = state["remaining"].pop(0)
            verdict = program.verdict(unit)
            if verdict is not None and not verdict.get("accepted"):
                continue
            if not (program.compiled / "coremldata.bin").exists():
                target = "the Neural Engine" if unit == NEURAL_ENGINE else "Core ML"
                self._status(
                    f"Preparing {self.architecture} for {target}. This happens once per "
                    "model and tile size and can take a minute."
                )
                try:
                    from .onnx_runtime import _RawImage

                    program.ensure_compiled(
                        _RawImage(self.source_descriptor).eval(), self.input_channels
                    )
                except Exception as error:  # noqa: BLE001
                    # Conversion does not depend on the compute unit.
                    logger.warning("Core ML conversion of %s failed: %s", self.architecture, error)
                    for rejected in (unit, *state["remaining"]):
                        program.store_verdict(
                            rejected, {"accepted": False, "reason": f"conversion: {error}"[:500]}
                        )
                    state["remaining"].clear()
                    break
            try:
                self._check_cancelled()
                runner = _load_compiled(program.compiled, unit, LOAD_TIMEOUT_SECONDS)
            except InterruptedError:
                raise
            except Exception as error:  # noqa: BLE001
                logger.warning("Core ML %s for %s failed: %s", unit, self.architecture, error)
                program.store_verdict(unit, {"accepted": False, "reason": str(error)[:500]})
                continue
            state["runner"], state["unit"] = runner, unit
            state["pending"] = verdict is None
            if not state["pending"]:
                self._announce(unit)
            return

    def _announce(self, unit: str) -> None:
        if self.active_unit != unit:
            self.active_unit = unit
            self._status(f"{self.architecture} runs on {UNIT_LABELS.get(unit, unit)}.")

    def _gate(self, state: dict, padded: torch.Tensor) -> torch.Tensor | None:
        """Compare Core ML with PyTorch on a real tile and decide.

        Returns the PyTorch output when the tile is inconclusive (the reference
        itself is unstable), so the next tile repeats the gate.
        """
        unit, runner, program = state["unit"], state["runner"], state["program"]
        reference = self._run_fallback(padded)
        reference_cpu = reference.detach().to("cpu", torch.float32)
        if not torch.isfinite(reference_cpu).all() or (
            reference_cpu.amin() < -4 or reference_cpu.amax() > 5
        ):
            return reference
        candidate = self._predict(runner, padded)
        psnr = _psnr(candidate.numpy(), reference_cpu.numpy())
        accepted = bool(np.isfinite(candidate.numpy()).all()) and psnr >= MINIMUM_PSNR
        coreml_ms = torch_ms = None
        if accepted:
            coreml_ms = self._time(lambda: self._predict(runner, padded))
            torch_ms = self._time(lambda: self._run_fallback(padded), synchronize=padded.device)
            margin = NEURAL_ENGINE_SPEED_MARGIN if unit == NEURAL_ENGINE else 1.0
            accepted = coreml_ms <= torch_ms * margin
        reason = (
            "matched and fast enough"
            if accepted
            else ("slower than PyTorch" if coreml_ms is not None else "did not match PyTorch")
        )
        program.store_verdict(
            unit,
            {
                "accepted": accepted,
                "reason": reason,
                "psnr": None if math.isinf(psnr) else round(psnr, 2),
                "coreml_ms": None if coreml_ms is None else round(coreml_ms, 2),
                "torch_ms": None if torch_ms is None else round(torch_ms, 2),
            },
        )
        logger.info(
            "Core ML gate %s %s: %s (PSNR %.1f dB, %s ms vs PyTorch %s ms)",
            self.architecture,
            unit,
            reason,
            psnr,
            coreml_ms,
            torch_ms,
        )
        if accepted:
            state["pending"] = False
            self._announce(unit)
            return candidate
        self._next_runner(state)
        return None

    @staticmethod
    def _time(function, *, synchronize: torch.device | None = None, repeats: int = 2) -> float:
        def run():
            function()
            if synchronize is not None and synchronize.type == "mps":
                torch.mps.synchronize()

        run()
        started = time.perf_counter()
        for _ in range(repeats):
            run()
        return (time.perf_counter() - started) / repeats * 1000

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        self._check_cancelled()
        height, width = image.shape[-2:]
        shape = self.shape
        if shape is None:
            side = max(height, width)
            shape = self.shape = self._shape_for(side, side)
        if height > shape[0] or width > shape[1] or shape[0] * shape[1] > self.maximum_pixels:
            # Larger recovery crops and tile sizes keep PyTorch.
            return self._run_fallback(image)
        state = self._state(shape)
        if state["runner"] is None and state["remaining"]:
            self._next_runner(state)
        padded = self._pad(image, shape)
        while state["runner"] is not None:
            self._check_cancelled()
            try:
                if state["pending"]:
                    output = self._gate(state, padded)
                    if output is None:
                        continue
                else:
                    output = self._predict(state["runner"], padded)
            except InterruptedError:
                raise
            except Exception as error:  # noqa: BLE001
                logger.warning("Core ML %s failed during a tile: %s", state["unit"], error)
                state["program"].store_verdict(
                    state["unit"], {"accepted": False, "reason": str(error)[:500]}
                )
                self._next_runner(state)
                continue
            self._check_cancelled()
            return output[..., : height * self.scale, : width * self.scale]
        if self.active_unit is not None:
            self.active_unit = None
            self._status(f"{self.architecture} runs on the GPU (PyTorch).")
        return self._run_fallback(image)


class CoreMLImageDescriptor:
    """A Spandrel descriptor whose network runs through Core ML when it can."""

    def __init__(self, descriptor, *, units: tuple[str, ...], identity: str):
        self.descriptor = copy.copy(descriptor)
        self.descriptor._model = _CoreMLImage(descriptor, units, identity).eval()
        self.descriptor._call_fn = lambda model, image: model(image)

    def __getattr__(self, name):
        return getattr(self.descriptor, name)

    def __call__(self, image):
        return self.descriptor(image)

    def set_cancel_event(self, cancel_event):
        self.model.cancel_event = cancel_event

    def set_status_callback(self, callback):
        self.model.status_callback = callback

    def prepare_tiles(self, tile_size: int, halo: int, frame: tuple[int, int] | None = None):
        self.model.prepare(tile_size, halo, frame)

    def whole_frame_shape(self, height: int, width: int, halo: int) -> tuple[int, int] | None:
        return self.model.whole_frame_shape(height, width, halo)

    @property
    def fixed_input_shape(self) -> tuple[int, int] | None:
        return self.model.fixed_shape()

    @property
    def input_device(self) -> torch.device | None:
        return torch.device("cpu") if self.model.fixed_shape() is not None else None
