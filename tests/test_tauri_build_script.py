from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
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
    assert "LDAI_COMP" not in environment
    assert "APPIMAGE_COMP" not in environment


def test_does_not_change_library_lookup_outside_linux(monkeypatch, tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "libworker.so").touch()
    base_environment = {"PATH": "/toolchain/bin"}

    monkeypatch.setattr(build, "ENGINE_DIR", engine)
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.setattr(build, "rust_build_environment", lambda: base_environment.copy())

    assert build.tauri_build_environment() == base_environment


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Linux packaging specific and requires POSIX symlink resolution",
)
def test_linuxdeploy_symlinks_private_rocm_soname_alias(monkeypatch, tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    torch_libraries = engine / "_internal" / "torch" / "lib"
    torch_libraries.mkdir(parents=True)
    rocm_libraries = {
        target_name: alias
        for alias, target_name in build.LINUXDEPLOY_PRIVATE_LIBRARY_ALIASES.items()
    }
    assert "libhipblas.so" in rocm_libraries
    assert rocm_libraries["libhipblas.so"] == "libhipblas.so.3"
    for library_name in rocm_libraries:
        (torch_libraries / library_name).touch()
    system_lib = tmp_path / "usr-local-lib"
    system_lib.mkdir()
    build_root = tmp_path / "build"
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setattr(build, "ENGINE_DIR", engine)
    monkeypatch.setattr(build, "BUILD_ROOT", build_root)
    monkeypatch.setattr(build, "LINUXDEPLOY_SYSTEM_LIB", system_lib)
    monkeypatch.setattr(build, "LINUXDEPLOY_DRIVER_LIBRARIES", ())
    monkeypatch.setattr(build.subprocess, "run", fake_run)

    created = build._symlink_engine_libs_for_linuxdeploy()

    for library_name, alias_name in rocm_libraries.items():
        rocm_library = torch_libraries / library_name
        direct_link = system_lib / library_name
        alias_link = system_lib / alias_name
        assert direct_link in created
        assert alias_link in created
        assert direct_link.resolve() == rocm_library
        assert alias_link.resolve() == rocm_library
    assert commands == [["sudo", "ldconfig"]]
    # Returning every link lets the caller clean up the isolated build host.
    for link in created:
        link.unlink()
    assert list(system_lib.iterdir()) == []
    assert all((torch_libraries / name).is_file() for name in rocm_libraries)


def test_resolves_windows_npm_command_wrapper(monkeypatch) -> None:
    npm = r"C:\toolcache\node\npm.cmd"
    monkeypatch.setattr(
        build.shutil,
        "which",
        lambda name, path=None: npm if name == "npm" else None,
    )

    assert build.npm_executable() == npm


@pytest.mark.skipif(os.name == "nt" or not shutil.which("cc"), reason="native Unix launcher")
def test_linuxdeploy_launcher_passes_graphics_exclusions_and_restores_tool(
    monkeypatch, tmp_path: Path
) -> None:
    tool = tmp_path / ".cache/tauri/linuxdeploy-x86_64.AppImage"
    tool.parent.mkdir(parents=True)
    capture = tmp_path / "arguments.txt"
    original = "#!/bin/sh\nprintf '%s\\n' \"$@\" > " + shlex.quote(str(capture)) + "\n"
    tool.write_text(original)
    tool.chmod(0o755)
    monkeypatch.setattr(build.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setattr(build, "BUILD_ROOT", tmp_path / "build")
    monkeypatch.setenv("LOCALSR_LINUXDEPLOY_WRAPPER_LOG", str(tmp_path / "wrapper.log"))

    backup = build._wrap_linuxdeploy_for_appimage()
    try:
        subprocess.run(
            [
                str(tool),
                "--appimage-extract-and-run",
                "--appdir",
                "App Dir",
                "--output",
                "appimage",
            ],
            check=True,
        )
        arguments = capture.read_text().splitlines()
        assert arguments[0] == "--appimage-extract-and-run"
        assert arguments[-4:] == ["--appdir", "App Dir", "--output", "appimage"]
        assert "--exclude-library=libwayland-client.so.0" in arguments
        assert "--exclude-library=libwayland-egl.so.1" in arguments
        assert "--exclude-library=libcuda.so.1" in arguments
        assert "--exclude-library=libnvidia-ml.so.1" in arguments
        assert len(arguments) == len(set(arguments))
    finally:
        build._restore_linuxdeploy_wrapper(backup)
    assert tool.read_text() == original
    assert not backup.exists()


@pytest.mark.skipif(os.name == "nt" or not shutil.which("cc"), reason="native Unix launcher")
def test_linuxdeploy_removes_plugin_copied_host_libraries_before_packaging(
    monkeypatch, tmp_path: Path
) -> None:
    tool = tmp_path / ".cache/tauri/linuxdeploy-x86_64.AppImage"
    tool.parent.mkdir(parents=True)
    appdir = tmp_path / "App Dir"
    libs = appdir / "usr/lib"
    libs.mkdir(parents=True)
    (libs / "libkeep.so").write_text("application library")
    calls = tmp_path / "calls"
    original = "#!/bin/sh\n" + (
        "printf '%s\\n' \"$*\" >> " + shlex.quote(str(calls)) + "\n"
        'case " $* " in\n'
        " *' --plugin gstreamer '*) touch "
        + shlex.quote(str(libs / "libwayland-client.so.0"))
        + " ;;\n"
        " *' --output appimage '*) test ! -e "
        + shlex.quote(str(libs / "libwayland-client.so.0"))
        + " || exit 24 ;;\n"
        "esac\n"
    )
    tool.write_text(original)
    tool.chmod(0o755)
    monkeypatch.setattr(build.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setattr(build, "BUILD_ROOT", tmp_path / "build")
    monkeypatch.setenv("LOCALSR_LINUXDEPLOY_WRAPPER_LOG", str(tmp_path / "wrapper.log"))
    backup = build._wrap_linuxdeploy_for_appimage()
    try:
        subprocess.run(
            [
                str(tool),
                "--appimage-extract-and-run",
                "--appdir",
                str(appdir),
                "--plugin",
                "gstreamer",
                "--output",
                "appimage",
            ],
            check=True,
        )
        deploy, package = calls.read_text().splitlines()
        assert "--plugin gstreamer" in deploy and "--output" not in deploy
        assert "--output appimage" in package and "--plugin" not in package
        assert not (libs / "libwayland-client.so.0").exists()
        assert (libs / "libkeep.so").read_text() == "application library"
    finally:
        build._restore_linuxdeploy_wrapper(backup)
    assert tool.read_text() == original


@pytest.mark.skipif(sys.platform == "win32", reason="Linux packaging specific")
def test_repacks_linux_appimage_payload_with_system_gzip(monkeypatch, tmp_path: Path) -> None:
    appimage_dir = tmp_path / "bundle" / "appimage"
    appdir = appimage_dir / "LocalSR Next Preview.AppDir"
    appdir.mkdir(parents=True)
    (appdir / "AppRun").write_text("#!/bin/sh\n", encoding="utf-8")
    image = appimage_dir / "LocalSR.AppImage"
    runtime = b"ELF-runtime-prefix"
    image.write_bytes(runtime + b"hsqs-old-zstd-payload")
    commands: list[list[str]] = []
    policy_checks: list[list[str]] = []

    def fake_run(command, **kwargs):
        if command[0] == str(image):
            return SimpleNamespace(returncode=0, stdout=f"{len(runtime)}\n")
        if str(command[1]).endswith("verify_codec_allowlist.py"):
            policy_checks.append(command)
            return SimpleNamespace(returncode=0, stdout="PASS", stderr="")
        commands.append(command)
        Path(command[2]).write_bytes(b"hsqs-gzip-payload")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setattr(
        build.shutil,
        "which",
        lambda name: "/usr/bin/mksquashfs" if name == "mksquashfs" else None,
    )
    monkeypatch.setattr(build.subprocess, "run", fake_run)

    repacked = build._repack_linux_appimages_with_system_mksquashfs(tmp_path / "bundle")

    assert repacked == [image]
    assert policy_checks and policy_checks[0][-2:] == ["--tree", str(appdir)]
    assert image.read_bytes() == runtime + b"hsqs-gzip-payload"
    assert image.stat().st_mode & 0o111
    assert commands == [
        [
            "/usr/bin/mksquashfs",
            str(appdir),
            str(image) + ".localsr-repacked.squashfs",
            "-noappend",
            "-comp",
            "gzip",
            "-no-duplicates",
        ]
    ]


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
