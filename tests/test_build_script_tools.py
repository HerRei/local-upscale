"""build_tauri_preview.py leaves the host as it found it when a build step fails."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "_build_tauri_preview_cleanup", ROOT / "scripts" / "build_tauri_preview.py"
)
assert SPEC and SPEC.loader
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="an AppImage is built on Linux only"
)
def test_engine_library_links_are_removed_when_the_linuxdeploy_wrapper_cannot_be_set_up(
    monkeypatch, tmp_path: Path
) -> None:
    link = tmp_path / "libhashed.so"
    link.symlink_to(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        sys, "argv", ["build", "--skip-worker", "--skip-checks", "--bundles", "appimage"]
    )
    monkeypatch.setattr(build, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(build, "write_bundle_overlay", lambda **kwargs: None)
    monkeypatch.setattr(
        build, "tauri_build_environment", lambda: {"CARGO_TARGET_DIR": str(tmp_path)}
    )
    monkeypatch.setattr(build, "_symlink_engine_libs_for_linuxdeploy", lambda: [link])

    def refuse() -> None:
        raise SystemExit("stale linuxdeploy backup requires manual cleanup")

    monkeypatch.setattr(build, "_wrap_linuxdeploy_for_appimage", refuse)
    monkeypatch.setattr(
        build.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0),
    )

    with pytest.raises(SystemExit, match="stale linuxdeploy backup"):
        build.main()

    assert not link.is_symlink()
    assert ["sudo", "ldconfig"] in commands


def test_linux_smoke_keeps_the_job_temp_dir_after_the_runtime_directory_is_removed(
    monkeypatch, tmp_path: Path
) -> None:
    import subprocess

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "_smoke_tauri_installer_tmpdir", ROOT / "scripts" / "smoke_tauri_installer.py"
    )
    assert spec and spec.loader
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

    artifact = tmp_path / "LocalSR.AppImage"
    artifact.write_bytes(b"\0")
    job_temp = tmp_path / "job-temp"
    job_temp.mkdir()
    seen: list[str | None] = []

    def fake_run(command, *, env, timeout, check=True):
        seen.append(env.get("TMPDIR"))
        if len(seen) == 1:
            raise subprocess.CalledProcessError(1, command)
        return None

    apprun = tmp_path / "AppRun"
    monkeypatch.setattr(smoke, "run", fake_run)
    monkeypatch.setattr(smoke.shutil, "which", lambda name: None)
    monkeypatch.setattr(smoke, "extract_appimage_payload", lambda *args: tmp_path)
    monkeypatch.setattr(smoke, "resolve_extracted_apprun", lambda root: (tmp_path, apprun))

    smoke.smoke_linux(
        artifact, tmp_path / "report.json", {"TMPDIR": str(job_temp), "PATH": ""}, timeout=5
    )

    assert len(seen) == 2
    assert seen[1] == str(job_temp)
