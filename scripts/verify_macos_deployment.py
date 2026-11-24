"""Fail when a macOS bundle holds code or Metal libraries newer than its minimum macOS.

0.6.0 shipped `mlx.metallib` from the macOS 26 wheel of mlx-metal, because the release Mac
ran macOS 26 and the installer picked the newest wheel for it. macOS 14 and 15 cannot load
that library and MLX stops the engine with a trap at start
(https://github.com/HerRei/local-upscale/issues/183). Every native file carries the oldest
macOS it was built for, so the bundle can be checked against its own minimum:

- Mach-O files: LC_BUILD_VERSION (minos) or LC_VERSION_MIN_MACOSX, every slice of a fat file.
- Metal libraries: bytes 12-15 of the MTLB header hold the target macOS (major, minor as
  little-endian u16), e.g. 14.0 in the macosx_14_0 wheel and 26.2 in the macosx_26_0 one.

Usage: verify_macos_deployment.py --tree LocalSR.app [--minimum 14.0]
Without --minimum the app's LSMinimumSystemVersion is used.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import struct
import sys
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
MH_MAGIC = 0xFEEDFACE
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
METALLIB_MAGIC = b"MTLB"


def _version(packed: int) -> tuple[int, int]:
    # Mach-O packs X.Y.Z as xxxx.yy.zz in one u32.
    return packed >> 16, (packed >> 8) & 0xFF


def _slice_minimum(data: bytes, offset: int) -> tuple[int, int] | None:
    (magic,) = struct.unpack_from("<I", data, offset)
    if magic not in (MH_MAGIC_64, MH_MAGIC):
        return None
    header_size = 32 if magic == MH_MAGIC_64 else 28
    ncmds, _sizeofcmds = struct.unpack_from("<II", data, offset + 16)
    position = offset + header_size
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", data, position)
        if command == LC_BUILD_VERSION:
            platform, minos = struct.unpack_from("<II", data, position + 8)
            if platform == PLATFORM_MACOS:
                return _version(minos)
        elif command == LC_VERSION_MIN_MACOSX:
            (minos,) = struct.unpack_from("<I", data, position + 8)
            return _version(minos)
        if size <= 0:
            break
        position += size
    return None


def macho_minimum(data: bytes) -> tuple[int, int] | None:
    """The highest minimum macOS of all slices, or None when this is not a Mach-O file."""
    if len(data) < 32:
        return None
    (big_magic,) = struct.unpack_from(">I", data, 0)
    if big_magic in (FAT_MAGIC, FAT_MAGIC_64):
        (count,) = struct.unpack_from(">I", data, 4)
        if count > 64:  # a Java class file shares the magic
            return None
        minimums = []
        for index in range(count):
            if big_magic == FAT_MAGIC:
                _cpu, _sub, slice_offset, _size, _align = struct.unpack_from(
                    ">IIIII", data, 8 + index * 20
                )
            else:
                _cpu, _sub, slice_offset, _size, _align, _reserved = struct.unpack_from(
                    ">IIQQII", data, 8 + index * 32
                )
            if (minimum := _slice_minimum(data, slice_offset)) is not None:
                minimums.append(minimum)
        return max(minimums) if minimums else None
    return _slice_minimum(data, 0)


def metallib_target(data: bytes) -> tuple[int, int] | None:
    if len(data) < 16 or data[:4] != METALLIB_MAGIC:
        return None
    major, minor = struct.unpack_from("<HH", data, 12)
    return major, minor


def _parse_minimum(text: str) -> tuple[int, int]:
    parts = [int(part) for part in text.split(".")[:2]]
    return parts[0], parts[1] if len(parts) > 1 else 0


def check_tree(tree: Path, minimum: tuple[int, int]) -> tuple[int, list[dict]]:
    checked = 0
    problems = []
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            head = handle.read(4)
            if len(head) < 4:
                continue
            if head == METALLIB_MAGIC:
                handle.seek(0)
                found, kind = metallib_target(handle.read(16)), "metallib"
            elif struct.unpack("<I", head)[0] in (MH_MAGIC_64, MH_MAGIC) or struct.unpack(
                ">I", head
            )[0] in (FAT_MAGIC, FAT_MAGIC_64):
                handle.seek(0)
                found, kind = macho_minimum(handle.read()), "mach-o"
            else:
                continue
        if found is None:
            continue
        checked += 1
        if found > minimum:
            problems.append(
                {
                    "path": str(path.relative_to(tree)),
                    "kind": kind,
                    "requires": f"{found[0]}.{found[1]}",
                }
            )
    return checked, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--minimum", help="defaults to the app's LSMinimumSystemVersion")
    args = parser.parse_args(argv)
    minimum_text = args.minimum
    if minimum_text is None:
        info = args.tree / "Contents" / "Info.plist"
        minimum_text = plistlib.loads(info.read_bytes())["LSMinimumSystemVersion"]
    minimum = _parse_minimum(minimum_text)
    checked, problems = check_tree(args.tree, minimum)
    print(json.dumps({"minimum": minimum_text, "checked": checked, "too_new": problems}, indent=2))
    if checked == 0:
        print("No Mach-O files or Metal libraries found.", file=sys.stderr)
        return 1
    if problems:
        print(
            f"{len(problems)} file(s) need a newer macOS than {minimum_text}; install the "
            "wheels for the minimum macOS (see docs/releasing.md, Release venv).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
