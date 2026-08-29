from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import struct
import sys
import tarfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(path: Path):
    module_name = "_ci_test_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


verify = load_script(ROOT / "tests" / "verify_artifacts.py")
preflight = load_script(ROOT / "scripts" / "storage_preflight.py")
cross_wheels = load_script(ROOT / "scripts" / "macos_cross_wheels.py")
artifact_server = load_script(ROOT / "scripts" / "ci_artifact_server.py")
artifact_auth = load_script(ROOT / "scripts" / "artifact_auth.py")
artifact_upload = load_script(ROOT / "scripts" / "upload_artifacts.py")
maintenance = load_script(ROOT / "scripts" / "ci_artifact_maintenance.py")
scratch = None if sys.platform == "win32" else load_script(ROOT / "scripts" / "ci_scratch.py")
macho_tree = load_script(ROOT / "scripts" / "verify_macho_tree.py")
frozen_smoke = load_script(ROOT / "scripts" / "smoke_frozen_worker.py")
release_version = load_script(ROOT / "scripts" / "check_release_version.py")
beta_readiness = load_script(ROOT / "scripts" / "check_beta_readiness.py")


def thin_macho(cpu: int) -> bytes:
    return b"\xcf\xfa\xed\xfe" + struct.pack("<I", cpu) + bytes(64)


def elf(machine: int) -> bytes:
    payload = bytearray(64)
    payload[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", payload, 18, machine)
    return bytes(payload)


def test_native_header_parsers():
    assert verify.macho_arches(thin_macho(0x0100000C)) == {"arm64"}
    fat = (
        b"\xca\xfe\xba\xbe"
        + struct.pack(">I", 2)
        + struct.pack(">IIIII", 0x01000007, 0, 0, 0, 0)
        + struct.pack(">IIIII", 0x0100000C, 0, 0, 0, 0)
    )
    assert verify.macho_arches(fat) == {"x86_64", "arm64"}
    assert verify.elf_arch(elf(62)) == "x86_64"
    assert verify.elf_arch(elf(224)) == "amdgpu"
    pe = bytearray(256)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 128)
    pe[128:132] = b"PE\0\0"
    struct.pack_into("<H", pe, 132, 0xAA64)
    assert verify.pe_arch(bytes(pe)) == "arm64"


def test_macho_tree_excludes_build_tool_fixtures(tmp_path: Path):
    runtime = tmp_path / "runtime"
    fixture = tmp_path / "delocate" / "tests" / "data"
    runtime.mkdir()
    fixture.mkdir(parents=True)
    (runtime / "extension.so").write_bytes(thin_macho(0x0100000C))
    (fixture / "single-arch.dylib").write_bytes(thin_macho(0x01000007))

    report = macho_tree.inspect(
        tmp_path,
        {"arm64"},
        None,
        ("delocate/tests/data/*",),
    )

    assert report["result"] == "PASS"
    assert report["native_binary_count"] == 1
    assert report["excluded_patterns"] == ["delocate/tests/data/*"]


def test_stream_verifies_arm64_mps_artifact(tmp_path: Path):
    artifact = tmp_path / "LocalSR-macOS-arm64.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        for name in (
            "LocalSR.app/Contents/MacOS/LocalSR",
            "LocalSR.app/Contents/Frameworks/torch/lib/libtorch_cpu.dylib",
        ):
            payload = thin_macho(0x0100000C)
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    artifact.with_name(artifact.name + ".sha256").write_text(
        f"{digest}  {artifact.name}\n", encoding="utf-8"
    )
    metadata = {
        "artifact_filename": artifact.name,
        "platform": "macos",
        "architecture": "arm64",
        "backend": "MPS",
        "sha256": digest,
        "repository_commit": "a" * 40,
        "github_run_id": "1",
        "run_attempt": "1",
        "timestamp": "2026-08-23T00:00:00+00:00",
        "signing": {
            "status": "ad-hoc",
            "developer_id": False,
            "notarized": False,
            "gatekeeper_accepted": False,
        },
        "mps": {
            "torch_arm64_wheel": "torch-2.2.2-cp311-none-macosx_11_0_arm64.whl",
            "torch_arm64_sha256": "b" * 64,
        },
    }
    artifact.with_name(artifact.name + ".metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    spec = verify.ArtifactSpec(
        artifact.name,
        "macos",
        "arm64",
        "MPS",
        "LocalSR.app/Contents/MacOS/LocalSR",
        True,
    )
    report, verified_digest = verify.verify_artifact(artifact, spec)
    assert "Result: PASS" in report
    assert "Native binaries inspected: 2" in report
    assert verified_digest == digest


