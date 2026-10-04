from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_edit_runtime", ROOT / "scripts/build_edit_runtime.py"
)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def fixture_archive(monkeypatch, tmp_path, entries):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    cache = tmp_path / ".cache"
    cache.mkdir()
    archive = cache / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for name, content in entries:
            output.writestr(name, content)
    return archive, (archive.name, hashlib.sha256(archive.read_bytes()).hexdigest())


def test_only_verified_native_executables_and_libraries_are_extracted(monkeypatch, tmp_path):
    _, asset = fixture_archive(
        monkeypatch,
        tmp_path,
        [
            ("bin/sd-cli", b"fixture"),
            ("lib/native.so.1", b"library"),
            ("models/weights.gguf", b"not wanted"),
        ],
    )
    destination = tmp_path / "runtime"
    destination.mkdir()
    assert build.unpack_verified_release(asset, destination) == {"sd-cli", "native.so.1"}
    assert not (destination / "weights.gguf").exists()
    assert (destination / "sd-cli").stat().st_mode & 0o111


@pytest.mark.parametrize("name", ["../sd-cli", "/sd-cli", "C:\\bin\\sd-cli.exe", "..\\sd-cli.exe"])
def test_native_archives_reject_unsafe_paths(monkeypatch, tmp_path, name):
    _, asset = fixture_archive(monkeypatch, tmp_path, [(name, b"fixture")])
    destination = tmp_path / "runtime"
    destination.mkdir()
    with pytest.raises(ValueError, match="Unsafe"):
        build.unpack_verified_release(asset, destination)


def test_native_archives_reject_flattened_filename_collisions(monkeypatch, tmp_path):
    _, asset = fixture_archive(monkeypatch, tmp_path, [("a/sd-cli", b"one"), ("b/sd-cli", b"two")])
    destination = tmp_path / "runtime"
    destination.mkdir()
    with pytest.raises(ValueError, match="Duplicate"):
        build.unpack_verified_release(asset, destination)


def test_bad_download_checksum_never_installs_native_code(monkeypatch, tmp_path):
    _, asset = fixture_archive(monkeypatch, tmp_path, [("sd-cli", b"fixture")])
    monkeypatch.setattr(
        build.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: __import__("io").BytesIO(b"tampered"),
    )
    destination = tmp_path / "runtime"
    destination.mkdir()
    with pytest.raises(ValueError, match="checksum mismatch"):
        build.unpack_verified_release((asset[0], "0" * 64), destination)
    assert not list(destination.iterdir())
