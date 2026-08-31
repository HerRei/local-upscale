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