def test_release_verifier_rejects_duplicate_archive_digests():
    with pytest.raises(ValueError, match="distinct SHA-256"):
        verify.verify_unique_digests(
            [
                ("LocalSR-Linux-CPU-x86_64.tar.gz", "a" * 64),
                ("LocalSR-Linux-CUDA-x86_64.tar.gz", "a" * 64),
            ]
        )


def test_release_metadata_is_synchronized():
    assert release_version.check("v0.0.9-alpha", ROOT) == "0.0.9-alpha"


def test_beta_readiness_register_is_valid_and_honest():
    data = beta_readiness.validate(ROOT / "ci" / "beta-readiness.json", ROOT)
    assert data["release"] == "0.0.9-alpha"
    assert data["beta_ready"] is False
    statuses = {gate["id"]: gate["status"] for gate in data["gates"]}
    assert statuses["automated-release-integrity"] == "automated-pass"
    assert statuses["macos-production-trust"] == "waiting-credentials"
    assert statuses["legacy-runtime-support"] == "decision-required"
    assert statuses["security-monitoring"] == "decision-required"
    assert statuses["video-labs"] == "labs"


@pytest.mark.skipif(sys.platform == "win32", reason="SSD scratch management uses POSIX locks")
def test_cache_trim_tolerates_concurrent_pip_rename(tmp_path: Path, monkeypatch):
    assert scratch is not None
    root = tmp_path / "scratch"
    cache = root / "cache"
    cache.mkdir(parents=True)
    (root / scratch.ROOT_MARKER).write_text("{}", encoding="utf-8")
    vanishing = cache / "entry.body"
    vanishing.write_bytes(b"cache")
    temporary = cache / "active.tmp"
    temporary.write_bytes(b"in progress")
    original_stat = Path.stat
    calls = 0

    def racing_stat(path, *args, **kwargs):
        nonlocal calls
        if path == vanishing:
            calls += 1
            if calls == 2:
                vanishing.unlink()
                raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", racing_stat)
    assert scratch.trim_cache(root, 0, dry_run=False) == 0
    assert temporary.read_bytes() == b"in progress"


def test_macos_signing_secrets_are_not_job_scoped():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    macos_job = workflow.split("  build-macos-flavors:", 1)[1].split("  verify-artifacts:", 1)[0]
    job_configuration = macos_job.split("    steps:", 1)[0]
    signing_step = macos_job.split("      - name: Sign, notarize, and archive the verified app", 1)[
        1
    ].split("      - name:", 1)[0]
    secret_names = (
        "MACOS_CERTIFICATE_P12_BASE64",
        "MACOS_CERTIFICATE_PASSWORD",
        "MACOS_SIGNING_IDENTITY",
        "MACOS_NOTARY_APPLE_ID",
        "MACOS_NOTARY_PASSWORD",
        "MACOS_TEAM_ID",
    )
    assert "secrets." not in job_configuration
    for name in secret_names:
        assert f"{name}: ${{{{ secrets.{name} }}}}" in signing_step


