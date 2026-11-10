"""ONNX image execution with the same Spandrel padding and range contract.

Graphs are converted from the checkpoint already verified by ModelAdapter. They
stay in memory and are bounded to four input shapes per loaded model. Checkpoints
and user settings are never rewritten by conversion.
"""

import copy
import gc
import hashlib
import io
import json
import logging
import math
import os
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import gpu_guard
from .gpu_guard import GATE_CACHE_ENV, gate_cache_directory  # noqa: F401

logger = logging.getLogger(__name__)


def directml_available() -> bool:
    try:
        import onnxruntime as ort

        ort.disable_telemetry_events()
        return "DmlExecutionProvider" in ort.get_available_providers()
    except (ImportError, OSError, RuntimeError):
        return False


class _ChannelLayerNorm(torch.nn.Module):
    """Express NAFNet's channel normalization as the native ONNX operation."""

    def __init__(self, original):
        super().__init__()
        self.weight, self.bias, self.eps = original.weight, original.bias, original.eps

    def forward(self, image):
        channels_last = image.permute(0, 2, 3, 1)
        return F.layer_norm(
            channels_last, (self.weight.shape[0],), self.weight, self.bias, self.eps
        ).permute(0, 3, 1, 2)


def prepare_export_model(module, architecture: str) -> None:
    for name, child in list(module.named_children()):
        kind = type(child).__name__
        if architecture == "NAFNet" and kind == "LayerNorm2d":
            setattr(module, name, _ChannelLayerNorm(child))
        elif architecture == "SPAN" and kind == "Conv3XC":
            # Freeze the exact FP32 inference weights before tracing. Repeating
            # reparameterization inside forward adds stateful export operations.
            child.update_params()
            fused = copy.deepcopy(child.eval_conv)
            if child.has_relu:
                fused = torch.nn.Sequential(fused, torch.nn.LeakyReLU(0.05))
            setattr(module, name, fused)
        else:
            prepare_export_model(child, architecture)


class _RawImage(torch.nn.Module):
    def __init__(self, descriptor):
        super().__init__()
        self.model = copy.deepcopy(descriptor.model).cpu().float().eval()
        self.call_fn = descriptor._call_fn
        prepare_export_model(self.model, descriptor.architecture.name)

    def forward(self, image):
        return self.call_fn(self.model, image)


# A GPU route counts only when its first real tile matches PyTorch on the CPU.
# Every LocalSR GPU route through ONNX runs FP32, so a correct route lands far above
# this (OpenVINO FP32 on an Intel iGPU: 128 dB); FP16 arithmetic lands near 50 dB.
MINIMUM_PSNR = 50.0
# An Intel NPU computes in FP16 whatever it is asked, so it is held to the bar the Neural
# Engine's FP16 check uses on a Mac (coreml_runtime), measured on real tiles there.
NPU_MINIMUM_PSNR = 45.0
# Spandrel architectures an Intel NPU runs whole, measured on a Core Ultra 200 (NPU 3720,
# driver 1.32) at a 288-pixel window against PyTorch on the CPU: SPAN 72 dB, ESRGAN 72 dB,
# FBCNN 81 dB, NAFNet 75-83 dB, each 1.7-2.8x faster than the same chip's GPU and 2-9x
# faster than its CPU. RealPLKSR and SCUNet do not compile for it; the attention models
# (HAT, SwinIR, DAT) did not finish compiling in 30 minutes. Those run on the CPU instead.
NPU_ARCHITECTURES = frozenset({"SPAN", "ESRGAN", "FBCNN", "NAFNet"})
_MAXIMUM_VERDICTS = 400
# Above this, a model keeps two sessions instead of four (NAFNet SIDD is 465 MB).
_LARGE_GRAPH_BYTES = 256 * 1024**2
# DXGI device-removed, hung, reset and internal-driver errors, and their usual texts.
_DEVICE_LOST_FRAGMENTS = (
    "887a0005",
    "887a0006",
    "887a0007",
    "887a0020",
    "device removed",
    "device_removed",
    "device hung",
    "device_hung",
    "device reset",
    "dxgi_error_device",
)
_MEMORY_FRAGMENTS = (
    "out of memory",
    "not enough memory",
    "insufficient memory",
    "failed to allocate",
    "e_outofmemory",
    "0x8007000e",
    "bad allocation",
    "bad_alloc",
    "allocation failed",
    "out of gpu resources",
    "cl_out_of_resources",
    "cl_mem_object_allocation_failure",
)
# A real tile below this spread of values is too flat to tell a GPU result apart.
_DETAILED_TILE_STD = 0.03
_OPENVINO_FP32 = json.dumps({"GPU": {"INFERENCE_PRECISION_HINT": "f32"}})
_OPENVINO_ACCURACY = json.dumps(
    {"GPU": {"INFERENCE_PRECISION_HINT": "f32", "EXECUTION_MODE_HINT": "ACCURACY"}}
)
# Configurations tried in order until one matches the CPU: (name, provider options,
# ONNX Runtime graph optimization: "all" or "basic"; NAFNet always runs with none).
# Metacommands are the vendor kernels DirectML uses when a driver offers them, a known
# source of results that differ from other GPUs. "basic" keeps constant folding and
# clean-ups that let the provider claim nodes, but no fusions.
ROUTE_CONFIGS = {
    "DmlExecutionProvider": (
        ("default", {}, "all"),
        ("no-metacommands", {"disable_metacommands": "true"}, "all"),
        ("no-metacommands-basic", {"disable_metacommands": "true"}, "basic"),
    ),
    "OpenVINOExecutionProvider": (
        ("fp32", {"load_config": _OPENVINO_FP32}, "all"),
        ("fp32-accuracy-basic", {"load_config": _OPENVINO_ACCURACY}, "basic"),
    ),
    "CPUExecutionProvider": (("default", {}, "all"),),
}
# The NPU has one precision; its compiled programs are kept in the check cache, because
# compiling a model for it takes from seconds to minutes.
NPU_ROUTE_CONFIGS = (("npu-fp16", {}, "all"),)


