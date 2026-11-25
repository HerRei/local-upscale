from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The script imports its siblings, as it does when run directly.
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "verify_deb_dependencies", ROOT / "scripts/verify_deb_dependencies.py"
)
deps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deps)


def elf(machine: int = 62) -> bytes:
    return (
        b"\x7fELF"
        + bytes([2, 1, 1])
        + bytes(9)
        + (3).to_bytes(2, "little")
        + machine.to_bytes(2, "little")
    )


def readelf(needed: list[str], soname: str | None = None) -> str:
    lines = [
        f" 0x0000000000000001 (NEEDED)             Shared library: [{name}]" for name in needed
    ]
    if soname:
        lines.append(f" 0x000000000000000e (SONAME)             Library soname: [{soname}]")
    return "Dynamic section at offset 0x1 contains 3 entries:\n" + "\n".join(lines) + "\n"


def test_depends_alternatives_versions_and_architectures_are_read_as_package_names() -> None:
    assert deps.parse_depends(
        "libwebkit2gtk-4.1-0, libgtk-3-0, libc6 (>= 2.35), ocl-icd-libopencl1 | libopencl1:any"
    ) == [["libwebkit2gtk-4.1-0"], ["libgtk-3-0"], ["libc6"], ["ocl-icd-libopencl1", "libopencl1"]]
    assert deps.parse_depends("") == []


def test_the_apt_closure_and_required_packages_are_what_apt_install_brings() -> None:
    closure = deps.parse_apt_cache_depends(
        "libgtk-3-0\n  Depends: libc6\n  |Depends: libcairo2\n libcairo2\nlibc6\n<libopencl1>\n"
        "libdbus-1-3:amd64\n"
    )
    assert closure == {"libgtk-3-0", "libc6", "libopencl1", "libdbus-1-3"}
    assert deps.parse_required(
        "libc6:amd64\trequired\t\ndpkg\trequired\tyes\nlibssl3\timportant\t\nbash\toptional\tyes\n"
    ) == {"libc6", "dpkg", "bash"}


def test_only_libraries_the_package_lacks_and_no_driver_library_count(
    monkeypatch, tmp_path: Path
) -> None:
    tree = tmp_path / "deb"
    engine = tree / "usr/lib/LocalSR/engine"
    engine.mkdir(parents=True)
    (tree / "usr/bin").mkdir(parents=True)
    (tree / "usr/bin/localsr-next").write_bytes(elf())
    (engine / "localsr-worker").write_bytes(elf())
    (engine / "libprivate-1a2b.so").write_bytes(elf())
    (engine / "libz.so.1").symlink_to("libprivate-1a2b.so")
    (engine / "gpu-code.co").write_bytes(elf(machine=224))  # AMD GPU code: not host code
    (tree / "usr/share/applications").mkdir(parents=True)
    (tree / "usr/share/applications/LocalSR.desktop").write_text("[Desktop Entry]\n")
    outputs = {
        "localsr-next": readelf(["libwebkit2gtk-4.1.so.0", "libc.so.6"]),
        "localsr-worker": readelf(
            ["libprivate-1a2b.so", "libz.so.1", "libOpenCL.so.1", "libc.so.6"]
        ),
        "libprivate-1a2b.so": readelf(["libcuda.so.1", "libgomp-x.so.1"], soname="libgomp-x.so.1"),
    }

    def run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout=outputs[Path(command[-1]).name], stderr=""
        )

    monkeypatch.setattr(deps.subprocess, "run", run)
    provided, needs = deps.scan(tree)
    assert {"libz.so.1", "libgomp-x.so.1", "LocalSR.desktop"} <= provided
    assert deps.system_needs(provided, needs) == {
        "libOpenCL.so.1": ["usr/lib/LocalSR/engine/localsr-worker"],
        "libc.so.6": ["usr/bin/localsr-next", "usr/lib/LocalSR/engine/localsr-worker"],
        "libwebkit2gtk-4.1.so.0": ["usr/bin/localsr-next"],
    }


def test_a_system_library_from_outside_the_dependency_closure_fails() -> None:
    external = {
        "libc.so.6": ["usr/bin/localsr-next"],
        "libOpenCL.so.1": ["usr/lib/LocalSR/engine/libopenvino_intel_gpu_plugin.so"],
        "libfoo.so.1": ["usr/lib/LocalSR/engine/localsr-worker"],
    }
    owners = {"libc.so.6": {"libc6"}, "libOpenCL.so.1": {"ocl-icd-libopencl1"}}
    assert deps.missing_libraries(external, owners, {"libc6", "ocl-icd-libopencl1"}) == {
        "libfoo.so.1": {"owners": [], "used_by": ["usr/lib/LocalSR/engine/localsr-worker"]}
    }
    assert deps.missing_libraries(external, owners, {"libc6"})["libOpenCL.so.1"] == {
        "owners": ["ocl-icd-libopencl1"],
        "used_by": ["usr/lib/LocalSR/engine/libopenvino_intel_gpu_plugin.so"],
    }


def test_the_deb_declares_the_glibc_floor_the_appimage_is_held_to() -> None:
    import verify_appimage_libc

    config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text())
    depends = config["bundle"]["linux"]["deb"]["depends"]
    assert f"libc6 (>= {verify_appimage_libc.DEFAULT_MAX})" in depends
