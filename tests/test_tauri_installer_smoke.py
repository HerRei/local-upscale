from __future__ import annotations

import importlib.util
import json
import plistlib
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "smoke_tauri_installer", ROOT / "scripts" / "smoke_tauri_installer.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def write_pe(path: Path, machine: int) -> None:
    payload = bytearray(256)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 128)
    payload[128:132] = b"PE\0\0"
    struct.pack_into("<H", payload, 132, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_reads_the_declared_macos_bundle_executable(tmp_path: Path) -> None:
    app = tmp_path / "LocalSR Next Preview.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "localsr-next"
    executable.write_bytes(b"binary")
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "localsr-next"}, handle)

    assert smoke.app_executable(app) == executable


def test_selects_the_app_executable_not_the_uninstaller(tmp_path: Path) -> None:
    (tmp_path / "Uninstall.exe").write_bytes(b"uninstaller")
    expected = tmp_path / "LocalSR Next Preview.exe"
    expected.write_bytes(b"app")

    assert smoke.installed_windows_executable(tmp_path) == expected


def test_selects_the_windows_host_instead_of_the_bundled_worker(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "localsr-worker.exe").write_bytes(b"worker")
    expected = tmp_path / "LocalSR Next Preview.exe"
    expected.write_bytes(b"app")

    assert smoke.installed_windows_executable(tmp_path) == expected


def test_resolves_exactly_one_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "LocalSR.AppImage"
    artifact.write_bytes(b"package")

    assert smoke.resolve_artifact(str(tmp_path / "*.AppImage")) == artifact.resolve()


def test_retries_a_busy_macos_test_mount(monkeypatch) -> None:
    return_codes = iter((1, 1, 0))
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, next(return_codes))

    monkeypatch.setattr(smoke, "run", fake_run)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)

    smoke.detach_dmg("/dev/disk-test", {}, 30)

    assert calls == [["hdiutil", "detach", "/dev/disk-test"]] * 3


def test_windows_smoke_uses_installed_host_without_starting_webview(
    tmp_path: Path, monkeypatch
) -> None:
    artifact = tmp_path / "LocalSR-setup.exe"
    write_pe(artifact, 0x014C)
    report = tmp_path / "report.json"
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "/S" in command:
            install_argument = next(value for value in command if value.startswith("/D="))
            install_dir = Path(install_argument.removeprefix("/D="))
            install_dir.mkdir(parents=True)
            write_pe(install_dir / "LocalSR Next Preview.exe", 0x8664)
            worker = install_dir / "engine" / "localsr-worker.exe"
            write_pe(worker, 0x8664)
        elif command[1:] == ["--headless-smoke-test"]:
            worker = Path(command[0]).parent / "engine" / "localsr-worker.exe"
            report.write_text(
                json.dumps({"passed": True, "worker": "ready", "worker_path": str(worker)}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(smoke, "run", fake_run)
    smoke.smoke_windows(artifact, report, {}, 240)

    assert calls[1][1:] == ["--headless-smoke-test"]
    evidence = json.loads(report.read_text(encoding="utf-8"))["package_architecture"]
    assert evidence["container"]["architecture"] == "x86"
    assert {item["architecture"] for item in evidence["native_payloads"]} == {"x86_64"}


def test_macos_smoke_bypasses_single_instance_forwarding(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "LocalSR.dmg"
    artifact.write_bytes(b"dmg")
    mount = tmp_path / "mounted"
    app = mount / "LocalSR Next Preview.app"
    executable = app / "Contents" / "MacOS" / "localsr-next"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"host")
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "localsr-next"}, handle)
    calls: list[list[str]] = []

    monkeypatch.setattr(smoke, "mount_dmg", lambda *_args: (mount, "/dev/test"))
    monkeypatch.setattr(smoke, "detach_dmg", lambda *_args: None)

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(smoke, "run", fake_run)
    smoke.smoke_macos(artifact, tmp_path / "report.json", {}, 240)

    assert calls == [[str(executable), "--headless-smoke-test"]]


def test_process_timeout_preserves_captured_diagnostics(monkeypatch, capsys) -> None:
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            ["installed-host"], 12, output=b"starting worker", stderr=b"startup detail"
        )

    monkeypatch.setattr(smoke.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        smoke.run(["installed-host"], env={}, timeout=12)

    output = capsys.readouterr().out
    assert "starting worker" in output
    assert "startup detail" in output


def test_linux_smoke_runs_the_appimage_host_without_a_webview(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "LocalSR.AppImage"
    artifact.write_bytes(b"appimage")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, *, env, **_kwargs):
        calls.append((command, env.copy()))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(smoke, "run", fake_run)

    smoke.smoke_linux(artifact, tmp_path / "report.json", {}, 240)

    assert len(calls) == 1
    command, environment = calls[0]
    assert command == [str(artifact), "--headless-smoke-test"]
    assert environment["APPIMAGE_EXTRACT_AND_RUN"] == "1"
    if smoke.os.name != "nt":
        assert artifact.stat().st_mode & 0o111
