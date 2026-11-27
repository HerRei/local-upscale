import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from math import lcm

import spandrel
import torch

from localsr.core.model_catalog import (
    CATALOG_BY_FILENAME,
    file_stamp,
    is_settled_file,
    verify_file_checksum,
)
from localsr.core.preparation import PreparationTimeout, bounded_preparation
from localsr.image_models.heart import heart_support
from localsr.image_models.nerve import nerve_support
from localsr.image_models.pulse import pulse_support

# Reading a checkpoint is a local file read; a drive that stopped answering would
# otherwise hold the job for ever.
MODEL_READ_SECONDS = 180.0

UNVERIFIED_CHECKPOINT_ENV = "LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS"
RISKY_CHECKPOINT_SUFFIXES = {".ckpt", ".pt", ".pth"}

# Spandrel's own registry plus architectures LocalSR vendors under permissive
# licences (NERVE, HEART, PULSE). A copy, so Spandrel's global registry stays
# unchanged; the added architectures are detected after Spandrel's own.
MODEL_REGISTRY = spandrel.MAIN_REGISTRY.copy()
MODEL_REGISTRY.add(nerve_support(), heart_support(), pulse_support())


def _unsupported_model_message(state_dict) -> str:
    keys = set(state_dict)
    hints = (
        ("MoESR", {"in_to_dim.weight", "blocks.0.blocks.0.conv.dwconv_hw.weight"}),
        ("MoSR", {"gblocks.0.weight", "gblocks.1.fc1.weight", "gblocks.1.conv.weight"}),
    )
    for name, required in hints:
        if required <= keys:
            return (
                f"This checkpoint seems to use {name}, but this model is not supported yet. "
                "See https://github.com/HerRei/local-upscale/issues/154"
            )
    return "This checkpoint's architecture could not be recognized and is not supported."


class CheckpointSecurityError(RuntimeError):
    """Raised before an untrusted pickle or TorchScript checkpoint reaches PyTorch."""


