"""Enumerate hardware adapters in the DXGI order used by DirectML."""

import ctypes
import sys
import uuid


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


def _method(pointer, index, result, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])


def _release(pointer):
    if pointer:
        _method(pointer, 2, ctypes.c_uint32)(pointer)


def directml_adapters() -> list[dict]:
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
    supports_d3d12 = d3d12.D3D12CreateDevice
    supports_d3d12.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(_Guid),
        ctypes.c_void_p,
    ]
    supports_d3d12.restype = ctypes.c_int32
    device_iid = _Guid("189819f1-1db6-4b57-be54-1821339b85f7")
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
                if description.flags & 2:  # Software rendering is not GPU acceptance.
                    continue
                if supports_d3d12(adapter, 0xB000, device_iid, None) < 0:
                    continue
                adapters.append(
                    {
                        "index": index,
                        "name": description.description,
                        "dedicated_memory": int(description.dedicated_video_memory),
                        "shared_memory": int(description.shared_system_memory),
                        "vendor_id": int(description.vendor_id),
                    }
                )
            finally:
                _release(adapter)
    finally:
        _release(factory)
    return adapters
