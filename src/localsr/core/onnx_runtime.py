"""ONNX image execution with the same Spandrel padding and range contract.

Graphs are converted from the checkpoint already verified by ModelAdapter. They
stay in memory and are bounded to two input shapes per loaded model. Checkpoints
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
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

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
GATE_CACHE_ENV = "LOCALSR_ONNX_GATE_CACHE"
_MAXIMUM_VERDICTS = 400
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
    "e_outofmemory",
    "0x8007000e",
    "bad allocation",
    "bad_alloc",
    "allocation failed",
)
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

    def __post_init__(self):
        if self.provider not in ROUTE_CONFIGS or self.device_index < 0:
            raise ValueError("Invalid ONNX execution provider or device index")
        if self.provider == "OpenVINOExecutionProvider" and self.ep_device is None:
            raise ValueError("The OpenVINO route needs an OpenVINO device")


def gate_cache_directory() -> Path:
    override = os.environ.get(GATE_CACHE_ENV)
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "LocalSR" / "gpu-checks"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "LocalSR" / "gpu-checks"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "LocalSR" / "gpu-checks"


class _Verdicts:
    """Stored first-tile checks, keyed by graph, route, configuration, device and versions."""

    def __init__(self, directory: Path):
        self.path = directory / "verdicts.json"

    def _read(self) -> dict:
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
        self.provider = route.provider
        self.device_index = route.device_index
        self.sessions = OrderedDict()
        self.cancel_event: threading.Event | None = None
        self.status_callback = None
        self.execution_verified = False
        self.verdicts = _Verdicts(gate_cache_directory())
        requirements = descriptor.size_requirements
        self.minimum = int(getattr(requirements, "minimum", 0) or 0)
        self.alignment = max(1, int(getattr(requirements, "multiple_of", 1) or 1))
        # (height, width) every tile is cut to, so one graph serves the whole image.
        self.shape: tuple[int, int] | None = None

    def prepare(self, tile_size: int, halo: int) -> None:
        side = max(tile_size + 2 * halo, self.minimum, 1)
        side = -(-side // self.alignment) * self.alignment
        current = self.shape
        # Keep a larger existing graph for smaller tiles, but not one so much larger
        # that padding would waste most of the work (the out-of-memory retry halves tiles).
        if current is None or side > current[0] or current[0] * current[1] > 1.5 * side * side:
            self.shape = (side, side)

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
        trace = tempfile.TemporaryDirectory(prefix="localsr-onnx-profile-")
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(trace.name) / "execution")
        try:
            if self.provider == "OpenVINOExecutionProvider":
                options.add_provider_for_devices([self.route.ep_device], dict(provider_options))
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
            if self.provider not in session.get_providers():
                raise RuntimeError(f"{self.provider} failed to initialize; select CPU explicitly.")
        except BaseException:
            trace.cleanup()
            raise
        return session, trace

    def _candidates(self, graph_hash: str) -> list[tuple]:
        configs = []
        for config in ROUTE_CONFIGS[self.provider]:
            verdict = (
                self.verdicts.get(self._verdict_key(graph_hash, config[0])) if self._gated else None
            )
            if verdict is not None and not verdict.get("accepted"):
                continue
            configs.append((config, verdict is None and self._gated))
        return configs

    def _rejected(self) -> GpuRouteRejected:
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
        # Release the oldest GPU graph before allocating another one, keeping
        # the peak at two sessions as well as the retained cache size.
        if len(self.sessions) >= 2:
            self.sessions.popitem(last=False)
        graph = self._export(image)
        graph_hash = hashlib.sha256(graph).hexdigest()[:24]
        candidates = self._candidates(graph_hash)
        if not candidates:
            raise self._rejected()
        state = {"graph": graph, "hash": graph_hash, "candidates": candidates}
        self._open_next(state)
        return state

    def _open_next(self, state: dict) -> None:
        """Open the next untried configuration of the route for this graph."""
        while state["candidates"]:
            config, pending = state["candidates"].pop(0)
            if pending:
                self._status(
                    f"Checking that {self._label()} gives the same result as the CPU for "
                    f"{self.architecture}. This happens once per model and tile size."
                )
            try:
                session, trace = self._create(state["graph"], config)
            except InterruptedError:
                raise
            except Exception as error:
                if _is_device_lost(error):
                    raise
                if not pending:
                    raise
                logger.warning("ONNX %s %s failed to open: %s", self.provider, config[0], error)
                self.verdicts.put(
                    self._verdict_key(state["hash"], config[0]),
                    {"accepted": False, "reason": f"session: {error}"[:500]},
                )
                continue
            state.update(session=session, trace=trace, config=config, pending=pending)
            return
        raise self._rejected()

    def forward(self, image):
        try:
            return self._execute(image)
        except Exception as error:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise InterruptedError("Cancelled") from error
            if isinstance(error, (GpuRouteRejected, GpuDeviceLost, InterruptedError)):
                raise
            if _is_device_lost(error):
                self.sessions.clear()
                self.execution_verified = False
                # The traceback would keep the failed tile's session, and its memory, alive.
                error = error.with_traceback(None)
                gc.collect()
                raise GpuDeviceLost(
                    f"The graphics driver stopped {self._label()} while it ran {self.architecture} "
                    "(device removed or reset). The engine restarts with a fresh device; if this "
                    "repeats, choose a smaller tile or the CPU."
                ) from error
            # ORT's native exceptions inherit Exception, not RuntimeError.
            # Translate allocation failures so the existing tile-size retry
            # can release GPU sessions and reduce the next allocation.
            message = str(error).lower()
            if any(fragment in message for fragment in _MEMORY_FRAGMENTS):
                self.sessions.clear()
                self.execution_verified = False
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
            output = session.run(
                ["restored"], {"image": image.detach().contiguous().numpy()}, options
            )[0]
            self._check_cancelled()
            return output
        finally:
            completed.set()
            if watcher is not None:
                watcher.join()

    def _close_profile(self, state: dict) -> set:
        """The providers the first run used, from the session's profile."""
        trace = state.pop("trace", None)
        if trace is None:
            return set()
        try:
            profile_path = Path(state["session"].end_profiling())
            events = json.loads(profile_path.read_text())
            return {
                event.get("args", {}).get("provider")
                for event in events
                if event.get("args", {}).get("provider")
            }
        except Exception:  # noqa: BLE001
            logger.debug("Could not read the ONNX profile", exc_info=True)
            return set()
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

    def _judge(self, state: dict, image, output: np.ndarray) -> np.ndarray | None:
        """Compare a pending configuration with PyTorch on the CPU.

        Two inputs: a fixed, detailed test pattern (a flat first tile such as sky or a
        black border would let almost anything pass) and the real tile. Returns the
        output to use, or None when the configuration is rejected. When the CPU's own
        result is unstable on both, the CPU output is used and the check repeats on the
        next tile.
        """
        config = state["config"][0]
        pattern, _ = self._compare(state, _test_pattern(tuple(image.shape)), None)
        tile, reference = self._compare(state, image, output)
        results = [result for result in (pattern, tile) if result is not None]
        if not results:
            return reference
        psnr = min(result[0] for result in results)
        accepted = psnr >= MINIMUM_PSNR
        cpu_ms = results[-1][2]
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
                "checked": ["pattern"] * (pattern is not None) + ["tile"] * (tile is not None),
                "gpu_ms": None if gpu_ms is None else round(gpu_ms, 1),
                "cpu_ms": round(cpu_ms, 1),
                "shape": list(image.shape),
                "architecture": self.architecture,
                "device": self._label(),
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
            return None
        state["pending"] = False
        state.pop("graph", None)
        if gpu_ms is not None and gpu_ms > cpu_ms:
            self._status(
                f"{self.architecture} runs on {self._label()}, but slower than on the CPU here "
                f"({gpu_ms:.0f} ms against {cpu_ms:.0f} ms per tile)."
            )
        else:
            fallback = "" if config == ROUTE_CONFIGS[self.provider][0][0] else f" ({config})"
            self._status(f"{self.architecture} runs on {self._label()}{fallback}.")
        return output if tile is not None else reference

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
        while True:
            try:
                output = self._run(state["session"], image)
            except InterruptedError:
                raise
            except Exception as error:
                message = str(error).lower()
                untried = state.get("pending") and not _is_device_lost(error)
                if not untried or any(fragment in message for fragment in _MEMORY_FRAGMENTS):
                    raise
                # A configuration still under test that fails to run is rejected like
                # one whose result differs; the next configuration gets the tile.
                logger.warning(
                    "ONNX %s %s failed on its first tile: %s",
                    self.provider,
                    state["config"][0],
                    error,
                )
                trace = state.pop("trace", None)
                if trace is not None:
                    trace.cleanup()
                self.verdicts.put(
                    self._verdict_key(state["hash"], state["config"][0]),
                    {"accepted": False, "reason": f"run: {error}"[:500]},
                )
                self._open_next(state)
                continue
            first_run = "trace" in state
            providers = self._close_profile(state) if first_run else set()
            if output.shape != expected:
                raise ValueError("ONNX returned an invalid image shape")
            if first_run and self.provider not in providers:
                if not state["pending"]:
                    raise RuntimeError(
                        f"The model did not execute on {self.provider}; select CPU explicitly."
                    )
                self.verdicts.put(
                    self._verdict_key(state["hash"], state["config"][0]),
                    {"accepted": False, "reason": "no node ran on the GPU"},
                )
                self._open_next(state)
                continue
            if first_run:
                logger.info(
                    "ONNX %s executed with providers: %s", self.architecture, sorted(providers)
                )
            if state["pending"]:
                judged = self._judge(state, image, output)
                if judged is None:
                    self._open_next(state)
                    continue
                output = judged
            elif not np.isfinite(output).all():
                raise ValueError("ONNX returned an invalid image shape or non-finite pixels")
            self.execution_verified = True
            self.sessions[key] = state
            while len(self.sessions) > 2:
                self.sessions.popitem(last=False)
            return torch.from_numpy(np.ascontiguousarray(output))


def _test_pattern(shape: tuple[int, ...]) -> torch.Tensor:
    """A fixed, detailed image of the given (N, C, H, W) shape: gradients, fine and
    coarse waves, hard edges and light noise, all within [0, 1]."""
    _, channels, height, width = shape
    y = torch.linspace(0, 1, height).view(height, 1)
    x = torch.linspace(0, 1, width).view(1, width)
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

    def prepare_tiles(self, tile_size: int, halo: int, frame=None):
        # Whole frames vary in size; tiles share one shape cut from real pixels, so an
        # image's edge tiles do not each need their own export, compile and GPU check.
        self.model.prepare(tile_size, halo)

    @property
    def fixed_input_shape(self) -> tuple[int, int] | None:
        return self.model.shape

    @property
    def execution_provider(self):
        return self.model.provider

    @property
    def execution_verified(self):
        return self.model.execution_verified
