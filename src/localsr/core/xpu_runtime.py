"""Check the native Intel runtime shipped beside a frozen worker."""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST_NAME = "xpu-runtime.json"
REQUIRED_LIBRARIES = (
    "libsycl.so",
    "libur_loader.so",
    "libur_adapter_level_zero.so",
    "libur_adapter_level_zero_v2.so",
    "libur_adapter_opencl.so",
)
REQUIRED_DISTRIBUTIONS = frozenset({"intel-cmplr-lib-ur", "intel-sycl-rt"})


def require_runtime_libraries(names: list[str]) -> None:
    missing = [
        base
        for base in REQUIRED_LIBRARIES
        if not any(n == base or n.startswith(base + ".") for n in names)
    ]
    if missing:
        raise RuntimeError(f"XPU runtime is missing native libraries: {', '.join(missing)}")


def verify_bundled_runtime(root: Path, torch_version: str) -> dict[str, object]:
    """Verify actual bundled files without initializing a GPU or loading a driver."""
    try:
        manifest = json.loads((root / "localsr" / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Bundled XPU runtime manifest is missing or invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("torch_version") != torch_version
    ):
        raise RuntimeError("Bundled XPU runtime manifest has a different runtime identity")
    distributions = manifest.get("distributions")
    if not isinstance(distributions, dict) or not REQUIRED_DISTRIBUTIONS.issubset(distributions):
        raise RuntimeError("Bundled XPU runtime lacks Intel distribution evidence")
    if not all(isinstance(version, str) and version for version in distributions.values()):
        raise RuntimeError("Bundled XPU runtime has invalid distribution versions")
    libraries, data_files = manifest.get("libraries"), manifest.get("data_files")
    if not isinstance(libraries, list) or not isinstance(data_files, list):
        raise RuntimeError("Bundled XPU runtime has no native file inventory")
    names = libraries + data_files
    if not all(
        isinstance(name, str)
        and name not in {"", ".", ".."}
        and "/" not in name
        and "\\" not in name
        for name in names
    ) or len(set(names)) != len(names):
        raise RuntimeError("Bundled XPU runtime contains unsafe or duplicate file paths")
    require_runtime_libraries(libraries)
    root = root.resolve()
    for name in names:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Bundled XPU runtime file is missing: {name}")
        if name in libraries:
            with path.open("rb") as stream:
                header = stream.read(20)
            if header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != 62:
                raise RuntimeError(f"Bundled XPU runtime library is not x86-64 ELF: {name}")
    return {
        "xpu_runtime_files_verified": True,
        "xpu_runtime_library_count": len(libraries),
        "xpu_runtime_data_file_count": len(data_files),
        "xpu_runtime_distributions": distributions,
    }
