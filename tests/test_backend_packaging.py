from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backend_wheelhouse
from prepare_engine_payload import prepare
from release_targets import ROOT, targets, validate_manifest


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
        ("linux", "AMD-ROCm"),
        ("macos", "MPS"),
    }
    # Intel XPU stays in the source but is not distributed until Intel's runtime
    # redistribution terms are cleared.
    registry = json.loads((ROOT / "ci" / "tauri-targets.json").read_text())
    assert [t["backend"] for t in registry["withheld_targets"]] == ["Intel-XPU"]
    manifest = json.loads((ROOT / "ci" / "tauri-release-artifacts.json").read_text())
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
            # Both ONNX Runtime wheels install the same package; plain ORT would replace
            # DirectML's. The OpenVINO plugin registers into the DirectML build.
            assert "\nonnxruntime==" not in lock
            assert "onnxruntime-ep-openvino==1.8.1" in lock
        if target["id"] == "linux-x86_64-cpu":
            # ONNX Runtime lists Intel NPUs on Linux only from 1.28 on (/sys/class/accel).
            assert "onnxruntime==1.31.1" in lock
            assert "onnxruntime-ep-openvino==1.8.1" in lock
            assert "onnxruntime-directml" not in lock
        elif target["backend"] != "DirectML":
            # The GPU editions keep their own runtimes and size.
            assert "onnxruntime-ep-openvino" not in lock
        if target["id"] == "linux-x86_64-cuda":
            assert "nvidia-cublas-cu12==" in lock
        if target["id"] == "linux-x86_64-xpu":
            assert "intel-sycl-rt==" in lock


def _elf(machine: int = 62, elf_class: int = 2) -> bytes:
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = elf_class
    header[18:20] = machine.to_bytes(2, "little")
    return bytes(header)


def test_the_engine_prefers_a_newer_host_cpp_runtime(tmp_path, monkeypatch):
    from localsr.worker import host_runtime
    from localsr.worker.host_runtime import prefer_newer_host_runtime

    # The test process may already hold its own copies; the frozen engine does not.
    monkeypatch.setattr(host_runtime, "_already_loaded", lambda name: False)
    bundle, host, legacy = tmp_path / "bundle", tmp_path / "host", tmp_path / "lib32"
    for directory in (bundle, host, legacy):
        directory.mkdir()
    (bundle / "libstdc++.so.6").write_bytes(_elf() + b"\x00GLIBCXX_3.4.29\x00GLIBCXX_3.4.30\x00")
    (bundle / "libgcc_s.so.1").write_bytes(_elf() + b"\x00GCC_3.0\x00GCC_12.0.0\x00")
    (host / "libgcc_s.so.1").write_bytes(_elf() + b"\x00GCC_3.0\x00GCC_14.0.0\x00")
    (host / "libstdc++.so.6").write_bytes(_elf() + b"\x00GLIBCXX_3.4.34\x00")
    # A 32-bit copy earlier in the search order is passed over, not a reason to give up.
    (legacy / "libstdc++.so.6").write_bytes(
        _elf(machine=3, elf_class=1) + b"\x00GLIBCXX_3.4.36\x00"
    )
    directories = (str(legacy), str(host))
    loaded = []
    assert prefer_newer_host_runtime(bundle, directories, loaded.append) == [
        str(host / "libgcc_s.so.1"),
        str(host / "libstdc++.so.6"),
    ]
    assert loaded == [str(host / "libgcc_s.so.1"), str(host / "libstdc++.so.6")]

    # A newer host libstdc++ brings the host's libgcc_s along even at the same version.
    (host / "libstdc++.so.6").write_bytes(_elf() + b"\x00GLIBCXX_3.4.34\x00")
    (host / "libgcc_s.so.1").write_bytes(_elf() + b"\x00GCC_3.0\x00GCC_12.0.0\x00")
    loaded.clear()
    assert prefer_newer_host_runtime(bundle, directories, loaded.append) == [
        str(host / "libgcc_s.so.1"),
        str(host / "libstdc++.so.6"),
    ]

    # An older or equal host runtime leaves the bundled one in place.
    (host / "libstdc++.so.6").write_bytes(_elf() + b"\x00GLIBCXX_3.4.28\x00")
    (host / "libgcc_s.so.1").write_bytes(_elf() + b"\x00GCC_12.0.0\x00")
    loaded.clear()
    assert prefer_newer_host_runtime(bundle, directories, loaded.append) == []
    assert loaded == []


def test_the_entrypoint_settles_the_cpp_runtime_before_importing_the_engine():
    source = (ROOT / "packaging" / "worker_entrypoint.py").read_text()
    assert source.index("prefer_newer_host_runtime()") < source.index("localsr.worker.cli")
