"""Enumerate hardware adapters in the DXGI order used by DirectML.

Besides the description, each adapter reports, when Windows can tell: whether it shares
memory with the CPU (D3D12 UMA), the memory budget Windows grants this process right now
(IDXGIAdapter3::QueryVideoMemoryInfo) and its user-mode driver version. Each of these
queries is optional: a failure leaves the field None and never hides the adapter.
"""

import ctypes
import logging
import sys
import uuid

logger = logging.getLogger(__name__)

_ADAPTER3_IID = "645967a4-1392-4310-a798-8053ce3e93fd"
_DEVICE_IID = "189819f1-1db6-4b57-be54-1821339b85f7"  # ID3D12Device
_DXGI_DEVICE_IID = "54ec77fa-1377-44e6-8c32-88fd5f44c84c"  # IDXGIDevice, for the driver version
_FEATURE_LEVEL_11_0 = 0xB000
_D3D12_FEATURE_ARCHITECTURE = 1


class _Guid(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__()
        self.bytes[:] = uuid.UUID(value).bytes_le


class _Luid(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_int32)]


class _AdapterDescription(ctypes.Structure):
    _fields_ = [
        ("description", ctypes.c_wchar * 128),
        ("vendor_id", ctypes.c_uint32),
        ("device_id", ctypes.c_uint32),
        ("subsystem_id", ctypes.c_uint32),
        ("revision", ctypes.c_uint32),
        ("dedicated_video_memory", ctypes.c_size_t),
        ("dedicated_system_memory", ctypes.c_size_t),
        ("shared_system_memory", ctypes.c_size_t),
        ("luid", _Luid),
        ("flags", ctypes.c_uint32),
    ]


class _Architecture(ctypes.Structure):
    _fields_ = [
        ("node_index", ctypes.c_uint32),
        ("tile_based_renderer", ctypes.c_int32),
        ("uma", ctypes.c_int32),
        ("cache_coherent_uma", ctypes.c_int32),
    ]


class _VideoMemoryInfo(ctypes.Structure):
    _fields_ = [
        ("budget", ctypes.c_uint64),
        ("current_usage", ctypes.c_uint64),
        ("available_for_reservation", ctypes.c_uint64),
        ("current_reservation", ctypes.c_uint64),
    ]


def _method(pointer, index, result, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])


def _release(pointer):
    if pointer:
        _method(pointer, 2, ctypes.c_uint32)(pointer)


def _driver_version(adapter) -> str | None:
    # IDXGIAdapter::CheckInterfaceSupport (slot 9) reports the user-mode driver version.
    version = ctypes.c_int64()
    check = _method(
        adapter, 9, ctypes.c_int32, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_int64)
    )
    if check(adapter, _Guid(_DXGI_DEVICE_IID), ctypes.byref(version)) < 0:
        return None
    value = version.value & 0xFFFFFFFFFFFFFFFF
    return ".".join(str((value >> shift) & 0xFFFF) for shift in (48, 32, 16, 0))


def _uma(create_device, adapter) -> bool | None:
    device = ctypes.c_void_p()
    if create_device(adapter, _FEATURE_LEVEL_11_0, _Guid(_DEVICE_IID), ctypes.byref(device)) < 0:
        return None
    try:
        architecture = _Architecture()
        # ID3D12Device::CheckFeatureSupport is slot 13.
        check = _method(device, 13, ctypes.c_int32, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        if (
            check(
                device,
                _D3D12_FEATURE_ARCHITECTURE,
                ctypes.byref(architecture),
                ctypes.sizeof(architecture),
            )
            < 0
        ):
            return None
        return bool(architecture.uma)
    finally:
        _release(device)


def _memory_budget(adapter) -> tuple[int, int, int, int] | None:
    """Budget and current usage of the local and non-local segments for this process.

    Intel reports an iGPU's shared memory as local; AMD reports only the BIOS carve-out
    as local and the shared system memory as non-local, so callers need both.
    """
    adapter3 = ctypes.c_void_p()
    query = _method(
        adapter, 0, ctypes.c_int32, ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)
    )
    if query(adapter, _Guid(_ADAPTER3_IID), ctypes.byref(adapter3)) < 0:
        return None
    try:
        # IDXGIAdapter3::QueryVideoMemoryInfo is slot 14; segment group 0 is local,
        # 1 is non-local.
        read = _method(
            adapter3,
            14,
            ctypes.c_int32,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.POINTER(_VideoMemoryInfo),
        )
        values = []
        for group in (0, 1):
            info = _VideoMemoryInfo()
            if read(adapter3, 0, group, ctypes.byref(info)) < 0:
                return None
            values += [int(info.budget), int(info.current_usage)]
        return tuple(values)

    finally:
        _release(adapter3)


