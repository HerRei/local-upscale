from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_tauri_preview", ROOT / "scripts" / "build_tauri_preview.py"
)
assert SPEC and SPEC.loader
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def test_keeps_an_environment_that_already_exposes_cargo(monkeypatch) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(["/toolchain/bin", "/usr/bin"]))
    monkeypatch.setattr(
        build.shutil,
        "which",
        lambda name, path=None: "/toolchain/bin/cargo" if name == "cargo" else None,
    )

    environment = build.rust_build_environment()

    assert environment["PATH"].split(os.pathsep)[0] == "/toolchain/bin"


def test_resolves_cargo_through_rustup_for_noninteractive_builds(
    monkeypatch, tmp_path: Path
) -> None:
    cargo = tmp_path / "toolchain" / "bin" / "cargo"
    cargo.parent.mkdir(parents=True)
    cargo.touch()
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(
        build.shutil,
        "which",
        lambda name, path=None: "/usr/bin/rustup" if name == "rustup" else None,
    )
    invocation: list[str] = []

    def fake_run(command, **_kwargs):
        invocation.extend(command)
        return SimpleNamespace(stdout=f"{cargo}\n")

    monkeypatch.setattr(build.subprocess, "run", fake_run)

    environment = build.rust_build_environment()

    assert invocation == ["/usr/bin/rustup", "which", "cargo"]
    assert environment["PATH"].split(os.pathsep)[0] == str(cargo.parent)


def test_exposes_private_worker_libraries_only_to_linux_packager(
    monkeypatch, tmp_path: Path
) -> None:
    engine = tmp_path / "engine"
    numpy_libraries = engine / "_internal" / "numpy.libs"
    torch_libraries = engine / "_internal" / "torch" / "lib"
    numpy_libraries.mkdir(parents=True)
    torch_libraries.mkdir(parents=True)
    (numpy_libraries / "libquadmath-private.so.0").touch()
    (torch_libraries / "libtorch_cpu.so").touch()
    (engine / "README.txt").touch()

    monkeypatch.setattr(build, "ENGINE_DIR", engine)
    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/system/libraries")
    monkeypatch.setattr(
        build,
        "rust_build_environment",
        lambda: {
            "PATH": "/toolchain/bin",
            "LD_LIBRARY_PATH": "/system/libraries",
        },
    )

    environment = build.tauri_build_environment()
    library_path = environment["LD_LIBRARY_PATH"].split(os.pathsep)

    assert library_path == [
        str(numpy_libraries),
        str(torch_libraries),
        "/system/libraries",
    ]


def test_does_not_change_library_lookup_outside_linux(monkeypatch, tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "libworker.so").touch()
    base_environment = {"PATH": "/toolchain/bin"}

    monkeypatch.setattr(build, "ENGINE_DIR", engine)
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.setattr(build, "rust_build_environment", lambda: base_environment.copy())

    assert build.tauri_build_environment() == base_environment


def test_resolves_windows_npm_command_wrapper(monkeypatch) -> None:
    npm = r"C:\toolcache\node\npm.cmd"
    monkeypatch.setattr(
        build.shutil,
        "which",
        lambda name, path=None: npm if name == "npm" else None,
    )

    assert build.npm_executable() == npm


def test_linux_tauri_environments_use_the_managed_ssd_scratch() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text(encoding="utf-8")

    assert workflow.count('VENV="/ci-scratch/localsr-tauri/') == 2
    assert workflow.count("--ssd-path /ci-scratch") == 2
