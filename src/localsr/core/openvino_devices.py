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


def _linux_driver() -> str | None:
    """Intel's OpenCL driver as named by the ICD loader, with its size and date, which
    change with every driver update."""
    for icd in sorted(Path("/etc/OpenCL/vendors").glob("*intel*.icd")):
        try:
            library = icd.read_text().strip().splitlines()[0].strip()
        except (OSError, IndexError):
            continue
        candidates = (
            [Path(library)]
            if library.startswith("/")
            else [
                Path(directory) / library
                for directory in (
                    "/usr/lib64",
                    "/usr/lib/x86_64-linux-gnu",
                    "/usr/lib",
                    "/usr/local/lib",
                )
            ]
        )
        for candidate in candidates:
            try:
                info = candidate.stat()
            except OSError:
                continue
            return f"{candidate.name}:{info.st_size}:{int(info.st_mtime)}"
    return None


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
