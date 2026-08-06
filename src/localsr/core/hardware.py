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


def get_memory_snapshot(device_id: str) -> dict:
    """Return live memory values for the active device without loading a model."""
    total_ram, available_ram = _system_memory()
    device_total = total_ram
    device_free = available_ram

    if device_id == "mps" and torch.backends.mps.is_available():
        device_total, device_free = _mps_memory(total_ram, available_ram)
    elif device_id.startswith("cuda") and torch.cuda.is_available():
        _, _, raw_index = device_id.partition(":")
        index = int(raw_index or 0)
        free, total = torch.cuda.mem_get_info(index)
        device_total, device_free = int(total), int(free)

    return {
        "device_total_memory": int(device_total),
        "device_free_memory": int(device_free),
        "system_ram_total": int(total_ram),
        "system_ram_available": int(available_ram),
    }


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
            }
        )

    if torch.cuda.is_available():
        for index in range(torch.cuda.device_count()):
            with torch.cuda.device(index):
                free, total = torch.cuda.mem_get_info(index)
                properties = torch.cuda.get_device_properties(index)
            major, minor = properties.major, properties.minor
            devices.append(
                {
                    "id": f"cuda:{index}",
                    "type": "cuda",
                    "name": properties.name,
                    "total_memory": int(total),
                    "free_memory": int(free),
                    "supports_fp16": (major, minor) >= (5, 3),
                    "recommended_tile_sizes": _recommended_tiles(int(free)),
                }
            )

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
        }
    )

    return {
        "system_ram_total": total_ram,
        "system_ram_available": available_ram,
        "devices": devices,
    }
