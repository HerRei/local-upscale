"""Use the computer's C++ runtime when it is newer than the one the Linux engine bundles.

The engine is built on an older Linux and bundles that system's libstdc++. Drivers the
engine loads from the computer, such as Intel's OpenCL driver for OpenVINO, are built
against the computer's newer libstdc++ and fail to load against the bundled one, so the
GPU simply does not appear. A newer libstdc++ runs everything built for an older one, so
the newer of the two is loaded first, before any C++ library: every later library that
needs libstdc++ then uses it. Standard library only; this runs before anything else.
"""

import ctypes
import os
import re
import sys
from pathlib import Path

HOST_LIBRARY_DIRECTORIES = (
    "/usr/lib64",
    "/usr/lib/x86_64-linux-gnu",
    "/lib64",
    "/lib/x86_64-linux-gnu",
    "/usr/lib",
)
_VERSION = {
    "libstdc++.so.6": re.compile(rb"GLIBCXX_3\.4\.(\d+)\x00"),
    "libgcc_s.so.1": re.compile(rb"GCC_(\d+)\.(\d+)(?:\.(\d+))?\x00"),
}


def _newest(path: Path, name: str) -> tuple[int, ...] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    versions = [
        tuple(int(part) for part in match.groups() if part is not None)
        for match in _VERSION[name].finditer(data)
    ]
    return max(versions) if versions else None


def _elf_kind(path: Path) -> tuple[int, int] | None:
    """(class, machine) from the ELF header: 64-bit x86-64 is (2, 62)."""
    try:
        with path.open("rb") as stream:
            header = stream.read(20)
    except OSError:
        return None
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None
    return header[4], int.from_bytes(header[18:20], "little")


def _host_copy(name: str, directories, kind: tuple[int, int] | None) -> Path | None:
    """The first host copy built for the same class and machine as the bundled one."""
    if kind is None:
        return None
    for directory in directories:
        candidate = Path(directory) / name
        if candidate.is_file() and _elf_kind(candidate) == kind:
            return candidate
    return None


def _already_loaded(name: str) -> bool:
    try:
        return name in Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def prefer_newer_host_runtime(
    bundle: Path | None = None, directories=HOST_LIBRARY_DIRECTORIES, load=None
) -> list[str]:
    """Load the host's libgcc_s and libstdc++ where they are newer than the bundled ones.
    Returns the paths it loaded."""
    if bundle is None:
        if not (sys.platform == "linux" and getattr(sys, "frozen", False)):
            return []
        bundle = Path(getattr(sys, "_MEIPASS", ""))
    load = load or (lambda path: ctypes.CDLL(path, mode=os.RTLD_NOW | os.RTLD_GLOBAL))
    candidates = {}
    for name in ("libgcc_s.so.1", "libstdc++.so.6"):
        bundled = bundle / name
        if not bundled.is_file() or _already_loaded(name):
            continue
        host = _host_copy(name, directories, _elf_kind(bundled))
        if host is None:
            continue
        host_version, bundled_version = _newest(host, name), _newest(bundled, name)
        if host_version is not None and bundled_version is not None:
            candidates[name] = (host, host_version, bundled_version)
    cxx = candidates.get("libstdc++.so.6")
    newer_cxx = cxx is not None and cxx[1] > cxx[2]
    loaded = []
    # libstdc++ needs libgcc_s, so that one goes first. A newer host libstdc++ comes with
    # the host's libgcc_s even when both libgcc_s name the same newest version.
    for name in ("libgcc_s.so.1", "libstdc++.so.6"):
        if name not in candidates:
            continue
        host, host_version, bundled_version = candidates[name]
        if not (
            host_version > bundled_version
            or (name == "libgcc_s.so.1" and newer_cxx and host_version >= bundled_version)
        ):
            continue
        try:
            load(str(host))
        except OSError:
            continue
        loaded.append(str(host))
    return loaded
