from __future__ import annotations

import importlib.util
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "smoke_tauri_installer", ROOT / "scripts" / "smoke_tauri_installer.py"
)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


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