def test_arm_verifier_rejects_x86_member(tmp_path: Path):
    artifact = tmp_path / "bad.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        for name, cpu in (("LocalSR/LocalSR", 0x0100000C), ("LocalSR/bad.dylib", 0x01000007)):
            payload = thin_macho(cpu)
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    spec = verify.ArtifactSpec(artifact.name, "macos", "arm64", "MPS", "LocalSR/LocalSR")
    _count, errors = verify.verify_archive(artifact, spec)
    assert any("bad.dylib" in error and "x86_64" in error for error in errors)


def test_linux_verifier_distinguishes_rocm_device_code_from_host_libraries(tmp_path: Path):
    artifact = tmp_path / "rocm.tar.gz"
    members = {
        "LocalSR/LocalSR": elf(62),
        "LocalSR/_internal/torch/lib/rocblas/library/kernel.hsaco": elf(224),
        "LocalSR/_internal/torch/lib/hipblaslt/library/extop_gfx1100.co": elf(224),
    }
    with tarfile.open(artifact, "w:gz") as archive:
        for name, payload in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    spec = verify.ArtifactSpec(artifact.name, "linux", "x86_64", "AMD-ROCm", "LocalSR/LocalSR")
    count, errors = verify.verify_archive(artifact, spec)
    assert count == 1
    assert errors == []

    members["LocalSR/_internal/bad.so"] = elf(224)
    with tarfile.open(artifact, "w:gz") as archive:
        for name, payload in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    _count, errors = verify.verify_archive(artifact, spec)
    assert any("bad.so" in error and "amdgpu" in error for error in errors)


def test_storage_preflight_includes_peak_and_reserve(monkeypatch: pytest.MonkeyPatch):
    shutil_usage = (1000, 400, 600)
    monkeypatch.setattr(preflight.shutil, "disk_usage", lambda _path: shutil_usage)
    item = preflight.status_for("SSD", Path("."), 25, 200, 300)
    assert item.reserve_bytes == 250
    assert item.required_free_bytes == 550
    assert item.passed
    monkeypatch.setattr(
        preflight.shutil,
        "disk_usage",
        lambda _path: (1000, 500, 500),
    )
    assert not preflight.status_for("SSD", Path("."), 25, 200, 300).passed


def test_cross_wheel_pair_is_merged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    x86 = tmp_path / "x86"
    arm = tmp_path / "arm"
    output = tmp_path / "out"
    x86.mkdir()
    arm.mkdir()
    (x86 / "demo-1.0-cp311-cp311-macosx_12_0_x86_64.whl").write_bytes(b"x86")
    (arm / "demo-1.0-cp311-cp311-macosx_12_0_arm64.whl").write_bytes(b"arm")

    def fake_run(command, check):
        assert check
        destination = Path(command[command.index("-w") + 1])
        merged = destination / ("demo-1.0-cp311-cp311-macosx_10_13_x86_64.macosx_11_0_arm64.whl")
        merged.write_bytes(b"merged")

    monkeypatch.setattr(cross_wheels.subprocess, "run", fake_run)
    records = cross_wheels.merge_wheel_sets(x86, arm, output)
    assert records[0].operation == "delocate-merge"
    assert cross_wheels.is_dual_arch_wheel(Path(records[0].output))


def test_torch_openmp_aliases_become_dual_arch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    site_packages = tmp_path / "site-packages"
    paths = (
        site_packages / "torch/lib/libiomp5.dylib",
        site_packages / "functorch/.dylibs/libiomp5.dylib",
        site_packages / "functorch/.dylibs/libomp.dylib",
    )
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"arm" if path.name == "libomp.dylib" else b"x86")

    def fake_arches(path: Path, _lipo: str = "lipo") -> set[str]:
        payload = path.read_bytes()
        if payload == b"fat":
            return {"x86_64", "arm64"}
        return {"arm64"} if payload == b"arm" else {"x86_64"}

    def fake_fat(_x86: Path, _arm: Path, destination: Path, **_kwargs) -> None:
        destination.write_bytes(b"fat")

    monkeypatch.setattr(cross_wheels, "lipo_arches", fake_arches)
    monkeypatch.setattr(cross_wheels, "make_fat_binary", fake_fat)
    report = cross_wheels.normalize_torch_openmp(site_packages)
    assert len(report["outputs"]) == 3
    assert all(path.read_bytes() == b"fat" for path in paths)


