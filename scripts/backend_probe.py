#!/usr/bin/env python3
"""Record and validate that a packaging environment contains its named backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def probe(backend: str) -> dict[str, object]:
    import torch

    result: dict[str, object] = {
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "hip_version": torch.version.hip,
        "mps_is_built": bool(getattr(torch.backends.mps, "is_built", lambda: False)()),
        "mps_is_available_on_builder": bool(
            getattr(torch.backends.mps, "is_available", lambda: False)()
        ),
        "xpu_module_present": hasattr(torch, "xpu"),
        "xpu_runtime_version": getattr(torch.version, "xpu", None),
    }
    normalized = backend.lower()
    if normalized == "cpu":
        if torch.version.cuda is not None or torch.version.hip is not None:
            raise RuntimeError(f"CPU artifact contains a GPU torch build: {result}")
    elif normalized == "cuda":
        if torch.version.cuda is None:
            raise RuntimeError(f"CUDA artifact lacks a CUDA torch runtime: {result}")
    elif normalized == "amd-rocm":
        if torch.version.hip is None:
            raise RuntimeError(f"ROCm artifact lacks a HIP torch runtime: {result}")
    elif normalized == "intel-xpu":
        xpu_build = "+xpu" in torch.__version__ or result["xpu_runtime_version"] is not None
        result["xpu_build"] = xpu_build
        if not xpu_build:
            raise RuntimeError(f"Intel artifact lacks an XPU torch runtime: {result}")
    elif normalized == "directml":
        import torch_directml

        result["directml_module"] = str(Path(torch_directml.__file__).resolve())
        result["directml_device_factory_present"] = hasattr(torch_directml, "device")
        if not result["directml_device_factory_present"]:
            raise RuntimeError(f"DirectML module lacks its device factory: {result}")
    elif normalized == "mps":
        result["mps_backend_module_present"] = hasattr(torch.backends, "mps")
        if not result["mps_backend_module_present"] or not result["mps_is_built"]:
            raise RuntimeError(f"Torch lacks a built MPS backend: {result}")
    else:
        raise ValueError(f"Unknown backend: {backend}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.backend)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