def _route_configs(route) -> tuple:
    return NPU_ROUTE_CONFIGS if route.kind == "NPU" else ROUTE_CONFIGS[route.provider]


# A compiled NPU program is about as large as its model (FBCNN: 296 MB).
NPU_CACHE_LIMIT = 2 * 1024**3


def _npu_cache_directory() -> Path:
    return gate_cache_directory() / "npu-cache"


def _trim_npu_cache(directory: Path, limit: int | None = None) -> None:
    """Delete the oldest compiled programs until the cache fits its limit."""
    limit = NPU_CACHE_LIMIT if limit is None else limit
    try:
        blobs = sorted(
            (path.stat().st_mtime, path.stat().st_size, path)
            for path in directory.iterdir()
            if path.is_file()
        )
    except OSError:
        return
    total = sum(size for _, size, _ in blobs)
    for _, size, path in blobs:
        if total <= limit:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size


def _provider_options(route, options: dict) -> dict:
    if route.kind != "NPU":
        return dict(options)
    cache = _npu_cache_directory()
    _trim_npu_cache(cache)
    return {**options, "load_config": json.dumps({"NPU": {"CACHE_DIR": str(cache)}})}


class GpuRouteRejected(RuntimeError):
    """No configuration of the GPU route reproduced the CPU's result for this model."""


class GpuDeviceLost(RuntimeError):
    """The graphics driver removed or reset the device; only a fresh process recovers."""


@dataclass(frozen=True)
class OnnxRoute:
    provider: str = "DmlExecutionProvider"
    device_index: int = 0
    ep_device: object = None
    # What identifies the hardware and driver in stored verdicts.
    identity: str = ""
    label: str = ""
    # Without a known device and driver identity a verdict lasts only for this process:
    # a later driver must be checked again.
    persist: bool = True
    # The app's device id ("directml:0", "openvino:<uuid>"), for the preflight process.
    device: str = ""
    # OpenVINO's device kind: "GPU", or "NPU" for an Intel NPU, which computes in FP16.
    kind: str = "GPU"

    def __post_init__(self):
        if self.provider not in ROUTE_CONFIGS or self.device_index < 0:
            raise ValueError("Invalid ONNX execution provider or device index")
        if self.provider == "OpenVINOExecutionProvider" and self.ep_device is None:
            raise ValueError("The OpenVINO route needs an OpenVINO device")


