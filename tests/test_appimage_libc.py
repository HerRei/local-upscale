from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_appimage_libc", ROOT / "scripts/verify_appimage_libc.py"
)
libc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(libc)

# GNU objdump -T: the version of a required symbol, in parentheses when hidden.
NEEDS_2_38 = """
DYNAMIC SYMBOL TABLE:
0000000000000000      DF *UND*\t0000000000000000 (GLIBC_2.34) __libc_start_main
0000000000000000      DF *UND*\t0000000000000000  GLIBC_2.38  __isoc23_strtol
0000000000000000      DF *UND*\t0000000000000000  GLIBC_2.2.5 free
0000000000001234 g    DF .text\t0000000000000010  GLIBC_2.40  defined_here
"""
NEEDS_2_17 = "0000000000000000      DF *UND*\t0000000000000000  GLIBC_2.17  clock_gettime\n"


def fake_objdump(monkeypatch, outputs: dict[str, str], unreadable: frozenset[str] = frozenset()):
    def run(command, **_kwargs):
        name = Path(command[-1]).name
        if name in unreadable:
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr=f"objdump: {name}: file format not recognized"
            )
        return subprocess.CompletedProcess(command, 0, stdout=outputs.get(name, ""), stderr="")

    monkeypatch.setattr(libc.subprocess, "run", run)


def elf(machine: int = 62) -> bytes:
    """An ELF header up to e_machine: 64-bit, little-endian, shared object."""
    return (
        b"\x7fELF"
        + bytes([2, 1, 1])
        + bytes(9)
        + (3).to_bytes(2, "little")
        + machine.to_bytes(2, "little")
    )


def test_only_required_symbols_count_and_versions_compare_by_number(monkeypatch, tmp_path):
    fake_objdump(monkeypatch, {"tool": NEEDS_2_38})
    assert libc.required_glibc(tmp_path / "tool") == "2.38"
    assert libc.version_key("2.4") < libc.version_key("2.35") < libc.version_key("2.38")


def tree_with(tmp_path: Path) -> tuple[Path, Path]:
    tree = tmp_path / "squashfs-root"
    (tree / "usr/lib").mkdir(parents=True)
    (tree / "usr/lib/libnew.so").write_bytes(elf())
    (tree / "usr/lib/libold.so").write_bytes(elf())
    (tree / "usr/lib/script.sh").write_bytes(b"#!/bin/sh\n")
    (tree / "usr/lib/link.so").symlink_to("libnew.so")
    appimage = tmp_path / "App.AppImage"
    appimage.write_bytes(elf())
    return tree, appimage


def run_main(monkeypatch, *arguments: str) -> int:
    monkeypatch.setattr(sys, "argv", ["verify_appimage_libc.py", *arguments])
    return libc.main()


def test_a_library_newer_than_the_ceiling_fails_with_its_path(monkeypatch, tmp_path):
    tree, appimage = tree_with(tmp_path)
    fake_objdump(monkeypatch, {"libnew.so": NEEDS_2_38, "libold.so": NEEDS_2_17})
    report = tmp_path / "libc.json"
    code = run_main(monkeypatch, str(tree), "--appimage", str(appimage), "--report", str(report))
    result = json.loads(report.read_text())
    assert code == 1
    assert result["newest_required"] == "2.38"
    assert result["offenders"] == [{"path": "usr/lib/libnew.so", "glibc": "2.38"}]
    assert result["elf_files_using_glibc"] == 2  # the symlink and the script do not count


def test_the_appimage_runtime_counts_too(monkeypatch, tmp_path):
    tree, appimage = tree_with(tmp_path)
    fake_objdump(monkeypatch, {"libold.so": NEEDS_2_17, "App.AppImage": NEEDS_2_38})
    report = tmp_path / "libc.json"
    assert run_main(monkeypatch, str(tree), "--appimage", str(appimage), "--report", str(report))
    assert json.loads(report.read_text())["offenders"][0]["path"] == "(AppImage runtime)"


def test_a_payload_within_the_ceiling_passes(monkeypatch, tmp_path):
    tree, appimage = tree_with(tmp_path)
    fake_objdump(monkeypatch, {"libnew.so": NEEDS_2_38, "libold.so": NEEDS_2_17})
    assert run_main(monkeypatch, str(tree), "--appimage", str(appimage), "--max", "2.38") == 0


def test_a_tree_without_host_code_using_glibc_fails(monkeypatch, tmp_path, capsys):
    _tree, appimage = tree_with(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    gpu_only = tmp_path / "gpu-only"
    gpu_only.mkdir()
    (gpu_only / "kernels.co").write_bytes(elf(machine=224))  # EM_AMDGPU
    fake_objdump(monkeypatch, {"App.AppImage": NEEDS_2_17, "kernels.co": NEEDS_2_17})
    for tree in (empty, gpu_only):
        assert run_main(monkeypatch, str(tree), "--appimage", str(appimage)) == 2
        assert "no ELF file" in capsys.readouterr().err


def test_a_file_objdump_cannot_read_fails_the_check(monkeypatch, tmp_path, capsys):
    tree, appimage = tree_with(tmp_path)
    fake_objdump(monkeypatch, {"libold.so": NEEDS_2_17}, unreadable=frozenset({"libnew.so"}))
    assert run_main(monkeypatch, str(tree), "--appimage", str(appimage)) == 2
    assert "libnew.so" in capsys.readouterr().err


def test_gpu_code_objects_are_not_host_code(monkeypatch, tmp_path):
    tree, appimage = tree_with(tmp_path)
    (tree / "usr/lib/kernels.co").write_bytes(elf(machine=224))  # EM_AMDGPU
    (tree / "usr/lib/short.so").write_bytes(b"\x7fELF")
    fake_objdump(
        monkeypatch,
        {"libnew.so": NEEDS_2_17, "libold.so": NEEDS_2_17},
        unreadable=frozenset({"kernels.co", "short.so"}),
    )
    assert run_main(monkeypatch, str(tree), "--appimage", str(appimage)) == 0
