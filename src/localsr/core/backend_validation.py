"""Validate runtime identity without claiming unperformed GPU hardware tests."""

import sys
from pathlib import Path


def probe(backend: str) -> dict[str, object]:
    import torch

    result: dict[str, object] = {
        "backend": backend,
        "hardware_tested": False,
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
        if (
            torch.version.cuda is not None
            or torch.version.hip is not None
            or "+xpu" in torch.__version__
            or result["xpu_runtime_version"] is not None
        ):
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
        if getattr(sys, "frozen", False):
            from localsr.core.xpu_runtime import verify_bundled_runtime

            result.update(verify_bundled_runtime(Path(sys._MEIPASS), str(torch.__version__)))
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
    sample = torch.ones((1, 1, 5, 5), device="cpu")
    kernel = torch.ones((1, 1, 3, 3), device="cpu")
    if float(torch.nn.functional.conv2d(sample, kernel).sum()) != 81.0:
        raise RuntimeError("Packaged Torch CPU inference failed")
    result["cpu_inference_verified"] = True
    result["runtime_verified"] = True
    return result
