from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backend_wheelhouse
from prepare_engine_payload import prepare
from release_targets import ROOT, targets, validate_manifest
from tauri_public_assets import prepare_installer


def test_engine_payload_streams_bounded_parts_with_exact_contents(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "localsr-worker.exe").write_bytes(bytes(range(256)) * 8)
    (engine / "_internal").mkdir()
    (engine / "_internal/runtime.dll").write_bytes(b"native-runtime")
    manifest = json.loads(
        prepare(engine, tmp_path / "parts", "cuda.engine", part_bytes=41).read_text()
    )
    assert len(manifest["parts"]) > 1
    archive = bytearray()
    for part in manifest["parts"]:
        data = (tmp_path / "parts" / part["filename"]).read_bytes()
        assert len(data) == part["size"] <= 41
        assert hashlib.sha256(data).hexdigest() == part["sha256"]
        archive.extend(data)
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive))) as tar:
        contents = {member.name: tar.extractfile(member).read() for member in tar}
    assert contents == {
        p.relative_to(engine).as_posix(): p.read_bytes() for p in engine.rglob("*") if p.is_file()
    }
    assert manifest["file_count"] == 2
    assert manifest["unpacked_bytes"] == sum(map(len, contents.values()))


def test_large_appimage_has_a_verified_reassembly_script(tmp_path: Path):
    source = tmp_path / "LocalSR.AppImage"
    source.write_bytes(b"linux-appimage" * 20)
    output = tmp_path / "output"
    output.mkdir()
    records = prepare_installer(source, output, part_bytes=51)
    assert all(item["size"] <= 51 for item in records[:-1])
    script = output / records[-1]["filename"]
    if sys.platform == "win32":
        return  # The reassembly helper targets Linux, and Windows need not have Bash.
    bash = shutil.which("bash")
    assert bash
    environment = os.environ.copy()
    if sys.platform == "darwin":
        # macOS's Perl shasum has the same check interface for this Linux helper.
        shims = tmp_path / "shims"
        shims.mkdir()
        checksum = shims / "sha256sum"
        checksum.write_text('#!/bin/sh\nexec /usr/bin/shasum -a 256 "$@"\n')
        checksum.chmod(0o755)
        environment["PATH"] = str(shims) + os.pathsep + environment["PATH"]
    subprocess.run([bash, str(script)], check=True, env=environment)
    assert (output / source.name).read_bytes() == source.read_bytes()
    (output / source.name).unlink()
    (output / records[0]["filename"]).write_bytes(b"corrupted")
    assert subprocess.run([bash, str(script)], env=environment).returncode != 0
    assert not (output / source.name).exists()


def test_wheelhouse_reuse_verifies_files_and_invalidates_changed_locks(tmp_path: Path, monkeypatch):
    lock = tmp_path / "lock.txt"
    lock.write_text("demo==1\n")
    calls = []

    def build(command, **kwargs):
        calls.append(command)
        stage = Path(command[command.index("--wheel-dir") + 1])
        with zipfile.ZipFile(stage / "demo-1-py3-none-any.whl", "w") as wheel:
            wheel.writestr("demo-1.dist-info/METADATA", "Name: demo\nVersion: 1\n")

    monkeypatch.setattr(backend_wheelhouse.subprocess, "run", build)
    cache = tmp_path / "cache"
    path, _ = backend_wheelhouse.prepare(lock, cache, "test")
    assert backend_wheelhouse.prepare(lock, cache, "test")[0] == path
    assert len(calls) == 1
    with (path / "demo-1-py3-none-any.whl").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="digest"):
        backend_wheelhouse.prepare(lock, cache, "test")
    lock.write_text("demo==1\n# new reviewed inputs\n")
    assert backend_wheelhouse.prepare(lock, cache, "test")[0] != path
    assert len(calls) == 2


def test_registry_covers_all_backends():
    entries = targets()
    assert {(t["platform"], t["backend"]) for t in entries} == {
        ("windows", "CPU"),
        ("windows", "CUDA"),
        ("windows", "DirectML"),
        ("linux", "CPU"),
        ("linux", "CUDA"),
        ("linux", "Intel-XPU"),
        ("linux", "AMD-ROCm"),
        ("macos", "MPS"),
    }
    for name in ("tauri-release-artifacts.json", "v0.0.12-cross-alpha-artifacts.json"):
        manifest = json.loads((ROOT / "ci" / name).read_text())
        validate_manifest(manifest)
        manifest["artifacts"].pop()
        with pytest.raises(ValueError, match="every target"):
            validate_manifest(manifest)


@pytest.mark.parametrize("flavor", ["cu126", "rocm7.2", "xpu"])
def test_installed_cpu_probe_rejects_gpu_runtime_identity(flavor: str, monkeypatch):
    import torch

    from localsr.core.backend_validation import probe

    monkeypatch.setattr(torch, "__version__", f"2.13.0+{flavor}")
    monkeypatch.setattr(torch.version, "cuda", "12.6" if flavor == "cu126" else None)
    monkeypatch.setattr(torch.version, "hip", "7.2" if flavor == "rocm7.2" else None)
    with pytest.raises(RuntimeError, match="CPU artifact contains a GPU"):
        probe("CPU")


def test_backend_locks_have_the_named_torch_flavor_and_native_dependencies():
    for target in targets():
        if target["platform"] == "macos":
            continue
        lock = (ROOT / "requirements/locks" / f"{target['id']}.txt").read_text()
        flavor = "cpu" if target["backend"] == "DirectML" else target["index"]
        version = "2.13.0"
        assert f"torch=={version}+{flavor}" in lock
        assert "--hash=sha256:" in lock
        if target["backend"] == "DirectML":
            assert "onnxruntime-directml==1.24.4" in lock
            assert "onnx==1.22.0" in lock
            assert "torch-directml==" not in lock
        if target["id"] == "linux-x86_64-cuda":
            assert "nvidia-cublas-cu12==" in lock
        if target["id"] == "linux-x86_64-xpu":
            assert "intel-sycl-rt==" in lock
