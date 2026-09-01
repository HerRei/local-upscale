import ctypes
import os
import re
import subprocess
import sys
from pathlib import Path

import torch


def _system_memory() -> tuple[int, int]:
    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.total_physical, status.available_physical

    page_size = os.sysconf("SC_PAGE_SIZE")
    total = page_size * os.sysconf("SC_PHYS_PAGES")

    if sys.platform == "darwin":
        try:
            output = subprocess.check_output(["vm_stat"], text=True, timeout=2)
            page_match = re.search(r"page size of (\d+) bytes", output)
            vm_page_size = int(page_match.group(1)) if page_match else page_size
            values = {
                key.lower(): int(value)
                for key, value in re.findall(r"Pages ([\w ]+):\s+(\d+)\.", output)
            }
            reclaimable_pages = sum(
                values.get(key, 0) for key in ("free", "inactive", "speculative", "purgeable")
            )
            return total, reclaimable_pages * vm_page_size
        except (OSError, subprocess.SubprocessError, ValueError):
            return total, int(total * 0.25)

    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        values = {}
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            if value:
                values[key] = int(value.strip().split()[0]) * 1024
        return values.get("MemTotal", total), values.get("MemAvailable", int(total * 0.25))

    try:
        available = page_size * os.sysconf("SC_AVPHYS_PAGES")
    except (OSError, ValueError):
        available = int(total * 0.25)
    return total, available


def _recommended_tiles(free_memory: int) -> list[int]:
    gib = free_memory / (1024**3)
    if gib < 2:
        return [64]
    if gib < 4:
        return [64, 128]
    if gib < 8:
        return [64, 128, 192]
    if gib < 12:
        return [64, 128, 192, 256]
    return [64, 128, 192, 256, 320, 384, 512]


def _mps_memory(total_ram: int, available_ram: int) -> tuple[int, int]:
    recommended_max_memory = getattr(torch.mps, "recommended_max_memory", None)
    driver_allocated_memory = getattr(torch.mps, "driver_allocated_memory", None)
    recommended = (
        int(recommended_max_memory())
        if recommended_max_memory is not None
        else int(total_ram * 0.75)
    )
    allocated = int(driver_allocated_memory()) if driver_allocated_memory is not None else 0
    free = max(0, min(recommended - allocated, int(available_ram * 0.85)))
    return recommended, free


def _pressure_level(pressure_percent: float) -> str:
    if pressure_percent >= 90:
        return "high"
    if pressure_percent >= 75:
        return "moderate"
    return "low"


def _parse_macos_pressure(output: str) -> float | None:
    match = re.search(r"System-wide memory free percentage:\s*(\d+(?:\.\d+)?)%", output)
    if not match:
        return None
    return max(0.0, min(100.0, 100.0 - float(match.group(1))))


def _parse_macos_swap(output: str) -> tuple[int, int]:
    match = re.search(
        r"total\s*=\s*([\d.]+)([KMG])\s+used\s*=\s*([\d.]+)([KMG])",
        output,
        re.IGNORECASE,
    )
    if not match:
        return 0, 0
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
    total = int(float(match.group(1)) * multipliers[match.group(2).upper()])
    used = int(float(match.group(3)) * multipliers[match.group(4).upper()])
    return total, used


