from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_worker_target_arch_matches_pyinstaller_names() -> None:
    assert build.worker_target_arch("aarch64-apple-darwin") == "arm64"
    assert build.worker_target_arch("x86_64-pc-windows-msvc") == "x86_64"
    assert build.worker_target_arch(None) is None


def _isolated_bundle_paths(monkeypatch, tmp_path: Path, *, torch_version: str = "2.13.0") -> Path:
    engine = tmp_path / "engine"
    engine.mkdir()
    license_path = tmp_path / "LICENSE"
    notices = tmp_path / "THIRD_PARTY_NOTICES.md"
    license_path.write_text("MIT", encoding="utf-8")
    notices.write_text("notices", encoding="utf-8")
    config = tmp_path / "bundle.json"
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "ENGINE_DIR", engine)
    monkeypatch.setattr(build, "CONFIG_PATH", config)
    installed_version = importlib.metadata.version

    def dependency_version(name):
        # Bundle-policy fixtures must be independent of the host's ML runtime.
        return torch_version if name == "torch" else installed_version(name)

    monkeypatch.setattr(importlib.metadata, "version", dependency_version)
    return config


def test_release_overlay_refuses_unsigned_macos(monkeypatch, tmp_path: Path) -> None:
    _isolated_bundle_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.delenv("APPLE_SIGNING_IDENTITY", raising=False)
    monkeypatch.delenv("APPLE_CERTIFICATE", raising=False)

    with pytest.raises(SystemExit, match="signing and notarization"):
        build.write_bundle_overlay(require_signing=True)


def test_release_overlay_refuses_signed_but_unnotarized_macos(monkeypatch, tmp_path: Path) -> None:
    _isolated_bundle_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.setenv("APPLE_SIGNING_IDENTITY", "Developer ID Application: Example")
    for name in (
        "APPLE_ID",
        "APPLE_PASSWORD",
        "APPLE_TEAM_ID",
        "APPLE_API_KEY",
        "APPLE_API_ISSUER",
        "APPLE_API_KEY_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit, match="signing and notarization"):
        build.write_bundle_overlay(require_signing=True)


@pytest.mark.parametrize("torch_version,minimum", [("2.13.0", "14.0"), ("2.2.2", "12.0")])
def test_macos_overlay_preserves_the_runtime_deployment_floor(
    monkeypatch, tmp_path: Path, torch_version: str, minimum: str
) -> None:
    config = _isolated_bundle_paths(monkeypatch, tmp_path, torch_version=torch_version)
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.delenv("APPLE_SIGNING_IDENTITY", raising=False)
    monkeypatch.delenv("APPLE_CERTIFICATE", raising=False)

    build.write_bundle_overlay()

    base = json.loads((build.DESKTOP / "src-tauri" / "tauri.conf.json").read_text())
    overlay = json.loads(config.read_text())["bundle"]["macOS"]
    effective = {**base["bundle"]["macOS"], **overlay}
    assert effective["minimumSystemVersion"] == minimum
    assert effective["signingIdentity"] == "-"


def test_windows_release_overlay_configures_authenticode(monkeypatch, tmp_path: Path) -> None:
    config = _isolated_bundle_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(build.sys, "platform", "win32")
    monkeypatch.setattr(build.os, "name", "nt")
    monkeypatch.setenv("WINDOWS_CERTIFICATE_THUMBPRINT", "A1B2C3")
    monkeypatch.setenv("WINDOWS_TIMESTAMP_URL", "https://timestamp.example.test")

    build.write_bundle_overlay(require_signing=True)

    windows = json.loads(config.read_text(encoding="utf-8"))["bundle"]["windows"]
    assert windows == {
        "certificateThumbprint": "A1B2C3",
        "digestAlgorithm": "sha256",
        "timestampUrl": "https://timestamp.example.test",
    }


def test_linux_tauri_environments_use_the_managed_ssd_scratch() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text(encoding="utf-8")

    assert workflow.count('ROOT="/ci-scratch/localsr-tauri/') == 2
    assert workflow.count('VENV="$ROOT/venv"') == 2
    assert workflow.count("--ssd-path /ci-scratch") == 2
    assert workflow.count('export TMPDIR="$ROOT/tmp"') == 2
    assert workflow.count("PIP_CACHE_DIR=/ci-scratch/cache/pip") == 2


def test_windows_tauri_environments_bound_paths_and_native_failures() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text(encoding="utf-8")

    assert "C:\\lsr-ci\\$env:GITHUB_RUN_ID\\$env:GITHUB_RUN_ATTEMPT\\t\\verify" in workflow
    assert "C:\\lsr-ci\\$env:GITHUB_RUN_ID\\$env:GITHUB_RUN_ATTEMPT\\t\\package" in workflow
    assert workflow.count('"TEMP=$tmp"') == 2
    assert workflow.count('"TMP=$tmp"') == 2
    assert workflow.count("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }") == 5
    assert workflow.count("Remove-Item -LiteralPath $env:LOCALSR_PREVIEW_ROOT") == 2