def unverified_checkpoints_enabled() -> bool:
    return os.environ.get(UNVERIFIED_CHECKPOINT_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass
class NormalizedModelInfo:
    architecture: str
    scale: int
    in_channels: int
    out_channels: int
    tiling_supported: bool
    half_supported: bool
    size_requirements_min: int
    size_requirements_mult: int
    filename: str
    warnings: list[str]
    size_requirements_square: bool = False


class _DirectMLDescriptor:
    """Keep Spandrel's image contract while using DirectML-compatible no-grad.

    Spandrel 0.4.2 decorates __call__ with inference_mode. Torch-DirectML 0.2.5
    cannot execute convolutions with those tensors (version_counter error).
    Unwrap that decorator per instance, retaining padding, call_fn adaptation,
    cropping and range clamping. Other devices keep the original descriptor.
    """

    def __init__(self, descriptor):
        self.descriptor = descriptor
        self._call = getattr(type(descriptor).__call__, "__wrapped__", None)
        if self._call is None:
            raise RuntimeError("This Spandrel descriptor has no compatible DirectML call path")

    def __getattr__(self, name):
        return getattr(self.descriptor, name)

    def __call__(self, image):
        with torch.inference_mode(False), torch.no_grad():
            if torch.is_inference(image):
                image = image.clone()
            return self._call(self.descriptor, image)


class ModelAdapter:
    def __init__(self, *, allow_unverified_checkpoints: bool | None = None):
        self.loader = spandrel.ModelLoader(registry=MODEL_REGISTRY)
        self.approved_checkpoint: tuple[str, str] | None = None
        self.parsed = None
        self.parsed_path = None
        self._info_cache: dict[
            str, tuple[tuple[int, int, int, int, int], str, NormalizedModelInfo]
        ] = {}
        self.status_callback: Callable[[str], None] | None = None
        self.cancel_event: threading.Event | None = None
        self.allow_unverified_checkpoints = (
            unverified_checkpoints_enabled()
            if allow_unverified_checkpoints is None
            else allow_unverified_checkpoints
        )

    def _read(self, path: str, *, trust: str | None = None):
        """The checkpoint's trust class and parsed model, within MODEL_READ_SECONDS."""
        name = os.path.basename(path)

        def read():
            resolved_trust = trust if trust is not None else self._checkpoint_trust(path)
            try:
                return resolved_trust, self.loader.load_from_file(path)
            except spandrel.UnsupportedModelError as error:
                try:
                    state_dict = self.loader.load_state_dict_from_file(path)
                except Exception:
                    state_dict = None
                if state_dict is not None:
                    from localsr.image_models.ddcolor import (
                        is_ddcolor_state_dict,
                        load_ddcolor_descriptor,
                    )

                    if is_ddcolor_state_dict(state_dict):
                        return resolved_trust, load_ddcolor_descriptor(state_dict)
                    message = _unsupported_model_message(state_dict)
                else:
                    message = "This checkpoint's architecture could not be recognized and is not supported."
                raise RuntimeError(message) from error

        try:
            return bounded_preparation(
                f"Reading {name}",
                MODEL_READ_SECONDS,
                read,
                fallback="the job stops",
                slot="model-read",
                cancel_event=self.cancel_event,
                status=self.status_callback,
            )
        except PreparationTimeout as error:
            raise RuntimeError(
                f"Reading the model file {name} did not finish within "
                f"{MODEL_READ_SECONDS:.0f} s. Check that the drive it is on is connected, "
                "then try again."
            ) from error

    def _checkpoint_trust(self, path: str) -> str:
        candidate = os.path.abspath(path)
        suffix = os.path.splitext(candidate)[1].lower()
        catalog_model = CATALOG_BY_FILENAME.get(os.path.basename(candidate))
        if catalog_model is not None:
            if not verify_file_checksum(
                candidate, catalog_model.sha256, expected_size=catalog_model.size_bytes
            ):
                raise CheckpointSecurityError(
                    f"{catalog_model.name} failed its catalog SHA-256 check. "
                    "LocalSR refused to deserialize it; remove the file and download it again."
                )
            return "catalog-verified"
        if suffix in RISKY_CHECKPOINT_SUFFIXES:
            if self.approved_checkpoint:
                # A job that carries an approval may load exactly that file, as approved.
                if candidate != os.path.abspath(self.approved_checkpoint[0]):
                    raise CheckpointSecurityError(
                        "Only the checkpoint you confirmed can load in this job."
                    )
                if not verify_file_checksum(candidate, self.approved_checkpoint[1]):
                    raise CheckpointSecurityError(
                        "The approved checkpoint changed. Confirm it again."
                    )
                return "explicitly-trusted"
            if not self.allow_unverified_checkpoints:
                raise CheckpointSecurityError(
                    "LocalSR blocks unverified .pth, .pt, and .ckpt checkpoints because they may "
                    "execute code while loading. Use a .safetensors checkpoint or, only for a "
                    f"checkpoint you have independently trusted, set {UNVERIFIED_CHECKPOINT_ENV}=1."
                )
            return "explicitly-trusted"
        return "safe-format"

    def inspect(self, path: str) -> NormalizedModelInfo:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Model file not found: {path}")

        # Note: Spandrel uses safe load by default where possible, but .pth can be unsafe.
        normalized_path = os.path.abspath(path)
        stat = None
        stamp = None
        settled = False
        try:
            stat = os.stat(normalized_path)
            stamp = file_stamp(stat)
            settled = is_settled_file(stat)
        except OSError:
            pass

        if self.parsed is None or self.parsed_path != normalized_path:
            checkpoint_trust = self._checkpoint_trust(normalized_path)
            if settled and stamp is not None:
                cached = self._info_cache.get(normalized_path)
                if cached is not None and cached[0] == stamp and cached[1] == checkpoint_trust:
                    return replace(cached[2], warnings=list(cached[2].warnings))
            checkpoint_trust, self.parsed = self._read(normalized_path, trust=checkpoint_trust)
            self.parsed_path = normalized_path
        else:
            checkpoint_trust = "cached"

        arch = self.parsed.architecture.name
        scale = self.parsed.scale

        in_c = getattr(self.parsed, "input_channels", getattr(self.parsed, "in_channels", 3))
        out_c = getattr(self.parsed, "output_channels", getattr(self.parsed, "out_channels", 3))

        # Spandrel 0.4 does not expose a tiling capability flag. LocalSR tiles
        # ImageModelDescriptor inputs itself and applies the descriptor's size
        # requirements to every tile; DDColor runs whole-image at its input size.
        tiling_supported = arch != "DDColor"

        half_supported = getattr(self.parsed, "supports_half", False)

        req_min = 0
        req_mult = 1
        req_square = False
        if hasattr(self.parsed, "size_requirements"):
            req = self.parsed.size_requirements
            req_min = getattr(req, "minimum", 0)
            req_mult = getattr(req, "multiple_of", 1)
            req_square = getattr(req, "square", False)

        # Some transformer descriptors (including HAT in Spandrel 0.4.2)
        # expose neutral SizeRequirements even though their model requires
        # inputs aligned to its attention window.
        window_size = getattr(self.parsed.model, "window_size", None)
        if isinstance(window_size, int) and window_size > 1:
            req_mult = lcm(req_mult, window_size)
        elif isinstance(window_size, (tuple, list)):
            for dimension in window_size:
                if isinstance(dimension, int) and dimension > 1:
                    req_mult = lcm(req_mult, dimension)

        warnings = []
        if checkpoint_trust == "explicitly-trusted":
            warnings.append(
                "Security warning: loading this unverified pickle/TorchScript checkpoint was "
                "explicitly enabled. Treat it as executable code."
            )

        info = NormalizedModelInfo(
            architecture=arch,
            scale=scale,
            in_channels=in_c,
            out_channels=out_c,
            tiling_supported=tiling_supported,
            half_supported=half_supported,
            size_requirements_min=req_min,
            size_requirements_mult=req_mult,
            filename=os.path.basename(path),
            warnings=warnings,
            size_requirements_square=req_square,
        )
        if settled and stamp is not None and checkpoint_trust != "cached":
            self._info_cache[normalized_path] = (stamp, checkpoint_trust, info)
        return info

    def load(
        self,
        path: str,
        device: torch.device,
        precision: torch.dtype,
        *,
        onnx_route=None,
        coreml_units: tuple[str, ...] = (),
    ):
        normalized_path = os.path.abspath(path)
        if self.parsed is None or self.parsed_path != normalized_path:
            _, self.parsed = self._read(normalized_path)
            self.parsed_path = normalized_path
        # Run through Spandrel's descriptor rather than reaching into its raw
        # architecture module. Most SR modules return a tensor directly, but
        # restoration architectures such as FBCNN expose auxiliary outputs.
        # The descriptor normalizes those architecture-specific signatures to
        # the single-image tensor contract used by LocalSR.
        descriptor = self.parsed.to(device).to(precision)
        descriptor.eval()
        if onnx_route is not None:
            from .onnx_runtime import OnnxImageDescriptor

            return OnnxImageDescriptor(descriptor, route=onnx_route), self.parsed
        if coreml_units:
            from .coreml_runtime import CoreMLImageDescriptor, checkpoint_identity

            # The PyTorch descriptor stays on the GPU as the gate's reference
            # and as the fallback for anything Core ML cannot run.
            return (
                CoreMLImageDescriptor(
                    descriptor, units=coreml_units, identity=checkpoint_identity(normalized_path)
                ),
                self.parsed,
            )
        if device.type == "privateuseone":
            return _DirectMLDescriptor(descriptor), self.parsed
        return descriptor, self.parsed

    def release(self, *, clear_metadata: bool = False):
        self.parsed = None
        self.parsed_path = None
        if clear_metadata:
            self._info_cache.clear()
