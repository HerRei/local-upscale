import os
from dataclasses import dataclass
from math import lcm

import spandrel
import torch

from localsr.core.model_catalog import CATALOG_BY_FILENAME, file_matches_checksum

UNVERIFIED_CHECKPOINT_ENV = "LOCALSR_ALLOW_UNVERIFIED_CHECKPOINTS"
RISKY_CHECKPOINT_SUFFIXES = {".ckpt", ".pt", ".pth"}


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
    parameter_count: int = 0
    model_file_size: int = 0


class ModelAdapter:
    def __init__(self, *, allow_unverified_checkpoints: bool | None = None):
        self.loader = spandrel.ModelLoader()
        self.parsed = None
        self.parsed_path = None
        self.allow_unverified_checkpoints = (
            unverified_checkpoints_enabled()
            if allow_unverified_checkpoints is None
            else allow_unverified_checkpoints
        )

    def _checkpoint_trust(self, path: str) -> str:
        candidate = os.path.abspath(path)
        suffix = os.path.splitext(candidate)[1].lower()
        catalog_model = CATALOG_BY_FILENAME.get(os.path.basename(candidate))
        if catalog_model is not None:
            if not file_matches_checksum(candidate, catalog_model.size_bytes, catalog_model.sha256):
                raise CheckpointSecurityError(
                    f"{catalog_model.name} failed its catalog SHA-256 check. "
                    "LocalSR refused to deserialize it; remove the file and download it again."
                )
            return "catalog-verified"
        if suffix in RISKY_CHECKPOINT_SUFFIXES:
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
        if self.parsed is None or self.parsed_path != normalized_path:
            checkpoint_trust = self._checkpoint_trust(normalized_path)
            self.parsed = self.loader.load_from_file(normalized_path)
            self.parsed_path = normalized_path
        else:
            checkpoint_trust = "cached"

        # Determine attributes using official spandrel 0.4 API
        arch = self.parsed.architecture.name
        scale = self.parsed.scale

        # Channels
        in_c = getattr(self.parsed, "input_channels", getattr(self.parsed, "in_channels", 3))
        out_c = getattr(self.parsed, "output_channels", getattr(self.parsed, "out_channels", 3))

        # Spandrel 0.4 does not expose a tiling capability flag. LocalSR tiles
        # ImageModelDescriptor inputs itself and applies the descriptor's size
        # requirements to every tile.
        tiling_supported = True

        # Half support
        half_supported = getattr(self.parsed, "supports_half", False)

        # Size requirements
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

        parameter_count = sum(parameter.numel() for parameter in self.parsed.model.parameters())

        return NormalizedModelInfo(
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
            parameter_count=parameter_count,
            model_file_size=os.path.getsize(normalized_path),
        )

    def load(self, path: str, device: torch.device, precision: torch.dtype):
        normalized_path = os.path.abspath(path)
        if self.parsed is None or self.parsed_path != normalized_path:
            self._checkpoint_trust(normalized_path)
            self.parsed = self.loader.load_from_file(normalized_path)
            self.parsed_path = normalized_path
        model = self.parsed.model.to(device).to(precision)
        model.eval()
        return model, self.parsed

    def release(self):
        self.parsed = None
        self.parsed_path = None
