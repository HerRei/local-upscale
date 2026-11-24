"""Issue #181: the media runtime build in a uv venv and behind a certificate Python cannot verify."""

import ssl
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging" / "ffmpeg"))
sys.path.insert(0, str(ROOT / "scripts"))
import build_lgpl_media as media  # noqa: E402
import install_media_runtime as installer  # noqa: E402


def _certificate_error(*_args, **_kwargs):
    raise urllib.error.URLError(
        ssl.SSLCertVerificationError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    )


def test_download_falls_back_to_curl_only_for_certificate_errors(monkeypatch, tmp_path):
    partial = tmp_path / "opus.tar.gz.part"
    calls = []

    def curl(command, **kwargs):
        calls.append(command)
        Path(command[command.index("-o") + 1]).write_bytes(b"archive")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media.urllib.request, "urlopen", _certificate_error)
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(media.subprocess, "run", curl)
    media._download("https://downloads.xiph.org/releases/opus/opus.tar.gz", partial)
    assert partial.read_bytes() == b"archive"
    assert calls[0][0] == "/usr/bin/curl" and "=https" in calls[0]

    def refused(*_args, **_kwargs):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(media.urllib.request, "urlopen", refused)
    calls.clear()
    with pytest.raises(OSError):
        media._download("https://example.invalid/a.tar.gz", partial)
    assert calls == []


def test_fetch_source_still_rejects_a_curl_download_with_the_wrong_digest(monkeypatch, tmp_path):
    monkeypatch.setattr(media.urllib.request, "urlopen", _certificate_error)
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/curl")

    def curl(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_bytes(b"tampered")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media.subprocess, "run", curl)
    entry = {
        "name": "opus",
        "filename": "opus.tar.gz",
        "url": "https://downloads.xiph.org/opus.tar.gz",
        "sha256": "0" * 64,
    }
    with pytest.raises(media.BuildError, match="SHA-256"):
        media.fetch_source(entry, tmp_path)
    assert not (tmp_path / "opus.tar.gz").exists()


def test_pip_install_uses_uv_when_the_venv_has_no_pip(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, b"", b"No module named pip")

    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setattr(installer, "run", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/opt/homebrew/bin/uv")
    installer.pip_install("/venv/bin/python", "--no-deps", "--force-reinstall", "av.whl")
    assert commands[-1] == [
        "/opt/homebrew/bin/uv",
        "pip",
        "install",
        "--python",
        "/venv/bin/python",
        "--no-deps",
        "--reinstall",
        "av.whl",
    ]