def _optional(name: str, query, *arguments):
    try:
        return query(*arguments)
    except (OSError, ValueError, ctypes.ArgumentError) as error:
        logger.info("Graphics adapter query %s failed: %s", name, error)
        return None


def directml_adapters(*, include_software: bool = False) -> list[dict]:
    """DirectX 12 adapters in DXGI order. ``include_software`` lets tests use WARP."""
    if sys.platform != "win32":
        return []
    # Load system DLLs with an explicit system-directory search flag.
    dxgi = ctypes.WinDLL("dxgi.dll", winmode=0x800)
    d3d12 = ctypes.WinDLL("d3d12.dll", winmode=0x800)
    create = dxgi.CreateDXGIFactory1
    create.argtypes = [ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_int32
    factory = ctypes.c_void_p()
    if create(_Guid("770aae78-f26f-4dba-a829-253c83d1b387"), ctypes.byref(factory)) < 0:
        raise RuntimeError("Windows could not enumerate graphics adapters")
    create_device = d3d12.D3D12CreateDevice
    create_device.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(_Guid),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    create_device.restype = ctypes.c_int32
    device_iid = _Guid(_DEVICE_IID)
    adapters = []
    try:
        enumerate_adapter = _method(
            factory, 12, ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)
        )
        for index in range(64):
            adapter = ctypes.c_void_p()
            result = enumerate_adapter(factory, index, ctypes.byref(adapter))
            if result & 0xFFFFFFFF == 0x887A0002:  # DXGI_ERROR_NOT_FOUND
                break
            if result < 0:
                raise RuntimeError(f"Windows adapter enumeration failed: {result & 0xFFFFFFFF:x}")
            try:
                description = _AdapterDescription()
                get_description = _method(
                    adapter, 10, ctypes.c_int32, ctypes.POINTER(_AdapterDescription)
                )
                if get_description(adapter, ctypes.byref(description)) < 0:
                    raise RuntimeError(f"Could not read graphics adapter {index}")
                if description.flags & 2 and not include_software:
                    continue  # Software rendering is not GPU acceptance.
                if create_device(adapter, _FEATURE_LEVEL_11_0, device_iid, None) < 0:
                    continue
                budget = _optional("memory budget", _memory_budget, adapter)
                # NVIDIA makes no x86 integrated GPUs, and creating a device would wake a
                # sleeping laptop dGPU just to ask.
                if description.vendor_id == 0x10DE:
                    uma = False
                else:
                    uma = _optional("architecture", _uma, create_device, adapter)
                adapters.append(
                    {
                        "index": index,
                        "name": description.description,
                        "dedicated_memory": int(description.dedicated_video_memory),
                        "shared_memory": int(description.shared_system_memory),
                        "vendor_id": int(description.vendor_id),
                        "device_id": int(description.device_id),
                        "subsystem_id": int(description.subsystem_id),
                        "revision": int(description.revision),
                        "luid": f"{description.luid.high & 0xFFFFFFFF:08x}{description.luid.low:08x}",
                        "software": bool(description.flags & 2),
                        "uma": uma,
                        "budget": None if budget is None else budget[0],
                        "usage": None if budget is None else budget[1],
                        "shared_budget": None if budget is None else budget[2],
                        "shared_usage": None if budget is None else budget[3],
                        "driver_version": _optional("driver version", _driver_version, adapter),
                    }
                )
            finally:
                _release(adapter)
    finally:
        _release(factory)
    return adapters
