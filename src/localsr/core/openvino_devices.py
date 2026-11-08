"""Intel GPUs through ONNX Runtime's OpenVINO plugin (onnxruntime-ep-openvino).

The plugin carries its own OpenVINO runtime. A GPU appears only where the host has
Intel's graphics compute driver; on any other machine, or when anything here fails,
there is simply no OpenVINO device. Device ids use OpenVINO's device UUID, which stays
the same across restarts, unlike the LUID.
"""

import functools
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

EP_NAME = "OpenVINOExecutionProvider"
REGISTRATION_NAME = "localsr_openvino"


@functools.cache
def plugin_registered() -> bool:
    try:
        import onnxruntime as ort
        import onnxruntime_ep_openvino as plugin
    except ImportError:
        return False
    try:
        ort.disable_telemetry_events()
        ort.register_execution_provider_library(REGISTRATION_NAME, plugin.get_library_path())
    except Exception as error:  # noqa: BLE001 - a native load failure means no OpenVINO route
        logger.info("OpenVINO plugin could not be registered: %s", error)
        return False
    return True


def _windows_driver(luid: str) -> str | None:
    """The DXGI driver version of the adapter with OpenVINO's LUID (8 bytes, memory order)."""
    try:
        raw = bytes.fromhex(luid)
        low, high = int.from_bytes(raw[:4], "little"), int.from_bytes(raw[4:8], "little")
        from .windows_adapters import directml_adapters

        wanted = f"{high:08x}{low:08x}"
        return next(
            (a.get("driver_version") for a in directml_adapters() if a.get("luid") == wanted), None
        )
    except (ValueError, OSError, RuntimeError, AttributeError):
        return None


_LINUX_LIBRARY_DIRECTORIES = (
    "/usr/lib64",
    "/usr/lib64/intel-opencl",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib/x86_64-linux-gnu/intel-opencl",
    "/usr/lib",
    "/usr/local/lib",
)
_OPENCL_VENDORS = Path("/etc/OpenCL/vendors")


def _fingerprint(path: Path) -> str:
    resolved = path.resolve()
    info = resolved.stat()
    return f"{resolved.name}:{info.st_size}:{int(info.st_mtime)}"


def _linux_library(name: str, directories) -> Path | None:
    for directory in directories:
        candidate = Path(directory) / name
        if candidate.exists():
            return candidate
    return None


def _linux_driver() -> str | None:
    """Intel's GPU compute runtime and its graphics compiler, each by its file name, size
    and date, which change with every update. Without exactly one such runtime there is no
    driver identity, and verdicts are not stored."""
    runtimes = set()
    for icd in sorted(_OPENCL_VENDORS.glob("*.icd")):
        try:
            library = icd.read_text().strip().splitlines()[0].strip()
        except (OSError, IndexError):
            continue
        # libigdrcl is the GPU runtime; Intel's CPU OpenCL driver has its own ICD.
        if not Path(library).name.startswith("libigdrcl"):
            continue
        path = (
            Path(library)
            if library.startswith("/")
            else _linux_library(library, _LINUX_LIBRARY_DIRECTORIES)
        )
        if path is not None and path.exists():
            runtimes.add(path.resolve())
    if len(runtimes) != 1:
        return None
    runtime = runtimes.pop()
    directories = (str(runtime.parent), *_LINUX_LIBRARY_DIRECTORIES)
    parts = [_fingerprint(runtime)]
    for prefix in ("libigc.so", "libigdfcl.so"):
        found = next(
            (match for d in directories for match in sorted(Path(d).glob(f"{prefix}*"))), None
        )
        if found is None:
            return None
        parts.append(_fingerprint(found))
    return "|".join(parts)


def _driver(luid: str) -> str | None:
    if sys.platform == "win32":
        return _windows_driver(luid)
    if sys.platform == "linux":
        return _linux_driver()
    return None


def _number(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def gpu_devices() -> list[dict]:
    """The GPUs the plugin can run on, each with its ONNX Runtime device handle."""
    if not plugin_registered():
        return []
    import onnxruntime as ort

    try:
        ep_devices = ort.get_ep_devices()
    except Exception as error:  # noqa: BLE001
        logger.info("OpenVINO device enumeration failed: %s", error)
        return []
    found = {}
    for ep_device in ep_devices:
        if ep_device.ep_name != EP_NAME or "GPU" not in str(ep_device.device.type):
            continue
        metadata = dict(ep_device.ep_metadata)
        key = (metadata.get("DEVICE_UUID") or metadata.get("DEVICE_LUID") or "").lower()
        if not key or key in found:
            continue
        found[key] = {
            "key": key,
            "name": metadata.get("FULL_DEVICE_NAME") or "Intel GPU",
            "integrated": metadata.get("DEVICE_TYPE") == "integrated",
            "total_memory": _number(metadata.get("GPU_DEVICE_TOTAL_MEM_SIZE")),
            "max_allocation": _number(metadata.get("GPU_DEVICE_MAX_ALLOC_MEM_SIZE")),
            "luid": metadata.get("DEVICE_LUID", ""),
            "device_id": metadata.get("GPU_DEVICE_ID", ""),
            "architecture": metadata.get("DEVICE_ARCHITECTURE", ""),
            "plugin_version": metadata.get("version", ""),
            "driver": _driver(metadata.get("DEVICE_LUID", "")),
            "ep_device": ep_device,
        }
    return [found[key] for key in sorted(found)]


def device_for(key: str) -> dict | None:
    key = key.lower()
    return next((device for device in gpu_devices() if device["key"] == key), None)