def _system_pressure_snapshot(total_ram: int, available_ram: int) -> dict:
    pressure_percent = (
        max(0.0, min(100.0, (1.0 - available_ram / total_ram) * 100.0)) if total_ram > 0 else 0.0
    )
    compressed_memory = 0
    swap_total = 0
    swap_used = 0

    if sys.platform == "darwin":
        try:
            pressure_output = subprocess.check_output(
                ["memory_pressure", "-Q"], text=True, timeout=2
            )
            measured = _parse_macos_pressure(pressure_output)
            if measured is not None:
                pressure_percent = measured
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        try:
            vm_output = subprocess.check_output(["vm_stat"], text=True, timeout=2)
            page_match = re.search(r"page size of (\d+) bytes", vm_output)
            page_size = int(page_match.group(1)) if page_match else 4096
            compressed_match = re.search(r"Pages occupied by compressor:\s+(\d+)\.", vm_output)
            if compressed_match:
                compressed_memory = int(compressed_match.group(1)) * page_size
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        try:
            swap_output = subprocess.check_output(["sysctl", "vm.swapusage"], text=True, timeout=2)
            swap_total, swap_used = _parse_macos_swap(swap_output)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    elif sys.platform.startswith("linux"):
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            try:
                values = {}
                for line in meminfo.read_text(encoding="utf-8").splitlines():
                    key, _, value = line.partition(":")
                    if value:
                        values[key] = int(value.strip().split()[0]) * 1024
                compressed_memory = values.get("Zswap", 0)
                swap_total = values.get("SwapTotal", 0)
                swap_used = max(0, swap_total - values.get("SwapFree", 0))
            except (OSError, ValueError):
                pass

    return {
        "system_memory_pressure_percent": round(pressure_percent, 1),
        "system_memory_pressure_level": _pressure_level(pressure_percent),
        "system_compressed_memory": int(compressed_memory),
        "system_swap_total": int(swap_total),
        "system_swap_used": int(swap_used),
    }


def get_memory_snapshot(device_id: str) -> dict:
    """Return live memory values for the active device without loading a model."""
    total_ram, available_ram = _system_memory()
    device_total = total_ram
    device_free = available_ram
    device_allocated = 0
    mps_tensor_allocated = 0
    mps_driver_allocated = 0
    mps_recommended_max = 0

    if device_id == "mps" and torch.backends.mps.is_available():
        device_total, device_free = _mps_memory(total_ram, available_ram)
        current_allocated = getattr(torch.mps, "current_allocated_memory", None)
        driver_allocated = getattr(torch.mps, "driver_allocated_memory", None)
        recommended_max = getattr(torch.mps, "recommended_max_memory", None)
        mps_tensor_allocated = int(current_allocated()) if callable(current_allocated) else 0
        mps_driver_allocated = int(driver_allocated()) if callable(driver_allocated) else 0
        mps_recommended_max = int(recommended_max()) if callable(recommended_max) else device_total
        device_allocated = mps_driver_allocated
    elif device_id.startswith("cuda") and torch.cuda.is_available():
        _, _, raw_index = device_id.partition(":")
        index = int(raw_index or 0)
        free, total = torch.cuda.mem_get_info(index)
        device_total, device_free = int(total), int(free)
        device_allocated = max(0, device_total - device_free)
    elif device_id.startswith("xpu") and hasattr(torch, "xpu") and torch.xpu.is_available():
        _, _, raw_index = device_id.partition(":")
        index = int(raw_index or 0)
        free, total = torch.xpu.mem_get_info(index)
        device_total, device_free = int(total), int(free)
        device_allocated = max(0, device_total - device_free)

    snapshot = {
        "device_total_memory": int(device_total),
        "device_free_memory": int(device_free),
        "device_allocated_memory": int(device_allocated),
        "system_ram_total": int(total_ram),
        "system_ram_available": int(available_ram),
        "mps_tensor_allocated_memory": int(mps_tensor_allocated),
        "mps_driver_allocated_memory": int(mps_driver_allocated),
        "mps_recommended_max_memory": int(mps_recommended_max),
    }
    snapshot.update(_system_pressure_snapshot(total_ram, available_ram))
    return snapshot


