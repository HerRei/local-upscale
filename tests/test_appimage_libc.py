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


def fake_objdump(monkeypatch, outputs: dict[str, str]) -> None:
    def run(command, **_kwargs):
        stdout = outputs.get(Path(command[-1]).name, "")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(libc.subprocess, "run", run)


def test_only_required_symbols_count_and_versions_compare_by_number(monkeypatch, tmp_path):
    fake_objdump(monkeypatch, {"tool": NEEDS_2_38})
    assert libc.required_glibc(tmp_path / "tool") == "2.38"
    assert libc.version_key("2.4") < libc.version_key("2.35") < libc.version_key("2.38")


def tree_with(tmp_path: Path) -> tuple[Path, Path]:
    tree = tmp_path / "squashfs-root"
    (tree / "usr/lib").mkdir(parents=True)
    (tree / "usr/lib/libnew.so").write_bytes(b"\x7fELF new")
    (tree / "usr/lib/libold.so").write_bytes(b"\x7fELF old")
    (tree / "usr/lib/script.sh").write_bytes(b"#!/bin/sh\n")
    (tree / "usr/lib/link.so").symlink_to("libnew.so")
    appimage = tmp_path / "App.AppImage"
    appimage.write_bytes(b"\x7fELF runtime")
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