class _Verdicts:
    """Stored first-tile checks, keyed by graph, route, configuration, device and versions."""

    def __init__(self, directory: Path, persist: bool = True):
        self.path = directory / "verdicts.json"
        self.memory: dict | None = None if persist else {}

    def _read(self) -> dict:
        if self.memory is not None:
            return self.memory
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def get(self, key: str) -> dict | None:
        value = self._read().get(key)
        return value if isinstance(value, dict) else None

    def put(self, key: str, verdict: dict) -> None:
        verdicts = self._read()
        verdicts[key] = {**verdict, "recorded": datetime.now(UTC).isoformat(timespec="seconds")}
        if self.memory is not None:
            return
        if len(verdicts) > _MAXIMUM_VERDICTS:
            newest = sorted(verdicts.items(), key=lambda item: str(item[1].get("recorded", "")))
            verdicts = dict(newest[-_MAXIMUM_VERDICTS:])
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(verdicts, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            logger.warning("Could not store the GPU check result", exc_info=True)


def _psnr(first: np.ndarray, second: np.ndarray) -> float:
    error = float(np.mean((np.clip(first, 0, 1) - np.clip(second, 0, 1)) ** 2))
    return math.inf if error == 0 else 10 * math.log10(1 / error)


def _localsr_version() -> str:
    from .. import __version__

    return __version__


class _OnnxImage(torch.nn.Module):
    def __init__(self, descriptor, route: OnnxRoute):
        super().__init__()
        self.source = _RawImage(descriptor)
        # PyTorch on the CPU is the reference a GPU route must reproduce.
        self.reference_model = descriptor.model
        self.reference_call = descriptor._call_fn
        self.architecture = descriptor.architecture.name
        self.scale = descriptor.scale
        self.output_channels = descriptor.output_channels
        self.route = route
        self.minimum_psnr = NPU_MINIMUM_PSNR if route.kind == "NPU" else MINIMUM_PSNR
        self.provider = route.provider
        self.device_index = route.device_index
        self.sessions = OrderedDict()
        self.cancel_event: threading.Event | None = None
        self.status_callback = None
        self.execution_verified = False
        # Tiles that ran through PyTorch on the CPU because the route could not be checked.
        self.cpu_tiles = 0
        self.verdicts = _Verdicts(gate_cache_directory(), persist=route.persist)
        self.crashes = gpu_guard.CrashNotes(gate_cache_directory())
        self.preflight_passed = gpu_guard.Passed(gate_cache_directory())
        # How this GPU crashed before ("engine" or "preflight"), when it did.
        self.crash_cause: str | None = None
        # An image's tiles come in up to four shapes (interior, right edge, bottom edge,
        # corner). Keeping all four lets the next image or video frame of the same size
        # reuse them; a very large model keeps two to spare shared memory.
        self.session_limit = 4
        # The one input shape an NPU route runs at, once the engine has said its tile size.
        self.fixed_shape: tuple[int, int] | None = None

    def _check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("Cancelled")

    def _status(self, message: str) -> None:
        logger.info(message)
        if self.status_callback is not None:
            try:
                self.status_callback(message)
            except Exception:  # noqa: BLE001
                logger.debug("ONNX status callback failed", exc_info=True)

    @property
    def _gated(self) -> bool:
        return self.provider != "CPUExecutionProvider"

    def _label(self) -> str:
        return self.route.label or self.provider

    def _crash_key(self) -> str:
        # The whole GPU, not one configuration: a driver that crashes on one setting is
        # not trusted with the others.
        # The device as well: two identical cards share an identity.
        return "|".join((self.route.identity, self.route.device, self.provider, _localsr_version()))

    def _watch(self):
        return self.crashes.watch(self._crash_key()) if self._gated else nullcontext()

    def _verdict_key(self, graph_hash: str, config: str) -> str:
        import onnxruntime as ort

        return "|".join(
            (
                graph_hash,
                self.provider,
                config,
                self.route.identity,
                f"ort{ort.__version__}",
                f"localsr{_localsr_version()}",
            )
        )

    def _export(self, image) -> bytes:
        import onnx

        buffer = io.BytesIO()
        with torch.inference_mode(False), torch.no_grad():
            # Use an ordinary tensor: tracing may save tensors for shape logic.
            sample = image.detach().clone()
            torch.onnx.export(
                self.source,
                (sample,),
                buffer,
                input_names=["image"],
                output_names=["restored"],
                opset_version=20,
                dynamo=False,
                external_data=False,
            )
        graph = buffer.getvalue()
        onnx.checker.check_model(graph)
        return graph

    def _create(self, graph: bytes, config: tuple):
        import onnxruntime as ort

        name, provider_options, optimization = config
        self._check_cancelled()
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        options.intra_op_num_threads = 2
        # Fused arithmetic changes NAFNet's sensitive channel normalization on
        # the tested Intel adapter. Keep its operations explicit and FP32.
        if self.architecture == "NAFNet":
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        elif optimization == "basic":
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        # Which provider got which node (ONNX Runtime 1.24); read after creation.
        options.add_session_config_entry("session.record_ep_graph_assignment_info", "1")
        # Windows keeps a profile open while its session lives; a leftover file must not
        # turn into an error.
        trace = tempfile.TemporaryDirectory(
            prefix="localsr-onnx-profile-", ignore_cleanup_errors=True
        )
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(trace.name) / "execution")
        session = None
        try:
            if self.provider == "OpenVINOExecutionProvider":
                options.add_provider_for_devices(
                    [self.route.ep_device], _provider_options(self.route, provider_options)
                )
                session = ort.InferenceSession(graph, sess_options=options)
            else:
                if self.provider not in ort.get_available_providers():
                    raise RuntimeError(f"Requested ONNX provider is unavailable: {self.provider}")
                providers = [self.provider]
                if self.provider == "DmlExecutionProvider":
                    providers = [
                        (
                            self.provider,
                            {"device_id": str(self.device_index), **provider_options},
                        ),
                        "CPUExecutionProvider",
                    ]
                session = ort.InferenceSession(graph, sess_options=options, providers=providers)
            session.disable_fallback()
            if self.route.kind == "NPU":
                # Compiling just wrote this model's program; keep the cache within its limit.
                _trim_npu_cache(_npu_cache_directory())
            if self.provider not in session.get_providers():
                raise RuntimeError(f"{self.provider} failed to initialize; select CPU explicitly.")
        except BaseException:
            self._release_session({"session": session, "trace": trace})
            raise
        return session, trace

    def _candidates(self, graph_hash: str) -> list[tuple]:
        configs = []
        if self._gated:
            self.crash_cause = self.crashes.crashed(self._crash_key()) or self._preflight()
            if self.crash_cause:
                return []
        for config in _route_configs(self.route):
            verdict = (
                self.verdicts.get(self._verdict_key(graph_hash, config[0])) if self._gated else None
            )
            if verdict is not None and not verdict.get("accepted"):
                continue
            configs.append((config, verdict is None and self._gated))
        return configs

    def _preflight(self) -> str | None:
        """Run the GPU once in a separate process before this engine opens a session on
        it; "preflight" when that process crashed on it."""
        key = self._crash_key()
        if key in gpu_guard._PREFLIGHT_TIMEOUTS:
            return "timeout"
        if (
            not self.route.device
            or os.environ.get(gpu_guard.CHILD_ENV)
            or key in gpu_guard._PREFLIGHT_TRIED
            # A stored pass only counts for a GPU that can be told apart across restarts.
            or (self.route.persist and key in self.preflight_passed)
        ):
            return None
        self._status(
            f"Testing {self._label()} in a separate process before its first use. This "
            "happens once per graphics driver."
        )
        outcome = gpu_guard.run(self.route.device, self.cancel_event)
        gpu_guard._PREFLIGHT_TRIED.add(key)
        if outcome == "timeout":
            gpu_guard._PREFLIGHT_TIMEOUTS.add(key)
            return "timeout"
        if outcome == "crashed":
            self.crashes.mark(key, "preflight")
            return "preflight"
        if outcome == "passed" and self.route.persist:
            # Without a known driver identity a later driver could not be told apart.
            self.preflight_passed.add(key)
        return None

    def _rejected(self) -> GpuRouteRejected:
        if self.crash_cause == "timeout":
            return GpuRouteRejected(
                f"{self._label()} did not finish a test run within five minutes, so LocalSR "
                "does not use that GPU for now. Select the CPU, or restart LocalSR to try again."
            )
        if self.crash_cause:
            happened = (
                f"A test run on {self._label()} crashed"
                if self.crash_cause == "preflight"
                else f"The engine crashed on {self._label()} earlier"
            )
            retry = (
                "after the next update of LocalSR or of the graphics driver"
                if self.route.persist
                else "after the next update of LocalSR"
            )
            return GpuRouteRejected(
                f"{happened}, so LocalSR does not use that GPU. Select the CPU. LocalSR "
                f"tries the GPU again {retry}."
            )
        return GpuRouteRejected(
            f"{self.architecture} gives different results on {self._label()} than on the CPU, "
            "so LocalSR does not use that GPU for this model. Select the CPU for it."
        )

    def _session(self, image):
        key = tuple(image.shape)
        cached = self.sessions.pop(key, None)
        if cached is not None:
            self.sessions[key] = cached
            return cached
        self._check_cancelled()
        # Release the oldest GPU graph before allocating another one, keeping the peak
        # at the retained cache size.
        if len(self.sessions) >= self.session_limit:
            self.sessions.popitem(last=False)
        graph = self._export(image)
        if len(graph) > _LARGE_GRAPH_BYTES:
            self.session_limit = 2
            while len(self.sessions) >= self.session_limit:
                self.sessions.popitem(last=False)
        graph_hash = hashlib.sha256(graph).hexdigest()[:24]
        candidates = self._candidates(graph_hash)
        if not candidates:
            raise self._rejected()
        state = {"graph": graph, "hash": graph_hash, "candidates": candidates}
        self._open_next(state)
        return state

    def _open_next(self, state: dict) -> None:
        """Open the next untried configuration of the route for this graph."""
        # Free the configuration that failed before allocating its replacement.
        self._release_session(state)
        gc.collect()
        while state["candidates"]:
            config, pending = state["candidates"].pop(0)
            if pending:
                self._status(
                    f"Checking that {self._label()} gives the same result as the CPU for "
                    f"{self.architecture}. This happens once per model and tile shape."
                )
            try:
                with self._watch():
                    session, trace = self._create(state["graph"], config)
            except InterruptedError:
                raise
            except Exception as error:
                if _is_device_lost(error) or _is_memory_error(error):
                    # Not a verdict on the configuration: the tile retry or a fresh
                    # worker deals with these.
                    raise
                self._check_cancelled()
                logger.warning("ONNX %s %s failed to open: %s", self.provider, config[0], error)
                self._reject(state, config[0], f"session: {error}")
                continue
            state.update(
                session=session,
                trace=trace,
                config=config,
                pending=pending,
                placement=_placement(session, self.provider),
            )
            return
        raise self._rejected()

    @staticmethod
    def _release_session(state: dict) -> None:
        """Drop the open configuration's session. Its profile is ended first: Windows keeps
        the file open, and its folder could not be removed."""
        session = state.pop("session", None)
        trace = state.pop("trace", None)
        if trace is None:
            return
        if session is not None:
            try:
                session.end_profiling()
            except Exception:  # noqa: BLE001 - this only closes the profile file
                logger.debug("Could not end the ONNX profile", exc_info=True)
        trace.cleanup()

    def _reject(self, state: dict, config: str, reason: str) -> None:
        self.verdicts.put(
            self._verdict_key(state["hash"], config), {"accepted": False, "reason": reason[:500]}
        )

    def forward(self, image):
        try:
            return self._execute(image)
        except Exception as error:
            if isinstance(error, (GpuRouteRejected, GpuDeviceLost)):
                raise
            # A lost device outranks a cancellation that arrives at the same time: only
            # a fresh worker recovers it.
            if _is_device_lost(error):
                # The sessions stay: freeing them on a lost device can hang before the
                # failure is reported, and the worker exits right after reporting it.
                self.execution_verified = False
                # The driver's own words (an HRESULT or OpenCL code) help a bug report.
                detail = (str(error).strip().splitlines() or [""])[0][:200]
                raise GpuDeviceLost(
                    f"The graphics driver stopped {self._label()} while it ran {self.architecture} "
                    "(device removed or reset). The engine restarts with a fresh device; if this "
                    "repeats, choose a smaller tile or the CPU."
                    + (f" Driver message: {detail}" if detail else "")
                ) from error
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise InterruptedError("Cancelled") from error
            if isinstance(error, InterruptedError):
                raise
            # ORT's native exceptions inherit Exception, not RuntimeError.
            # Translate allocation failures so the existing tile-size retry
            # can release GPU sessions and reduce the next allocation.
            if _is_memory_error(error):
                self.sessions.clear()
                self.execution_verified = False
                self.cpu_tiles = 0
                error = error.with_traceback(None)
                gc.collect()
                raise RuntimeError("Out of memory in the ONNX inference engine.") from error
            raise

    def _run(self, session, image) -> np.ndarray:
        import onnxruntime as ort

        options = ort.RunOptions()
        completed = threading.Event()
        cancel_event = self.cancel_event

        def watch_cancellation():
            while not completed.wait(0.1):
                if cancel_event is not None and cancel_event.is_set():
                    options.terminate = True
                    return

        watcher = None
        if cancel_event is not None:
            watcher = threading.Thread(target=watch_cancellation, daemon=True)
            watcher.start()
        try:
            self._check_cancelled()
            with self._watch():
                output = session.run(
                    ["restored"], {"image": image.detach().contiguous().numpy()}, options
                )[0]
            self._check_cancelled()
            return output
        finally:
            completed.set()
            if watcher is not None:
                watcher.join()

    def _close_profile(self, state: dict) -> set | None:
        """The providers the first run used, from the session's profile; None when the
        profile could not be read."""
        trace = state.pop("trace", None)
        if trace is None:
            return set()
        try:
            profile_path = Path(state["session"].end_profiling())
            events = json.loads(profile_path.read_text(encoding="utf-8"))
            return {
                event.get("args", {}).get("provider")
                for event in events
                if event.get("args", {}).get("provider")
            }
        except Exception:  # noqa: BLE001
            logger.debug("Could not read the ONNX profile", exc_info=True)
            return None
        finally:
            trace.cleanup()

    def _reference(self, image) -> np.ndarray:
        with torch.inference_mode():
            output = self.reference_call(self.reference_model, image)
        return output.detach().to("cpu", torch.float32).numpy()

    def _compare(self, state: dict, sample, candidate: np.ndarray | None):
        """(PSNR, max error, CPU milliseconds) for one input, or None when the CPU's own
        result is unstable there; also returns the CPU result."""
        if candidate is None:
            candidate = self._run(state["session"], sample)
        started = time.perf_counter()
        reference = self._reference(sample)
        cpu_ms = (time.perf_counter() - started) * 1000
        self._check_cancelled()
        if not np.isfinite(reference).all() or reference.min() < -4 or reference.max() > 5:
            return None, reference
        if not np.isfinite(candidate).all():
            return (-math.inf, math.inf, cpu_ms), reference
        difference = np.abs(np.clip(candidate, 0, 1) - np.clip(reference, 0, 1))
        return (_psnr(candidate, reference), float(difference.max()), cpu_ms), reference

    def _pattern_check(self, state: dict, shape: tuple[int, ...]):
        """The configuration against the CPU on the fixed test pattern, once per
        configuration and shape. A second, smoother pattern stands in when the model's CPU
        result on the first is unstable; None when neither can be used."""
        config = state["config"][0]
        cached = state.get("pattern")
        if cached is not None and cached[0] == config:
            return cached[1]
        result = None
        for smooth in (False, True):
            result, _ = self._compare(state, _test_pattern(shape, smooth=smooth), None)
            if result is not None:
                break
        state["pattern"] = (config, result)
        return result

    def _on_cpu(self, state: dict, image, reference: np.ndarray | None = None):
        """This tile through PyTorch on the CPU, while the configuration cannot be checked."""
        if reference is None:
            reference = self._reference(image)
            self._check_cancelled()
        self.cpu_tiles += 1
        if state.get("cpu_only") and not state.get("told_cpu_only"):
            state["told_cpu_only"] = True
            self._status(
                f"LocalSR cannot check {self._label()} for {self.architecture}: the model's "
                "result on the CPU is unstable on its test images. These tiles run on the CPU."
            )
        elif not state.get("told_undecided"):
            state["told_undecided"] = True
            self._status(
                f"Tiles with too little detail to check {self._label()} run on the CPU until "
                f"a detailed tile shows that it matches the CPU for {self.architecture}."
            )
        return "undecided", reference

    def _judge(self, state: dict, image, output: np.ndarray):
        """Compare a configuration under test with PyTorch on the CPU.

        Two inputs must both match: a fixed, detailed test pattern and the real tile. When
        the CPU's own result on either cannot be used, nothing is decided and the tile's
        CPU result is used instead. Returns ("accepted", output), ("rejected", None) or
        ("undecided", CPU output).
        """
        config = state["config"][0]
        pattern = self._pattern_check(state, tuple(image.shape))
        if pattern is None:
            state["cpu_only"] = True
            # These tiles never use the GPU again; release what only it needed.
            self._release_session(state)
            state.pop("graph", None)
            state["candidates"] = []
            return self._on_cpu(state, image)
        tile = None
        results = [pattern]
        if pattern[0] >= self.minimum_psnr:
            tile, reference = self._compare(state, image, output)
            if tile is None:
                return self._on_cpu(state, image, reference)
            results.append(tile)
        psnr = min(result[0] for result in results)
        accepted = psnr >= self.minimum_psnr
        cpu_ms = (tile or pattern)[2]
        gpu_ms = None
        if accepted:
            started = time.perf_counter()
            self._run(state["session"], image)
            gpu_ms = (time.perf_counter() - started) * 1000
        self.verdicts.put(
            self._verdict_key(state["hash"], config),
            {
                "accepted": accepted,
                "reason": "matched the CPU" if accepted else "did not match the CPU",
                "psnr": None if not math.isfinite(psnr) else round(psnr, 2),
                "max_error": round(max(result[1] for result in results), 6)
                if all(math.isfinite(result[1]) for result in results)
                else None,
                "checked": ["pattern", "tile"][: len(results)],
                "gpu_ms": None if gpu_ms is None else round(gpu_ms, 1),
                "cpu_ms": round(cpu_ms, 1),
                "shape": list(image.shape),
                "architecture": self.architecture,
                "device": self._label(),
                "placement": state.get("placement"),
            },
        )
        logger.info(
            "GPU check %s on %s (%s): %s, PSNR %.1f dB, %s ms vs CPU %.0f ms",
            self.architecture,
            self._label(),
            config,
            "accepted" if accepted else "rejected",
            psnr,
            gpu_ms,
            cpu_ms,
        )
        if not accepted:
            return "rejected", None
        state["pending"] = False
        self._announce(state, config, gpu_ms, cpu_ms)
        return "accepted", output

    def _announce(self, state: dict, config: str, gpu_ms: float | None, cpu_ms: float | None):
        placement = state.get("placement") or {}
        if placement.get("cpu_heavy") or placement.get("cpu_subgraphs", 0) > 2:
            self._status(
                f"Part of {self.architecture} runs on the CPU on {self._label()} "
                f"({placement['cpu_nodes']} of {placement['nodes']} operations), "
                "which costs speed but not accuracy."
            )
        if gpu_ms is not None and cpu_ms is not None and gpu_ms > cpu_ms:
            self._status(
                f"{self.architecture} runs on {self._label()}, but slower than on the CPU here "
                f"({gpu_ms:.0f} ms against {cpu_ms:.0f} ms per tile)."
            )
        else:
            fallback = "" if config == _route_configs(self.route)[0][0] else f" ({config})"
            self._status(f"{self.architecture} runs on {self._label()}{fallback}.")

    def _attempt(self, state: dict, image, expected) -> tuple[str, np.ndarray | None]:
        """Run one tile on the open configuration: ("accepted" | "rejected" | "undecided",
        output). Errors that are not about the configuration propagate."""
        if state.get("cpu_only"):
            return self._on_cpu(state, image)
        if state["pending"] and float(image.std()) < _DETAILED_TILE_STD:
            # Too flat to tell results apart: no GPU run, the CPU's result is used.
            return self._on_cpu(state, image)
        first_run = "trace" in state
        output = self._run(state["session"], image)
        providers = self._close_profile(state) if first_run else set()
        if output.shape != expected:
            raise ValueError("ONNX returned an invalid image shape")
        if first_run and providers is None:
            placement = state.get("placement")
            if placement and placement["nodes"] > placement["cpu_nodes"]:
                providers = {self.provider}
            elif self._gated and not state.get("reprofiled"):
                # Where the tile ran is unknown: no verdict, the CPU's result, and the
                # configuration opens again once with a fresh profile for the next tile.
                state["reprofiled"] = True
                state["candidates"].insert(0, (state["config"], state["pending"]))
                self._open_next(state)
                self._status(
                    f"LocalSR could not tell where {self.architecture} ran on {self._label()}; "
                    "this tile ran on the CPU, and the next one checks again."
                )
                return self._on_cpu(state, image)
            elif self._gated:
                # Still unknown: this shape stays on the CPU until the model loads again.
                state["cpu_only"] = state["told_cpu_only"] = True
                self._release_session(state)
                state.pop("graph", None)
                state["candidates"] = []
                self._status(
                    f"LocalSR still could not tell where {self.architecture} ran on "
                    f"{self._label()}; these tiles run on the CPU."
                )
                return self._on_cpu(state, image)
            else:
                providers = set()
        if first_run and self.provider not in providers:
            if not self._gated:
                raise RuntimeError(
                    f"The model did not execute on {self.provider}, or execution was not profiled."
                )
            self._reject(state, state["config"][0], "no node ran on the GPU")
            return "rejected", None
        if first_run:
            logger.info("ONNX %s executed with providers: %s", self.architecture, sorted(providers))
        if state["pending"]:
            return self._judge(state, image, output)
        if not np.isfinite(output).all():
            raise ValueError("ONNX returned an invalid image shape or non-finite pixels")
        if first_run:
            self._announce(state, state["config"][0], None, None)
        return "accepted", output

    def _execute(self, image):
        self._check_cancelled()
        if image.device.type != "cpu" or image.dtype != torch.float32:
            raise ValueError("ONNX image staging requires CPU FP32 tensors")
        key = tuple(image.shape)
        state = self._session(image)
        expected = (
            image.shape[0],
            self.output_channels,
            image.shape[2] * self.scale,
            image.shape[3] * self.scale,
        )
        try:
            return self._advance(state, key, image, expected)
        except BaseException:
            # A switch to the next configuration that was interrupted (cancelled, out of
            # memory) leaves no session; drop it so the next tile starts over from the
            # stored verdicts, where an untried configuration is still untried.
            if (
                "session" not in state
                and not state.get("cpu_only")
                and self.sessions.get(key) is state
            ):
                del self.sessions[key]
            raise

    def _advance(self, state: dict, key: tuple, image, expected):
        while True:
            failure = None
            # A configuration that fails on its first tile (or during its check) is
            # rejected like one whose result differs, accepted before or not; memory
            # errors, a lost device and cancellation are not about the configuration.
            fallible = self._gated and ("trace" in state or state.get("pending"))
            try:
                outcome, output = self._attempt(state, image, expected)
            except InterruptedError:
                raise
            except Exception as error:
                if not fallible or _is_device_lost(error) or _is_memory_error(error):
                    raise
                # Cancelling stops ONNX Runtime with an ordinary error; that says nothing
                # about the configuration.
                self._check_cancelled()
                failure = f"run: {error}"
                logger.warning(
                    "ONNX %s %s failed on its first tile: %s",
                    self.provider,
                    state["config"][0],
                    error,
                )
            if failure is not None:
                self._reject(state, state["config"][0], failure)
                self._open_next(state)
                continue
            if outcome == "rejected":
                self._open_next(state)
                continue
            # The session stays cached either way; an undecided tile keeps checking.
            self.sessions[key] = state
            while len(self.sessions) > self.session_limit:
                self.sessions.popitem(last=False)
            if outcome == "accepted":
                self.execution_verified = True
                # The serialized graph is needed only to open another configuration.
                state.pop("graph", None)
                state["candidates"] = []
            return torch.from_numpy(np.ascontiguousarray(output))