def get_capability_report() -> dict:
    total_ram, available_ram = _system_memory()
    devices = []

    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        recommended, free = _mps_memory(total_ram, available_ram)
        devices.append(
            {
                "id": "mps",
                "type": "mps",
                "name": "Apple GPU (Metal)",
                "total_memory": recommended,
                "free_memory": free,
                "supports_fp16": True,
                "recommended_tile_sizes": _recommended_tiles(free),
                "is_integrated": True,
            }
        )

    if torch.cuda.is_available():
        is_rocm = bool(getattr(torch.version, "hip", None))
        backend_type = "rocm" if is_rocm else "cuda"
        backend_name = "ROCm" if is_rocm else "CUDA"
        for index in range(torch.cuda.device_count()):
            with torch.cuda.device(index):
                free, total = torch.cuda.mem_get_info(index)
                properties = torch.cuda.get_device_properties(index)
            major, minor = properties.major, properties.minor
            devices.append(
                {
                    "id": f"cuda:{index}",
                    "type": backend_type,
                    "name": f"{properties.name} ({backend_name})",
                    "total_memory": int(total),
                    "free_memory": int(free),
                    "supports_fp16": (major, minor) >= (5, 3),
                    "recommended_tile_sizes": _recommended_tiles(int(free)),
                    "is_integrated": False,
                }
            )

    if hasattr(torch, "xpu") and torch.xpu.is_available():
        for index in range(torch.xpu.device_count()):
            free, total = torch.xpu.mem_get_info(index)
            properties = torch.xpu.get_device_properties(index)
            integrated = bool(getattr(properties, "is_integrated_gpu", False))
            kind = "integrated" if integrated else "discrete"
            devices.append(
                {
                    "id": f"xpu:{index}",
                    "type": "xpu",
                    "name": f"{properties.name} (Intel XPU, {kind})",
                    "total_memory": int(total),
                    "free_memory": int(free),
                    "supports_fp16": bool(getattr(properties, "has_fp16", False)),
                    "recommended_tile_sizes": _recommended_tiles(int(free)),
                    "is_integrated": integrated,
                }
            )

    if sys.platform == "win32":
        _detect_directml(devices, total_ram, available_ram)
        # QNN provider discovery is intentionally not exposed yet. The current
        # inference engine consumes PyTorch/Spandrel checkpoints and cannot
        # execute them through ONNX Runtime QNN without a separate conversion
        # and validation pipeline. Advertising those devices would create a
        # selectable backend whose every job is guaranteed to fail.

    cpu_budget = max(512 * 1024 * 1024, int(available_ram * 0.65))
    devices.append(
        {
            "id": "cpu",
            "type": "cpu",
            "name": "CPU",
            "total_memory": total_ram,
            "free_memory": available_ram,
            "supports_fp16": False,
            "recommended_tile_sizes": _recommended_tiles(cpu_budget),
            "is_integrated": False,
        }
    )

    report = {
        "system_ram_total": total_ram,
        "system_ram_available": available_ram,
        "devices": devices,
    }
    report.update(_system_pressure_snapshot(total_ram, available_ram))
    return report


def _detect_directml(devices: list[dict], total_ram: int, available_ram: int) -> None:
    # Packaging smoke tests can run in a Windows VM with no passed-through D3D12
    # adapter. Some torch-directml builds block indefinitely while probing that
    # configuration, so allow the smoke harness to exercise the worker protocol
    # without pretending that CI has DirectML hardware. Normal application runs
    # never set this variable and retain full device discovery.
    if os.environ.get("LOCALSR_SKIP_DIRECTML_PROBE") == "1":
        return
    try:
        import torch_directml

        if torch_directml.is_available():
            for index in range(torch_directml.device_count()):
                devices.append(
                    {
                        "id": f"directml:{index}",
                        "type": "directml",
                        "name": f"DirectML Device {index}",
                        "total_memory": total_ram,
                        "free_memory": available_ram,
                        "supports_fp16": True,
                        "recommended_tile_sizes": _recommended_tiles(int(available_ram * 0.5)),
                        "is_integrated": True,
                    }
                )
    except ImportError:
        pass


def _detect_qnn(devices: list[dict], total_ram: int, available_ram: int) -> None:
    try:
        import onnxruntime as ort

        available_providers = ort.get_available_providers()
        if "QNNExecutionProvider" in available_providers:
            devices.append(
                {
                    "id": "qnn-npu",
                    "type": "qnn",
                    "name": "Snapdragon NPU (Hexagon QNN)",
                    "total_memory": total_ram,
                    "free_memory": available_ram,
                    "supports_fp16": True,
                    "recommended_tile_sizes": [64, 128],
                    "is_integrated": True,
                }
            )
        if "QNNExecutionProvider" in available_providers:
            devices.append(
                {
                    "id": "qnn-gpu",
                    "type": "qnn-gpu",
                    "name": "Snapdragon Adreno GPU (QNN)",
                    "total_memory": total_ram,
                    "free_memory": available_ram,
                    "supports_fp16": True,
                    "recommended_tile_sizes": _recommended_tiles(int(available_ram * 0.5)),
                    "is_integrated": True,
                }
            )
    except ImportError:
        pass