def test_artifact_path_components_and_managed_root(tmp_path: Path):
    assert artifact_server.safe_component("LocalSR-macOS-arm64.tar.gz", "name")
    with pytest.raises(ValueError):
        artifact_server.safe_component("../../data.img", "name")
    root = tmp_path / "ci-artifacts"
    root.mkdir()
    assert maintenance.managed_root(root) == root.resolve()
    with pytest.raises(ValueError):
        maintenance.managed_root(tmp_path)


def test_artifact_hmac_covers_metadata_and_rejects_replay():
    secret = b"s" * 32
    timestamp = "1787976000"
    nonce = "a" * 32
    headers = {
        "Content-Length": "7",
        "X-Run-Id": "123",
        "X-Run-Attempt": "1",
        "X-Platform": "linux",
        "X-Artifact-Name": "artifact.zip",
        "X-Content-SHA256": "b" * 64,
    }
    signature = artifact_auth.sign_request(
        secret, "POST", "/v1/artifacts", timestamp, nonce, headers
    )
    authorization = f"{artifact_auth.AUTH_SCHEME} {signature}"
    assert artifact_auth.verify_request(
        secret,
        authorization,
        "POST",
        "/v1/artifacts",
        timestamp,
        nonce,
        headers,
        current_time=int(timestamp),
    )

    altered = dict(headers)
    altered["X-Artifact-Name"] = "other.zip"
    assert not artifact_auth.verify_request(
        secret,
        authorization,
        "POST",
        "/v1/artifacts",
        timestamp,
        nonce,
        altered,
        current_time=int(timestamp),
    )
    cache = artifact_auth.NonceCache()
    assert cache.claim(nonce, current_time=int(timestamp))
    assert not cache.claim(nonce, current_time=int(timestamp))


def test_artifact_upload_and_receiver_use_hmac_without_bearer(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    server = artifact_server.ArtifactServer(("127.0.0.1", 0), artifact_server.Handler)
    server.root = root
    server.token = b"s" * 32
    server.min_free_gib = 0
    server.min_free_percent = 0
    server.nonces = artifact_auth.NonceCache()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = tmp_path / "LocalSR-test.zip"
    payload.write_bytes(b"verified artifact")
    try:
        result = artifact_upload.upload(
            f"http://127.0.0.1:{server.server_port}",
            "s" * 32,
            "123",
            "1",
            "linux",
            payload,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result["status"] == "stored"
    assert (root / "123" / "1" / "linux" / payload.name).read_bytes() == payload.read_bytes()
    assert "Bearer " not in (ROOT / "scripts" / "upload_artifacts.py").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="Test fixture uses a POSIX executable script")
def test_frozen_worker_smoke_protocol(tmp_path: Path):
    bundle = tmp_path / "LocalSR"
    bundle.mkdir()
    worker = bundle / "LocalSRWorker"
    worker.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "print(json.dumps({'type': 'worker_ready', 'data': {}}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if message['type'] == 'capabilities_request':\n"
        "        print(json.dumps({'type': 'capabilities_info', 'data': "
        "{'devices': [{'id': 'cpu', 'type': 'cpu'}]}}), flush=True)\n"
        "    elif message['type'] == 'shutdown_request':\n"
        "        break\n",
        encoding="utf-8",
    )
    worker.chmod(0o755)

    report = frozen_smoke.smoke(bundle, timeout=10)

    assert report["result"] == "PASS"
    assert report["worker_ready"] is True
    assert report["capabilities"]["devices"][0]["type"] == "cpu"