# Operations that carry a model's work; one of these on the CPU makes a GPU route slow.
_HEAVY_OPERATIONS = {"Conv", "ConvTranspose", "MatMul", "Gemm", "Attention", "FusedConv"}


def _placement(session, provider: str) -> dict | None:
    """How many nodes, and which heavy ones, ONNX Runtime gave to the CPU instead."""
    try:
        subgraphs = session.get_provider_graph_assignment_info()
    except (AttributeError, RuntimeError):
        return None
    nodes = cpu_nodes = cpu_subgraphs = 0
    heavy: set[str] = set()
    for subgraph in subgraphs:
        assigned = subgraph.get_nodes()
        nodes += len(assigned)
        if subgraph.ep_name == "CPUExecutionProvider" and provider != "CPUExecutionProvider":
            cpu_nodes += len(assigned)
            cpu_subgraphs += 1
            heavy.update(node.op_type for node in assigned if node.op_type in _HEAVY_OPERATIONS)
    return {
        "nodes": nodes,
        "cpu_nodes": cpu_nodes,
        "cpu_subgraphs": cpu_subgraphs,
        "cpu_heavy": sorted(heavy),
    }


def _test_pattern(shape: tuple[int, ...], smooth: bool = False) -> torch.Tensor:
    """A fixed, detailed image of the given (N, C, H, W) shape: gradients, fine and
    coarse waves, hard edges and light noise, all within [0, 1]. The smooth variant has
    soft waves and faint noise only, for models whose CPU result on hard edges is unstable."""
    _, channels, height, width = shape
    y = torch.linspace(0, 1, height).view(height, 1)
    x = torch.linspace(0, 1, width).view(1, width)
    if smooth:
        soft = torch.sin(2 * math.pi * (x + y)) * torch.cos(2 * math.pi * 2 * x)
        grain = torch.randn(height, width, generator=torch.Generator().manual_seed(20261109))
        planes = [
            (0.5 + (0.22 + 0.03 * channel) * soft + 0.015 * grain).clamp(0, 1)
            for channel in range(channels)
        ]
        return torch.stack(planes).unsqueeze(0).expand(shape[0], -1, -1, -1).contiguous()
    waves = 0.5 + 0.3 * torch.sin(2 * math.pi * (3 * x + 2 * y)) * torch.cos(
        2 * math.pi * 7 * x * y
    )
    checker = (
        (torch.arange(height).view(height, 1) // 6 + torch.arange(width).view(1, width) // 6) % 2
    ).float()
    noise = torch.randn(height, width, generator=torch.Generator().manual_seed(20261108))
    planes = [
        (0.55 * waves + 0.3 * checker * (0.6 + 0.2 * channel) + 0.04 * noise).clamp(0, 1)
        for channel in range(channels)
    ]
    return torch.stack(planes).unsqueeze(0).expand(shape[0], -1, -1, -1).contiguous()


def _is_memory_error(error: BaseException) -> bool:
    # NumPy's "Unable to allocate ..." is a MemoryError too.
    if isinstance(error, MemoryError):
        return True
    message = str(error).lower()
    return any(fragment in message for fragment in _MEMORY_FRAGMENTS)


def _is_device_lost(error: BaseException) -> bool:
    message = str(error).lower()
    return any(fragment in message for fragment in _DEVICE_LOST_FRAGMENTS)


class OnnxImageDescriptor:
    def __init__(
        self,
        descriptor,
        *,
        provider="DmlExecutionProvider",
        device_index=0,
        route: OnnxRoute | None = None,
    ):
        route = route or OnnxRoute(provider=provider, device_index=device_index)
        self.descriptor = copy.copy(descriptor)
        self.descriptor._model = _OnnxImage(descriptor, route).eval()
        # Auxiliary outputs are already handled by _RawImage before export.
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
        # An NPU compiles every input shape on its own (seconds to minutes, and a cached
        # program as large as the model), so it gets one: the engine then cuts edge tiles
        # as full windows of real pixels, as for Core ML.
        if self.model.route.kind == "NPU":
            side = tile_size + 2 * halo
            self.model.fixed_shape = (side, side)

    @property
    def fixed_input_shape(self) -> tuple[int, int] | None:
        return self.model.fixed_shape

    @property
    def execution_provider(self):
        return self.model.provider

    @property
    def execution_verified(self):
        """At least one tile ran on the checked GPU route, and none fell back to the CPU."""
        return self.model.execution_verified and not self.model.cpu_tiles

    def reset_execution_evidence(self) -> None:
        """Start counting afresh; sessions and verdicts stay."""
        self.model.execution_verified = False
        self.model.cpu_tiles = 0
