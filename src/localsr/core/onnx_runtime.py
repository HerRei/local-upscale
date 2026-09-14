"""ONNX image execution with the same Spandrel padding and range contract.

Graphs are converted from the checkpoint already verified by ModelAdapter. They
stay in memory and are bounded to two input shapes per loaded model. Checkpoints
and user settings are never rewritten by conversion.
"""

import copy
import gc
import io
import json
import logging
import tempfile
import threading
from collections import OrderedDict
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


class _OnnxImage(torch.nn.Module):
    def __init__(self, descriptor, provider: str, device_index: int):
        super().__init__()
        self.source = _RawImage(descriptor)
        self.architecture = descriptor.architecture.name
        self.scale = descriptor.scale
        self.output_channels = descriptor.output_channels
        self.provider = provider
        self.device_index = device_index
        self.sessions = OrderedDict()
        self.cancel_event: threading.Event | None = None
        self.execution_verified = False

    def _check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise InterruptedError("Cancelled")

    def _session(self, image):
        import onnx
        import onnxruntime as ort

        key = tuple(image.shape)
        cached = self.sessions.pop(key, None)
        if cached is not None:
            self.sessions[key] = cached
            return cached, None
        self._check_cancelled()
        # Release the oldest GPU graph before allocating another one, keeping
        # the peak at two sessions as well as the retained cache size.
        if len(self.sessions) >= 2:
            self.sessions.popitem(last=False)
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
        self._check_cancelled()
        ort.disable_telemetry_events()
        if self.provider not in ort.get_available_providers():
            raise RuntimeError(f"Requested ONNX provider is unavailable: {self.provider}")
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        options.intra_op_num_threads = 2
        # Fused arithmetic changes NAFNet's sensitive channel normalization on
        # the tested Intel adapter. Keep its operations explicit and FP32.
        if self.architecture == "NAFNet":
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        trace = tempfile.TemporaryDirectory(prefix="localsr-onnx-profile-")
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(trace.name) / "execution")
        providers = [self.provider]
        if self.provider == "DmlExecutionProvider":
            providers = [
                (self.provider, {"device_id": str(self.device_index)}),
                "CPUExecutionProvider",
            ]
        try:
            session = ort.InferenceSession(graph, sess_options=options, providers=providers)
            session.disable_fallback()
            if self.provider not in session.get_providers():
                raise RuntimeError(f"{self.provider} failed to initialize; select CPU explicitly.")
        except BaseException:
            trace.cleanup()
            raise
        return session, trace

    def forward(self, image):
        try:
            return self._execute(image)
        except Exception as error:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise InterruptedError("Cancelled") from error
            # ORT's native exceptions inherit Exception, not RuntimeError.
            # Translate allocation failures so the existing tile-size retry
            # can release GPU sessions and reduce the next allocation.
            message = str(error).lower()
            if any(
                fragment in message
                for fragment in (
                    "out of memory",
                    "not enough memory",
                    "e_outofmemory",
                    "0x8007000e",
                    "bad allocation",
                    "bad_alloc",
                    "allocation failed",
                )
            ):
                self.sessions.clear()
                self.execution_verified = False
                gc.collect()
                raise RuntimeError("Out of memory in the ONNX inference engine.") from error
            raise

    def _execute(self, image):
        import onnxruntime as ort

        self._check_cancelled()
        if image.device.type != "cpu" or image.dtype != torch.float32:
            raise ValueError("ONNX image staging requires CPU FP32 tensors")
        session, trace = self._session(image)
        options = ort.RunOptions()
        completed = threading.Event()
        cancel_event = self.cancel_event

        def watch_cancellation():
            while not completed.wait(0.1):
                if cancel_event is not None and cancel_event.is_set():
                    options.terminate = True
                    return

        watcher = None
        profile_closed = False
        if cancel_event is not None:
            watcher = threading.Thread(target=watch_cancellation, daemon=True)
            watcher.start()
        try:
            self._check_cancelled()
            output = session.run(
                ["restored"], {"image": image.detach().contiguous().numpy()}, options
            )[0]
            self._check_cancelled()
            expected = (
                image.shape[0],
                self.output_channels,
                image.shape[2] * self.scale,
                image.shape[3] * self.scale,
            )
            if output.shape != expected or not np.isfinite(output).all():
                raise ValueError("ONNX returned an invalid image shape or non-finite pixels")
            if trace is not None:
                profile_path = Path(session.end_profiling())
                profile_closed = True
                events = json.loads(profile_path.read_text())
                providers = {
                    event.get("args", {}).get("provider")
                    for event in events
                    if event.get("args", {}).get("provider")
                }
                if self.provider not in providers:
                    raise RuntimeError(
                        f"The model did not execute on {self.provider}; select CPU explicitly."
                    )
                self.execution_verified = True
                logger.info(
                    "ONNX %s executed with providers: %s", self.architecture, sorted(providers)
                )
                self.sessions[tuple(image.shape)] = session
                while len(self.sessions) > 2:
                    self.sessions.popitem(last=False)
            return torch.from_numpy(output)
        finally:
            completed.set()
            if watcher is not None:
                watcher.join()
            if trace is not None:
                if not profile_closed:
                    try:
                        session.end_profiling()
                    except Exception:
                        logger.debug("Could not close failed ONNX profile", exc_info=True)
                trace.cleanup()


class OnnxImageDescriptor:
    def __init__(self, descriptor, *, provider="DmlExecutionProvider", device_index=0):
        if provider not in {"DmlExecutionProvider", "CPUExecutionProvider"} or device_index < 0:
            raise ValueError("Invalid ONNX execution provider or device index")
        self.descriptor = copy.copy(descriptor)
        self.descriptor._model = _OnnxImage(descriptor, provider, device_index).eval()
        # Auxiliary outputs are already handled by _RawImage before export.
        self.descriptor._call_fn = lambda model, image: model(image)

    def __getattr__(self, name):
        return getattr(self.descriptor, name)

    def __call__(self, image):
        return self.descriptor(image)

    def set_cancel_event(self, cancel_event):
        self.model.cancel_event = cancel_event

    @property
    def execution_provider(self):
        return self.model.provider

    @property
    def execution_verified(self):
        return self.model.execution_verified
