from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_package = load_script("verify_tauri_release_package")
verify_macos_signing = load_script("verify_macos_tauri_signing")
prepare_release = load_script("prepare_tauri_release_assets")
smoke_installer = load_script("smoke_tauri_installer")


def write_pe(path: Path, machine: int) -> None:
    pe = bytearray(256)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 128)
    pe[128:132] = b"PE\0\0"
    struct.pack_into("<H", pe, 132, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pe)


def installed_smoke(version: str) -> dict[str, object]:
    return {
        "passed": True,
        "mode": "headless-installed-host",
        "version": version,
        "worker": "ready",
        "worker_path": "/installed/localsr-worker",
    }


def architecture_evidence(filename: str, architecture: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact": filename,
        "required_architectures": [architecture],
        "native_binary_count": 1,
        "mismatches": [],
        "result": "PASS",
    }


def test_release_policy_and_signing_accept_only_production_requirements() -> None:
    assert prepare_release.release_policy({}) == prepare_release.SIGNED_POLICY
    with pytest.raises(ValueError, match="unsupported release policy"):
        prepare_release.release_policy({"release_policy": "v0.0.12-cross-alpha-exception"})
    unsigned = {"status": "ad-hoc-alpha", "production_signed": False, "warning": "testing"}
    with pytest.raises(ValueError, match="unsupported signing requirement"):
        prepare_release.validate_signing_evidence(unsigned, "ad-hoc-alpha")


def test_release_matrix_rejects_weak_smoke_and_architecture_evidence() -> None:
    with pytest.raises(ValueError, match="acceptable"):
        prepare_release.validate_smoke_evidence({"passed": True}, "0.0.12-alpha")
    with pytest.raises(ValueError, match="different artifact"):
        prepare_release.validate_architecture_evidence(
            "LocalSR.AppImage",
            architecture_evidence("other.AppImage", "x86_64"),
            "x86_64",
        )


def test_reads_x86_64_elf_and_pe_release_containers(tmp_path: Path) -> None:
    elf = bytearray(64)
    elf[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", elf, 18, 62)
    appimage = tmp_path / "LocalSR.AppImage"
    appimage.write_bytes(elf)

    installer = tmp_path / "LocalSR.exe"
    write_pe(installer, 0x8664)

    assert verify_package.read_elf_arch(appimage) == "x86_64"
    assert verify_package.read_pe_arch(installer) == "x86_64"


def test_verifies_x64_payload_inside_x86_nsis_bootstrap(tmp_path: Path) -> None:
    installer = tmp_path / "LocalSR-setup.exe"
    install_dir = tmp_path / "installed"
    host = install_dir / "localsr-next.exe"
    worker = install_dir / "engine" / "localsr-worker.exe"
    report = tmp_path / "package-smoke.json"
    write_pe(installer, 0x014C)
    write_pe(host, 0x8664)
    write_pe(worker, 0x8664)
    report.write_text(
        json.dumps({"passed": True, "worker": "ready", "worker_path": str(worker)}),
        encoding="utf-8",
    )

    smoke_installer.record_windows_payload_architectures(installer, install_dir, host, report)
    result = verify_package.verify(installer, "windows", "x86_64", report)

    assert result["result"] == "PASS"
    assert result["container_architecture"] == "x86"
    assert result["native_binary_count"] == 2
    assert {item["role"] for item in result["payloads"]} == {"host", "worker"}


def test_rejects_nsis_without_installed_payload_evidence(tmp_path: Path) -> None:
    installer = tmp_path / "LocalSR-setup.exe"
    report = tmp_path / "package-smoke.json"
    write_pe(installer, 0x014C)
    report.write_text(json.dumps({"passed": True, "worker": "ready"}), encoding="utf-8")

    with pytest.raises(ValueError, match="no NSIS payload architecture evidence"):
        verify_package.verify(installer, "windows", "x86_64", report)


def test_rejects_wrong_native_installer_architecture(tmp_path: Path) -> None:
    elf = bytearray(64)
    elf[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", elf, 18, 183)
    artifact = tmp_path / "wrong.AppImage"
    artifact.write_bytes(elf)

    with pytest.raises(ValueError, match="expected x86_64"):
        verify_package.verify(artifact, "linux", "x86_64")


def test_extracts_developer_id_and_team_from_codesign_output() -> None:
    identity, team = verify_macos_signing.identity_details(
        "Authority=Developer ID Application: Example Developer (TEAM123456)\n"
        "Authority=Developer ID Certification Authority\n"
        "TeamIdentifier=TEAM123456\n"
    )

    assert identity.startswith("Developer ID Application:")
    assert team == "TEAM123456"


def write_release_input(root: Path, attempt: int, platform: str, filename: str) -> Path:
    artifact = root / str(attempt) / platform / filename
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(f"attempt-{attempt}-{platform}".encode())
    for suffix in (".sha256", ".metadata.json", ".architecture.json"):
        artifact.with_name(artifact.name + suffix).write_text("evidence", encoding="utf-8")
    return artifact


def test_release_input_uses_previous_successful_jobs_during_publish_rerun(
    tmp_path: Path,
) -> None:
    filename = "LocalSR-v1-alpha-Linux-x86_64.AppImage"
    expected = write_release_input(tmp_path, 1, "linux", filename)
    (tmp_path / "2").mkdir()

    assert prepare_release.locate_release_input(tmp_path, filename) == expected


def test_release_input_combines_latest_complete_platform_artifacts(tmp_path: Path) -> None:
    filename = "LocalSR-v1-alpha-Windows-x86_64.exe"
    write_release_input(tmp_path, 1, "windows", filename)
    expected = write_release_input(tmp_path, 2, "windows", filename)
    incomplete = tmp_path / "3" / "windows" / filename
    incomplete.parent.mkdir(parents=True)
    incomplete.write_bytes(b"partial transfer")
    publish_copy = tmp_path / "3" / "tauri-release-assets" / filename
    publish_copy.parent.mkdir(parents=True)
    publish_copy.write_bytes(b"previous publish output")

    assert prepare_release.locate_release_input(tmp_path, filename) == expected


def test_release_input_rejects_duplicate_complete_uploads_in_latest_attempt(
    tmp_path: Path,
) -> None:
    filename = "LocalSR-v1-alpha-macOS-arm64.dmg"
    write_release_input(tmp_path, 4, "macos", filename)
    write_release_input(tmp_path, 4, "duplicate", filename)

    with pytest.raises(ValueError, match="exactly one complete"):
        prepare_release.locate_release_input(tmp_path, filename)


def release_fixture(tmp_path: Path):
    from release_targets import artifact_entries

    staging, output = tmp_path / "staging", tmp_path / "output"
    entries = artifact_entries("1-alpha")
    for number, entry in enumerate(entries):
        directory = staging / "1" / entry["platform"]
        directory.mkdir(parents=True, exist_ok=True)
        artifact = directory / entry["filename"]
        artifact.write_bytes(f"package-{number}".encode())
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(artifact.name + ".sha256").write_text(f"{digest}  {artifact.name}\n")
        signing = {"status": entry["signing"]}
        if entry["platform"] == "macos":
            signing.update(
                developer_id="Developer ID Application: Example",
                team_id="TEAM123456",
                notarized=True,
                stapled=True,
                gatekeeper_accepted=True,
            )
        if entry["platform"] == "windows":
            signing.update(signer_subject="CN=Example", signer_thumbprint="ABCD", timestamped=True)
        probe = {
            "backend": entry["backend"],
            "runtime_verified": True,
            "cpu_inference_verified": True,
            "hardware_tested": False,
        }
        if entry["backend"] == "Intel-XPU":
            probe.update(xpu_runtime_files_verified=True, xpu_runtime_library_count=5)
        smoke = installed_smoke("1-alpha") | {"backend_probe": probe}
        metadata = {
            "schema_version": 1,
            "artifact_filename": artifact.name,
            "artifact_size": artifact.stat().st_size,
            "sha256": digest,
            "platform": entry["platform"],
            "architecture": entry["architecture"],
            "backend": entry["backend"],
            "repository_commit": "c" * 40,
            "github_run_id": "1",
            "run_attempt": "1",
            "timestamp": "2026-09-05T12:00:00+00:00",
            "package_smoke": smoke,
            "backend_probe": probe,
            "signing": signing,
        }
        if entry["platform"] != "macos":
            lock = ROOT / "requirements/locks" / f"{entry['id']}.txt"
            metadata["dependency_wheelhouse"] = {
                "schema_version": 1,
                "target": entry["id"],
                "source_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "wheels": [{"name": "fixture", "sha256": "a" * 64}],
            }
        if entry["platform"] == "linux":
            metadata["live_models"] = {
                "result": "PASS",
                "models": [
                    {
                        "preset": preset,
                        "model_id": model_id,
                        "sha256": character * 64,
                        "download_bytes": 1024,
                        "output_shape": [1, 3, 64, 64],
                    }
                    for preset, model_id, character in (
                        ("Quick", "span_photo_x4", "a"),
                        ("Best", "realplksr_nomoswebphoto_x4", "b"),
                    )
                ],
            }
        if entry.get("external_engine"):
            part = directory / "cuda.engine.tar.gz.part-0001"
            part.write_bytes(b"frozen-engine-payload")
            payload = {
                "schema_version": 1,
                "format": "tar.gz.parts",
                "backend": "CUDA",
                "file_count": 1,
                "unpacked_bytes": 20,
                "parts": [
                    {
                        "filename": part.name,
                        "size": part.stat().st_size,
                        "sha256": hashlib.sha256(part.read_bytes()).hexdigest(),
                    }
                ],
            }
            payload_hash = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
            metadata.update(engine_payload=payload, engine_payload_sha256=payload_hash)
            smoke.update(engine_payload=payload, engine_payload_sha256=payload_hash)
        artifact.with_name(artifact.name + ".metadata.json").write_text(json.dumps(metadata))
        artifact.with_name(artifact.name + ".architecture.json").write_text(
            json.dumps(architecture_evidence(artifact.name, entry["architecture"]))
        )
    manifest, readiness = tmp_path / "manifest.json", tmp_path / "readiness.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "version": "1-alpha", "artifacts": entries})
    )
    readiness.write_text(json.dumps({"release": "1-alpha", "beta_ready": False, "gates": []}))
    return staging, manifest, output, readiness


def test_compacts_every_backend_and_verified_payload(tmp_path: Path) -> None:
    from verify_prepared_release import expected_assets, verify

    staging, manifest, output, readiness = release_fixture(tmp_path)
    index_path = prepare_release.prepare(staging, manifest, "v1-alpha", output, readiness)
    index = json.loads(index_path.read_text())
    assert len(index["installers"]) == len(index["source_matrix"]) == 7
    assert len(index["public_assets"]) == 8
    assert index["beta_ready"] is False
    assert len((output / "release-files.txt").read_text().splitlines()) == 10
    verify(output, manifest, "c" * 40)
    assert len(expected_assets(output)) == 10
    second = tmp_path / "rerun"
    prepare_release.prepare(staging, manifest, "v1-alpha", second, readiness)
    assert index_path.read_bytes() == (second / "release-index.json").read_bytes()


@pytest.mark.parametrize("failure", ["model", "backend", "payload", "wheelhouse", "missing-target"])
def test_rejects_incomplete_release_evidence(tmp_path: Path, failure: str) -> None:
    staging, manifest, output, readiness = release_fixture(tmp_path)
    match = {
        "model": "Quick/Best",
        "backend": "backend identity",
        "payload": "payload digest",
        "wheelhouse": "wheelhouse provenance",
        "missing-target": "every target",
    }[failure]
    if failure == "missing-target":
        data = json.loads(manifest.read_text())
        data["artifacts"].pop()
        manifest.write_text(json.dumps(data))
    elif failure == "payload":
        next(staging.rglob("*.part-0001")).write_bytes(b"corrupted")
    else:
        path = next(staging.rglob("*Linux-CPU*.metadata.json"))
        data = json.loads(path.read_text())
        if failure == "model":
            data.pop("live_models")
        elif failure == "wheelhouse":
            data["dependency_wheelhouse"]["source_lock_sha256"] = "f" * 64
        else:
            data["backend_probe"]["backend"] = "CUDA"
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=match):
        prepare_release.prepare(staging, manifest, "v1-alpha", output, readiness)


def test_release_matrix_rejects_cross_attempt_commit_mixing(tmp_path: Path) -> None:
    staging, manifest, output, readiness = release_fixture(tmp_path)
    path = next(staging.rglob("*Windows-CPU*.metadata.json"))
    data = json.loads(path.read_text())
    data["repository_commit"] = "b" * 40
    data["run_attempt"] = "2"
    path.write_text(json.dumps(data))
    destination = staging / "2" / "windows"
    destination.mkdir(parents=True)
    for source in path.parent.glob("*Windows-CPU*"):
        source.rename(destination / source.name)
    with pytest.raises(ValueError, match="mixes commits"):
        prepare_release.prepare(staging, manifest, "v1-alpha", output, readiness)


def test_release_matrix_accepts_crlf_and_lf_wheelhouse_locks(tmp_path: Path) -> None:
    staging, manifest, output, readiness = release_fixture(tmp_path)
    path = next(staging.rglob("*Windows-CUDA*.metadata.json"))
    data = json.loads(path.read_text())
    from release_targets import ROOT

    lock = ROOT / "requirements/locks/windows-x86_64-cuda.txt"
    normalized = lock.read_bytes().replace(b"\r\n", b"\n")
    crlf_hash = hashlib.sha256(normalized.replace(b"\n", b"\r\n")).hexdigest()
    data["dependency_wheelhouse"]["source_lock_sha256"] = crlf_hash
    path.write_text(json.dumps(data))
    index_path = prepare_release.prepare(staging, manifest, "v1-alpha", output, readiness)
    assert index_path.is_file()


# In-app updates on every edition: build identity, updater files and feed entries.

update_signing = load_script("beta_update_signing")
engine_payload = load_script("prepare_engine_payload")
release_targets = load_script("release_targets")
verify_prepared = load_script("verify_prepared_release")
COMMIT = "0123456789abcdef0123456789abcdef01234567"
UPDATE_TARGETS = {
    "macos-arm64-mps": "darwin-aarch64-mps-native",
    "windows-x86_64-cuda": "windows-x86_64-cuda-native",
    "windows-x86_64-cpu": "windows-x86_64-cpu-native",
    "windows-x86_64-directml": "windows-x86_64-directml-native",
    "linux-x86_64-cpu": "linux-x86_64-cpu-native",
    "linux-x86_64-cuda": "linux-x86_64-cuda-native",
    "linux-x86_64-rocm": "linux-x86_64-rocm-native",
}


def test_every_edition_has_its_own_update_platform_key() -> None:
    # The key is what UpdateConfig::target() builds: <os>-<arch>-<backend>-<kind>.
    assert {
        target["id"]: release_targets.platform_key(target) for target in release_targets.targets()
    } == UPDATE_TARGETS
    assert [entry["update_target"] for entry in release_targets.artifact_entries("1-beta")] == list(
        UPDATE_TARGETS.values()
    )


def test_registry_rejects_a_missing_or_duplicate_update_identity(
    tmp_path: Path, monkeypatch
) -> None:
    registry = json.loads(release_targets.REGISTRY.read_text())
    path = tmp_path / "targets.json"
    monkeypatch.setattr(release_targets, "REGISTRY", path)
    for change, match in (
        (lambda targets: targets[1].pop("update"), "update identity"),
        (lambda targets: targets[1]["update"].update(kind="installer"), "update identity"),
        (lambda targets: targets[1]["update"].update(backend="CUDA"), "update identity"),
        (lambda targets: targets[2]["update"].update(backend="cuda"), "platform key"),
    ):
        broken = json.loads(json.dumps(registry))
        change(broken["targets"])
        path.write_text(json.dumps(broken))
        with pytest.raises(ValueError, match=match):
            release_targets.targets()


def test_ci_build_identity_uses_the_production_key_and_a_content_engine_id() -> None:
    environment = release_targets.update_environment("windows-x86_64-cuda", COMMIT)
    assert environment == {
        "LOCALSR_UPDATE_PUBLIC_KEY": (ROOT / "packaging/updates/production.pub")
        .read_text()
        .strip(),
        "LOCALSR_UPDATE_BACKEND": "cuda",
        "LOCALSR_UPDATE_KIND": "native",
        "LOCALSR_ENGINE_ID": release_targets.engine_id(
            release_targets.target("windows-x86_64-cuda"), COMMIT
        ),
    }
    with pytest.raises(ValueError, match="commit"):
        release_targets.update_environment("linux-x86_64-rocm", "HEAD")
    with pytest.raises(ValueError, match="unknown release target"):
        release_targets.update_environment("linux-x86_64-xpu", COMMIT)


def test_update_env_cli_writes_github_env_lines() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/release_targets.py"),
            "--update-env",
            "linux-x86_64-rocm",
            "--commit",
            COMMIT,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines[1:] == [
        "LOCALSR_UPDATE_BACKEND=rocm",
        "LOCALSR_UPDATE_KIND=native",
        "LOCALSR_ENGINE_ID="
        + release_targets.engine_id(release_targets.target("linux-x86_64-rocm"), COMMIT),
    ]
    assert lines[0].startswith("LOCALSR_UPDATE_PUBLIC_KEY=") and "\n" not in lines[0]


def test_mac_release_script_agrees_with_the_registry_identity() -> None:
    script = (ROOT / "scripts/release_macos_beta.sh").read_text()
    mac = release_targets.target("macos-arm64-mps")
    assert f"LOCALSR_UPDATE_BACKEND={mac['update']['backend']}" in script
    assert f"LOCALSR_UPDATE_KIND={mac['update']['kind']}" in script
    assert "--update-env macos-arm64-mps" in script
    assert f'"{release_targets.platform_key(mac)}"' in script
    files = release_targets.update_files(mac, "1.2.3-beta")
    assert files["update_package"] == "LocalSR-v1.2.3-beta-macOS-arm64.app.tar.gz"
    assert files["engine_manifest"] == "LocalSR-v1.2.3-beta-macOS-arm64.engine-payload.json"


def test_updater_file_names_follow_the_published_package_names() -> None:
    cuda = release_targets.update_files(release_targets.target("windows-x86_64-cuda"), "1.2.3-beta")
    # The NSIS hook's adjacent parts and the updater's engine package are the same files.
    assert cuda == {
        "package": "LocalSR-v1.2.3-beta-Windows-CUDA-x86_64.exe",
        "update_package": "LocalSR-v1.2.3-beta-Windows-CUDA-x86_64.exe",
        "engine_prefix": "LocalSR-v1.2.3-beta-Windows-CUDA-x86_64.engine.tar.gz",
        "engine_manifest": "LocalSR-v1.2.3-beta-Windows-CUDA-x86_64.engine-payload.json",
        "payload_backend": "CUDA",
        "worker": "localsr-worker.exe",
    }
    rocm = release_targets.update_files(release_targets.target("linux-x86_64-rocm"), "1.2.3-beta")
    assert rocm["update_package"] == "LocalSR-v1.2.3-beta-Linux-ROCm-x86_64.AppImage"
    assert rocm["payload_backend"] == "ROCM"
    assert rocm["worker"] == "localsr-worker"
    directml = release_targets.update_files(
        release_targets.target("windows-x86_64-directml"), "1.2.3-beta"
    )
    assert directml["payload_backend"] == "DIRECTML"


def fake_engine(root: Path, worker: str) -> Path:
    engine = root / "engine"
    (engine / "lib").mkdir(parents=True)
    (engine / worker).write_bytes(b"worker")
    (engine / "lib" / "model.bin").write_bytes(bytes(range(256)) * 8)
    return engine


def test_engine_payload_cli_names_the_payload_for_its_edition(tmp_path: Path) -> None:
    engine = fake_engine(tmp_path, "localsr-worker.exe")
    output = tmp_path / "out"
    output.mkdir()
    manifest = engine_payload.prepare_for_target(
        "windows-x86_64-directml", output, engine=engine, release_version="1.2.3-beta"
    )
    assert manifest.name == "LocalSR-v1.2.3-beta-Windows-DirectML-x86_64.engine-payload.json"
    data = json.loads(manifest.read_text())
    assert data["backend"] == "DIRECTML"
    assert [part["filename"] for part in data["parts"]] == [
        "LocalSR-v1.2.3-beta-Windows-DirectML-x86_64.engine.tar.gz.part-0001"
    ]
    assert not (output / "engine-payload.json").exists()


def test_engine_payload_cli_reuses_the_cuda_installer_payload_only_when_it_matches(
    tmp_path: Path,
) -> None:
    built = tmp_path / "built"
    prefix = "LocalSR-v1.2.3-beta-Windows-CUDA-x86_64.engine.tar.gz"
    source = engine_payload.prepare(fake_engine(tmp_path, "localsr-worker.exe"), built, prefix)
    output = tmp_path / "out"
    output.mkdir()
    for part in built.glob("*.part-*"):
        (output / part.name).write_bytes(part.read_bytes())
    manifest = engine_payload.prepare_for_target(
        "windows-x86_64-cuda", output, reuse=source, release_version="1.2.3-beta"
    )
    assert manifest.read_bytes() == source.read_bytes()
    next(output.glob("*.part-0001")).write_bytes(b"truncated")
    with pytest.raises(ValueError, match="does not match"):
        engine_payload.prepare_for_target(
            "windows-x86_64-cuda", output, reuse=source, release_version="1.2.3-beta"
        )


def test_engine_payload_backends_cover_every_edition() -> None:
    for target in release_targets.targets():
        backend = release_targets.update_files(target, "1-beta")["payload_backend"]
        assert backend in engine_payload.BACKENDS
    rust = (ROOT / "desktop/src-tauri/src/engine_payload.rs").read_text()
    for backend in engine_payload.BACKENDS:
        assert f'"{backend}"' in rust


def update_smoke(target_id: str, release_version: str) -> dict[str, object]:
    target = release_targets.target(target_id)
    return installed_smoke(release_version) | {
        "update": release_targets.expected_update_identity(target, COMMIT),
        "commit": COMMIT,
    }


def test_installed_smoke_must_carry_the_build_update_identity() -> None:
    report = update_smoke("linux-x86_64-cuda", "1-beta")
    smoke_installer.check_update_identity(report, "linux-x86_64-cuda", COMMIT)
    assert report["update"] == {
        "enabled": True,
        "target": "linux-x86_64-cuda-native",
        "backend": "cuda",
        "kind": "native",
        "engine_id": release_targets.engine_id(release_targets.target("linux-x86_64-cuda"), COMMIT),
    }
    for change in (
        {"enabled": False},
        {"target": "linux-x86_64-cpu-native"},
        {"engine_id": "linux-x86_64-cuda-ffffffffffff"},
    ):
        broken = report | {"update": report["update"] | change}
        with pytest.raises(RuntimeError, match="update identity"):
            smoke_installer.check_update_identity(broken, "linux-x86_64-cuda", COMMIT)
    with pytest.raises(RuntimeError, match="update identity"):
        smoke_installer.check_update_identity(
            installed_smoke("1-beta"), "linux-x86_64-cuda", COMMIT
        )


def _compress(point: tuple[int, int, int, int]) -> bytes:
    prime = verify_prepared._P
    inverse = pow(point[2], prime - 2, prime)
    x, y = point[0] * inverse % prime, point[1] * inverse % prime
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


class SigningKey:
    """A disposable minisign key that signs the way tauri signer does (prehashed)."""

    def __init__(self, seed: bytes, key_id: bytes):
        digest = hashlib.sha512(seed).digest()
        scalar = int.from_bytes(digest[:32], "little")
        self.scalar = (scalar & ((1 << 254) - 8)) | (1 << 254)
        self.prefix = digest[32:]
        self.key_id = key_id
        self.point = _compress(verify_prepared._mul(self.scalar, verify_prepared._G))
        text = (
            f"untrusted comment: minisign public key: {key_id.hex().upper()}\n"
            + base64.b64encode(b"Ed" + key_id + self.point).decode()
            + "\n"
        )
        self.public_key = base64.b64encode(text.encode()).decode()

    def _raw(self, message: bytes) -> bytes:
        order = verify_prepared._Q
        r = int.from_bytes(hashlib.sha512(self.prefix + message).digest(), "little") % order
        commitment = _compress(verify_prepared._mul(r, verify_prepared._G))
        h = (
            int.from_bytes(hashlib.sha512(commitment + self.point + message).digest(), "little")
            % order
        )
        return commitment + ((r + h * self.scalar) % order).to_bytes(32, "little")

    def signature(self, data: bytes, name: str = "file") -> str:
        signature = self._raw(hashlib.blake2b(data, digest_size=64).digest())
        trusted = f"timestamp:0\tfile:{name}"
        text = (
            "untrusted comment: signature from tauri secret key\n"
            + base64.b64encode(b"ED" + self.key_id + signature).decode()
            + f"\ntrusted comment: {trusted}\n"
            + base64.b64encode(self._raw(signature + trusted.encode())).decode()
            + "\n"
        )
        return base64.b64encode(text.encode()).decode()

    def sign_file(self, path: Path) -> None:
        path.with_name(path.name + ".sig").write_text(self.signature(path.read_bytes(), path.name))


TEST_KEY = SigningKey(b"\x01" * 32, b"localsr1")
OTHER_KEY = SigningKey(b"\x02" * 32, b"localsr1")


def fake_sign(path: Path) -> None:
    """Stand-in for the Keychain signer: a disposable key, the same signature format."""
    TEST_KEY.sign_file(path)


def test_the_feed_check_verifies_signatures_as_the_app_does() -> None:
    # Real tauri signer output from the app's test fixtures.
    fixtures = ROOT / "desktop/src-tauri/tests/fixtures"
    single = json.loads((fixtures / "update-signature.json").read_text())
    data = single["data"].encode()
    assert verify_prepared.minisign_verify(single["public_key"], single["signature"], [data])
    assert not verify_prepared.minisign_verify(
        single["public_key"], single["signature"], [data + b"x"]
    )
    split = json.loads((fixtures / "update-parts.json").read_text())
    parts = [part["data"].encode() for part in split["parts"]]
    assert verify_prepared.minisign_verify(split["public_key"], split["whole"]["signature"], parts)
    assert not verify_prepared.minisign_verify(
        single["public_key"], split["whole"]["signature"], parts
    )
    # The test signer writes the same format.
    assert verify_prepared.minisign_verify(TEST_KEY.public_key, TEST_KEY.signature(data), [data])
    assert not verify_prepared.minisign_verify(
        TEST_KEY.public_key, OTHER_KEY.signature(data), [data]
    )


def platform_downloads(tmp_path: Path, release_version: str = "1.2.3-beta") -> Path:
    """What `gh run download` leaves for the Windows and Linux runs: one folder per edition."""
    downloads = tmp_path / "downloads"
    for target in release_targets.targets():
        if target["platform"] == "macos":
            continue
        directory = downloads / target["id"]
        directory.mkdir(parents=True)
        files = release_targets.update_files(target, release_version)
        name = files["package"]
        data = f"package {target['id']} ".encode() * 64
        if target["id"] in {"linux-x86_64-cuda", "linux-x86_64-rocm"}:
            # Larger than GitHub's limit in reality: CI keeps only 1,900 MiB parts.
            for index, start in enumerate(range(0, len(data), 1000), start=1):
                (directory / f"{name}.part-{index:04d}").write_bytes(data[start : start + 1000])
        else:
            (directory / name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        (directory / f"{name}.sha256").write_text(f"{digest}  {name}\n")
        engine = fake_engine(tmp_path / target["id"], files["worker"])
        manifest = engine_payload.prepare_for_target(
            target["id"], directory, engine=engine, release_version=release_version
        )
        smoke = update_smoke(target["id"], release_version)
        if target.get("external_engine"):
            # The Windows CUDA smoke test installs from this manifest and records it.
            smoke["engine_payload_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        (directory / f"{name}.smoke.json").write_text(json.dumps(smoke))
    return downloads


def mac_feed(tmp_path: Path, release_version: str = "1.2.3-beta") -> tuple[Path, dict]:
    """The files and the feed release_macos_beta.sh writes, with its signed statement."""
    mac = tmp_path / "mac"
    mac.mkdir()
    base = f"https://github.com/HerRei/local-upscale/releases/download/v{release_version}"
    stem = f"LocalSR-v{release_version}-macOS-arm64"
    archive = mac / f"{stem}.app.tar.gz"
    archive.write_bytes(b"mac app archive" * 32)
    written = engine_payload.prepare(
        fake_engine(tmp_path / "mac-engine", "localsr-worker"),
        mac,
        f"{stem}.engine.tar.gz",
        backend="MPS",
        worker="localsr-worker",
    )
    manifest = written.rename(mac / f"{stem}.engine-payload.json")
    for path in mac.iterdir():
        fake_sign(path)

    def file(path: Path) -> dict:
        return {
            "name": path.name,
            "url": f"{base}/{path.name}",
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "signature": path.with_name(path.name + ".sig").read_text(),
        }

    identity = release_targets.engine_id(release_targets.target("macos-arm64-mps"), COMMIT)
    payload = json.loads(manifest.read_text())
    feed = {
        "version": release_version,
        "pub_date": "2026-11-09T12:00:00Z",
        "notes": "Notes.",
        "platforms": {
            "darwin-aarch64-mps-native": {
                "url": f"{base}/{archive.name}",
                "signature": archive.with_name(archive.name + ".sig").read_text(),
                "localsr": {
                    "channel": "beta",
                    "backend": "mps",
                    "kind": "native",
                    "protocol": 1,
                    "engine_id": identity,
                    "size": archive.stat().st_size,
                    "unpacked_size": 10 * archive.stat().st_size,
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "engine": {
                        "id": identity,
                        "manifest": file(manifest),
                        "parts": [file(mac / part["filename"]) for part in payload["parts"]],
                    },
                },
            }
        },
    }
    feed_path = tmp_path / "beta.json"
    feed_path.write_text(json.dumps(feed, indent=2) + "\n")
    assert update_signing.add_statements(feed_path, sign=fake_sign) == ["darwin-aarch64-mps-native"]
    assert update_signing.add_statements(feed_path, sign=fake_sign) == []
    return mac, json.loads(feed_path.read_text())


def verify_feed(feed_path: Path, folders: list[Path], **options) -> None:
    verify_prepared.verify_feed(
        feed_path,
        COMMIT,
        files=folders,
        release_version="1.2.3-beta",
        public_key=TEST_KEY.public_key,
        **options,
    )


def test_platform_tool_signs_ci_artifacts_and_adds_their_feed_entries(tmp_path: Path) -> None:
    downloads = platform_downloads(tmp_path)
    feed_path = tmp_path / "beta.json"
    mac, original = mac_feed(tmp_path)
    signed: dict[str, str] = {}

    def recording(path: Path) -> None:
        # Only private copies are signed, never the downloaded files themselves.
        assert downloads not in path.parents
        signed[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        fake_sign(path)

    signatures = update_signing.add_platforms(
        downloads, feed_path, COMMIT, release_version="1.2.3-beta", sign=recording
    )
    feed = json.loads(feed_path.read_text())
    assert set(feed["platforms"]) == set(UPDATE_TARGETS.values())
    # The macOS entry and the release text are left exactly as the Mac build wrote them.
    assert (
        feed["platforms"]["darwin-aarch64-mps-native"]
        == (original["platforms"]["darwin-aarch64-mps-native"])
    )
    assert {key: feed[key] for key in ("version", "pub_date", "notes")} == {
        key: original[key] for key in ("version", "pub_date", "notes")
    }
    base = "https://github.com/HerRei/local-upscale/releases/download/v1.2.3-beta"
    cpu = feed["platforms"]["windows-x86_64-cpu-native"]
    package = downloads / "windows-x86_64-cpu" / "LocalSR-v1.2.3-beta-Windows-CPU-x86_64.exe"
    assert cpu["url"] == f"{base}/{package.name}"
    assert cpu["signature"] == package.with_name(package.name + ".sig").read_text()
    contract = cpu["localsr"]
    assert contract["backend"] == "cpu" and contract["kind"] == "native"
    assert (
        contract["engine_id"]
        == contract["engine"]["id"]
        == release_targets.engine_id(release_targets.target("windows-x86_64-cpu"), COMMIT)
    )
    assert contract["size"] == package.stat().st_size
    assert contract["sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()
    assert "parts" not in contract
    manifest = contract["engine"]["manifest"]
    assert manifest["name"] == "LocalSR-v1.2.3-beta-Windows-CPU-x86_64.engine-payload.json"
    engine = json.loads((package.parent / manifest["name"]).read_text())
    # A bundled engine is installed twice: in the package and as the versioned engine.
    assert contract["unpacked_size"] == contract["size"] + 2 * engine["unpacked_bytes"]
    statement = json.loads(base64.b64decode(contract["statement"]))
    assert statement == {
        "schema_version": 1,
        "version": "1.2.3-beta",
        "channel": "beta",
        "target": "windows-x86_64-cpu-native",
        "backend": "cpu",
        "kind": "native",
        "protocol": 1,
        "engine_id": release_targets.engine_id(
            release_targets.target("windows-x86_64-cpu"), COMMIT
        ),
        "unpacked_size": contract["unpacked_size"],
        "sha256": contract["sha256"],
        "engine_manifest_sha256": manifest["sha256"],
    }

    rocm = feed["platforms"]["linux-x86_64-rocm-native"]["localsr"]
    names = [part["name"] for part in rocm["parts"]]
    assert names[0].endswith(".AppImage.part-0001") and len(names) > 1
    assert feed["platforms"]["linux-x86_64-rocm-native"]["url"] == f"{base}/{names[0]}"
    assert sum(part["size"] for part in rocm["parts"]) == rocm["size"]
    directory = downloads / "linux-x86_64-rocm"
    whole = b"".join((directory / name).read_bytes() for name in names)
    assert rocm["sha256"] == hashlib.sha256(whole).hexdigest()
    # The whole AppImage was put together only to sign it; GitHub cannot host it.
    assert not (directory / "LocalSR-v1.2.3-beta-Linux-ROCm-x86_64.AppImage").exists()
    assert not [path for path in directory.iterdir() if path.name.startswith(".")]
    assert (directory / "LocalSR-v1.2.3-beta-Linux-ROCm-x86_64.AppImage.sig").is_file()
    assert all(path.is_file() and path.suffix == ".sig" for path in signatures)
    # Every signed copy had exactly the bytes the feed describes.
    for entry in feed["platforms"].values():
        contract = entry["localsr"]
        for item in [*contract.get("parts", []), contract["engine"]["manifest"]]:
            if item["name"] in signed:
                assert signed[item["name"]] == item["sha256"]
    verify_feed(feed_path, [downloads, mac])


@pytest.mark.parametrize("failure", ["checksum", "identity", "engine", "missing"])
def test_platform_tool_refuses_artifacts_it_cannot_vouch_for(tmp_path: Path, failure: str) -> None:
    downloads = platform_downloads(tmp_path)
    feed_path = tmp_path / "beta.json"
    _, original = mac_feed(tmp_path)
    directory = downloads / "linux-x86_64-cpu"
    name = "LocalSR-v1.2.3-beta-Linux-CPU-x86_64.AppImage"
    match = {
        "checksum": "checksum",
        "identity": "update identity",
        "engine": "engine payload",
        "missing": "linux-x86_64-cpu",
    }[failure]
    if failure == "checksum":
        (directory / name).write_bytes(b"substituted")
    elif failure == "identity":
        smoke = json.loads((directory / f"{name}.smoke.json").read_text())
        smoke["update"]["engine_id"] = "linux-x86_64-cpu-ffffffffffff"
        (directory / f"{name}.smoke.json").write_text(json.dumps(smoke))
    elif failure == "engine":
        next(directory.glob("*.engine.tar.gz.part-0001")).write_bytes(b"corrupt")
    else:
        shutil.rmtree(directory)
    with pytest.raises(ValueError, match=match):
        update_signing.add_platforms(
            downloads, feed_path, COMMIT, release_version="1.2.3-beta", sign=fake_sign
        )
    assert json.loads(feed_path.read_text()) == original


# Review findings 7 and 8: a payload the app could not install, or not the one the
# package was tested with, stops the tool before it signs anything.
@pytest.mark.parametrize(
    "failure",
    [
        "absolute",
        "traversal",
        "link",
        "no-files",
        "not-an-archive",
        "tested-engine",
        "missing-smoke",
        "null-smoke",
    ],
)
def test_platform_tool_signs_nothing_for_a_payload_it_cannot_vouch_for(
    tmp_path: Path, failure: str
) -> None:
    downloads = platform_downloads(tmp_path)
    feed_path = tmp_path / "beta.json"
    _, original = mac_feed(tmp_path)
    target = (
        "windows-x86_64-cuda"
        if failure in {"tested-engine", "missing-smoke", "null-smoke"}
        else "linux-x86_64-rocm"
    )
    directory = downloads / target
    manifest_path = next(directory.glob("*.engine-payload.json"))
    manifest = json.loads(manifest_path.read_text())
    part = directory / manifest["parts"][0]["filename"]
    outside = tmp_path / "outside" / part.name
    outside.parent.mkdir()
    if failure == "absolute":
        shutil.copyfile(part, outside)
        manifest["parts"][0]["filename"] = str(outside)
    elif failure == "traversal":
        shutil.copyfile(part, outside)
        manifest["parts"][0]["filename"] = f"{part.name}/../../../outside/{part.name}"
    elif failure == "link":
        part.rename(outside)
        part.symlink_to(outside)
    elif failure == "no-files":
        manifest["file_count"] = 0
    elif failure == "not-an-archive":
        part.write_bytes(b"not an engine archive" * 8)
        manifest["parts"][0].update(
            size=part.stat().st_size, sha256=hashlib.sha256(part.read_bytes()).hexdigest()
        )
    else:
        smoke_path = next(directory.glob("*.smoke.json"))
        smoke = json.loads(smoke_path.read_text())
        if failure == "missing-smoke":
            del smoke["engine_payload_sha256"]
        else:
            smoke["engine_payload_sha256"] = None if failure == "null-smoke" else "f" * 64
        smoke_path.write_text(json.dumps(smoke))
    manifest_path.write_text(json.dumps(manifest))
    signed: list[Path] = []
    with pytest.raises(ValueError):
        update_signing.add_platforms(
            downloads,
            feed_path,
            COMMIT,
            release_version="1.2.3-beta",
            sign=lambda path: (signed.append(path), fake_sign(path)),
        )
    assert signed == []
    assert json.loads(feed_path.read_text()) == original


@pytest.mark.parametrize("failure", ["traversal", "no-files", "not-an-archive"])
def test_engine_payload_reuse_refuses_what_the_installer_would_refuse(
    tmp_path: Path, failure: str
) -> None:
    built = tmp_path / "built"
    prefix = "LocalSR-v1.2.3-beta-Windows-CUDA-x86_64.engine.tar.gz"
    source = engine_payload.prepare(fake_engine(tmp_path, "localsr-worker.exe"), built, prefix)
    output = tmp_path / "out"
    output.mkdir()
    for part in built.glob("*.part-*"):
        (output / part.name).write_bytes(part.read_bytes())
    manifest = json.loads(source.read_text())
    if failure == "traversal":
        (tmp_path / "elsewhere").mkdir()
        shutil.copyfile(next(built.glob("*.part-*")), tmp_path / "elsewhere/x")
        manifest["parts"][0]["filename"] = f"{prefix}.part-0001/../../elsewhere/x"
    elif failure == "no-files":
        manifest["file_count"] = 0
    else:
        part = next(output.glob("*.part-*"))
        part.write_bytes(b"plain bytes")
        manifest["parts"][0].update(size=11, sha256=hashlib.sha256(b"plain bytes").hexdigest())
    source.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        engine_payload.prepare_for_target(
            "windows-x86_64-cuda", output, reuse=source, release_version="1.2.3-beta"
        )


# Review finding 9: files that change during signing are never signed as checked.
def test_platform_tool_signs_only_copies_of_what_it_checked(tmp_path: Path) -> None:
    downloads = platform_downloads(tmp_path)
    feed_path = tmp_path / "beta.json"
    _, original = mac_feed(tmp_path)
    later = downloads / "linux-x86_64-cpu" / "LocalSR-v1.2.3-beta-Linux-CPU-x86_64.AppImage"
    signed: list[Path] = []

    def swapping(path: Path) -> None:
        signed.append(path)
        if len(signed) == 1:
            later.write_bytes(b"replaced while the first file was being signed")
        fake_sign(path)

    with pytest.raises(ValueError, match="changed after it was checked"):
        update_signing.add_platforms(
            downloads, feed_path, COMMIT, release_version="1.2.3-beta", sign=swapping
        )
    assert later.name not in [path.name for path in signed]
    assert all(downloads not in path.parents for path in signed)
    assert json.loads(feed_path.read_text()) == original


def test_platform_tool_needs_the_mac_feed_of_the_same_release(tmp_path: Path) -> None:
    downloads = platform_downloads(tmp_path)
    feed_path = tmp_path / "beta.json"
    with pytest.raises(ValueError, match="release_macos_beta.sh"):
        update_signing.add_platforms(
            downloads, feed_path, COMMIT, release_version="1.2.3-beta", sign=fake_sign
        )
    mac_feed(tmp_path, "1.2.2-beta")
    with pytest.raises(ValueError, match="version"):
        update_signing.add_platforms(
            downloads, feed_path, COMMIT, release_version="1.2.3-beta", sign=fake_sign
        )


def feed_with_every_target(tmp_path: Path) -> tuple[Path, list[Path]]:
    downloads = platform_downloads(tmp_path)
    feed_path = tmp_path / "beta.json"
    mac, _ = mac_feed(tmp_path)
    update_signing.add_platforms(
        downloads, feed_path, COMMIT, release_version="1.2.3-beta", sign=fake_sign
    )
    return feed_path, [downloads, mac]


@pytest.mark.parametrize(
    "failure",
    [
        "missing-target",
        "unknown-target",
        "backend",
        "engine-id",
        "no-engine",
        "part-sum",
        "oversized-file",
        "http",
        "unsigned",
        "statement",
        "unsigned-statement",
        "other-key",
        "other-bytes",
    ],
)
def test_feed_contract_enforces_every_edition(tmp_path: Path, failure: str) -> None:
    feed_path, folders = feed_with_every_target(tmp_path)
    feed = json.loads(feed_path.read_text())
    platforms = feed["platforms"]
    cpu = platforms["windows-x86_64-cpu-native"]
    rocm = platforms["linux-x86_64-rocm-native"]
    package = folders[0] / "windows-x86_64-cpu" / "LocalSR-v1.2.3-beta-Windows-CPU-x86_64.exe"
    if failure == "missing-target":
        del platforms["linux-x86_64-cuda-native"]
    elif failure == "unknown-target":
        platforms["linux-x86_64-xpu-native"] = cpu
    elif failure == "backend":
        cpu["localsr"]["backend"] = "cuda"
    elif failure == "engine-id":
        cpu["localsr"]["engine_id"] = cpu["localsr"]["engine"]["id"] = "windows-x86_64-cpu-old"
    elif failure == "no-engine":
        del cpu["localsr"]["engine"]
    elif failure == "part-sum":
        rocm["localsr"]["parts"][0]["size"] += 1
    elif failure == "oversized-file":
        cpu["localsr"]["size"] = cpu["localsr"]["unpacked_size"] = 3 * 1024**3
    elif failure == "http":
        cpu["url"] = cpu["url"].replace("https://", "http://")
    elif failure == "unsigned":
        cpu["signature"] = ""
    elif failure == "statement":
        # A relabelled package: well-formed, but not what the signed statement says.
        cpu["localsr"]["sha256"] = "f" * 64
    elif failure == "unsigned-statement":
        cpu["localsr"]["statement_signature"] = OTHER_KEY.signature(
            base64.b64decode(cpu["localsr"]["statement"])
        )
    elif failure == "other-key":
        cpu["signature"] = OTHER_KEY.signature(package.read_bytes(), package.name)
    else:
        package.write_bytes(b"other bytes of the same length".ljust(package.stat().st_size))
    match = {
        "missing-target": "missing",
        "unknown-target": "unknown",
        "backend": "cpu native build",
        "engine-id": "engine "
        + release_targets.engine_id(release_targets.target("windows-x86_64-cpu"), COMMIT),
        "no-engine": "no engine package",
        "part-sum": "add up",
        "oversized-file": "2 GiB",
        "http": "HTTPS",
        "unsigned": "signature",
        "statement": "statement does not match",
        "unsigned-statement": "signed release statement from the updater key",
        "other-key": "no valid signature from the updater key",
        "other-bytes": "does not match its feed entry",
    }[failure]
    feed_path.write_text(json.dumps(feed))
    with pytest.raises(ValueError, match=match):
        verify_feed(feed_path, folders)


def test_feed_contract_cli(tmp_path: Path) -> None:
    feed_path, folders = feed_with_every_target(tmp_path)
    key = tmp_path / "test.pub"
    key.write_text(TEST_KEY.public_key + "\n")
    command = [
        sys.executable,
        str(ROOT / "scripts/verify_prepared_release.py"),
        "--feed",
        str(feed_path),
        "--commit",
        COMMIT,
        "--public-key",
        str(key),
        *[argument for folder in folders for argument in ("--files", str(folder))],
        "--version",
        "1.2.3-beta",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "7 update targets and their signatures" in result.stdout
    for changed in (command[:-1] + ["1.2.4-beta"], command[:4] + command[-2:]):
        result = subprocess.run(changed, capture_output=True, text=True, check=False)
        assert result.returncode != 0


@pytest.mark.parametrize("failure", ["duplicate", "case-collision", "crc", "trailer"])
def test_round2_signer_rejects_uninstallable_archives(tmp_path: Path, failure: str) -> None:
    import gzip
    import io
    import tarfile

    tar = io.BytesIO()
    names = [
        "localsr-worker",
        "LOCALSR-WORKER" if failure == "case-collision" else "localsr-worker",
    ]
    if failure in {"crc", "trailer"}:
        names = names[:1]
    with tarfile.open(fileobj=tar, mode="w") as archive:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = 3
            archive.addfile(info, io.BytesIO(b"abc"))
    data = gzip.compress(tar.getvalue(), mtime=0)
    if failure == "crc":
        data = data[:-8] + bytes([data[-8] ^ 1]) + data[-7:]
    elif failure == "trailer":
        data = data[:-8]
    name = "engine.part-0001"
    (tmp_path / name).write_bytes(data)
    manifest = {
        "schema_version": 1,
        "format": "tar.gz.parts",
        "backend": "CPU",
        "file_count": len(names),
        "unpacked_bytes": 3 * len(names),
        "parts": [
            {"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        ],
    }
    with pytest.raises(ValueError):
        engine_payload.validate_payload(manifest, tmp_path, "engine", "CPU", "localsr-worker")


def test_round2_identity_key_cannot_forge_minisign() -> None:
    identity = b"\x01" + b"\x00" * 31
    raw = identity + b"\x00" * 32
    public = base64.b64encode(
        b"untrusted comment: weak key\n" + base64.b64encode(b"Edlocalsr1" + identity)
    ).decode()
    signature = base64.b64encode(
        b"untrusted comment: signature\n"
        + base64.b64encode(b"EDlocalsr1" + raw)
        + b"\ntrusted comment: forged\n"
        + base64.b64encode(raw)
    ).decode()
    for data in [b"arbitrary", b"another message"]:
        assert not verify_prepared.minisign_verify(public, signature, [data])


def test_round2_statement_authenticates_install_space() -> None:
    contract = {
        "channel": "beta",
        "backend": "cpu",
        "kind": "native",
        "protocol": 1,
        "engine_id": "engine",
        "sha256": "a" * 64,
        "unpacked_size": 1234,
    }
    statement = json.loads(update_signing.release_statement("1.2.3", "target", contract))
    assert statement["unpacked_size"] == 1234


def _statement_v2(entry: dict) -> dict:
    return json.loads(base64.b64decode(entry["localsr"]["statement_v2"]))


def test_statement_v2_signs_the_sizes_and_the_part_lists_and_v1_stays_for_old_clients(
    tmp_path: Path,
) -> None:
    feed_path, _ = feed_with_every_target(tmp_path)
    feed = json.loads(feed_path.read_text())
    rocm = feed["platforms"]["linux-x86_64-rocm-native"]
    contract = rocm["localsr"]
    listed = lambda files: [  # noqa: E731
        {"name": f["name"], "size": f["size"], "sha256": f["sha256"]} for f in files
    ]
    statement = _statement_v2(rocm)
    assert statement == {
        "schema_version": 2,
        "version": "1.2.3-beta",
        "channel": "beta",
        "target": "linux-x86_64-rocm-native",
        "backend": "rocm",
        "kind": "native",
        "protocol": 1,
        "engine_id": contract["engine_id"],
        "size": contract["size"],
        "unpacked_size": contract["unpacked_size"],
        "sha256": contract["sha256"],
        "parts": listed(contract["parts"]),
        "engine_manifest": listed([contract["engine"]["manifest"]])[0],
        "engine_parts": listed(contract["engine"]["parts"]),
    }
    assert len(statement["parts"]) > 1 and statement["engine_parts"]
    assert verify_prepared.minisign_verify(
        TEST_KEY.public_key,
        contract["statement_v2_signature"],
        [base64.b64decode(contract["statement_v2"])],
    )
    # Clients before the format change read the version 1 statement, which is unchanged.
    old = json.loads(base64.b64decode(contract["statement"]))
    assert old["schema_version"] == 1 and set(old) == {
        "schema_version",
        "version",
        "channel",
        "target",
        "backend",
        "kind",
        "protocol",
        "engine_id",
        "unpacked_size",
        "sha256",
        "engine_manifest_sha256",
    }
    # An entry without an application part list signs an empty one.
    cpu = feed["platforms"]["windows-x86_64-cpu-native"]
    assert _statement_v2(cpu)["parts"] == []


@pytest.mark.parametrize(
    "tamper",
    [
        "size",
        "part-name",
        "part-size",
        "part-digest",
        "part-added",
        "part-removed",
        "engine-manifest-name",
        "engine-manifest-size",
        "engine-part-name",
        "engine-part-size",
        "engine-part-digest",
        "engine-part-added",
        "engine-part-removed",
    ],
)
def test_the_feed_check_refuses_any_change_to_a_signed_size_or_part(
    tmp_path: Path, tamper: str
) -> None:
    feed_path, _ = feed_with_every_target(tmp_path)
    contract = json.loads(feed_path.read_text())["platforms"]["linux-x86_64-rocm-native"]["localsr"]
    verify_prepared._statement(
        "linux-x86_64-rocm-native", contract, "1.2.3-beta", "beta", TEST_KEY.public_key
    )
    engine = contract["engine"]
    if tamper == "size":
        contract["size"] += 1
    elif tamper == "part-name":
        contract["parts"][0]["name"] += "x"
    elif tamper == "part-size":
        contract["parts"][0]["size"] += 1
    elif tamper == "part-digest":
        contract["parts"][0]["sha256"] = "0" * 64
    elif tamper == "part-added":
        contract["parts"].append(dict(contract["parts"][-1]))
    elif tamper == "part-removed":
        contract["parts"].pop()
    elif tamper == "engine-manifest-name":
        engine["manifest"]["name"] += "x"
    elif tamper == "engine-manifest-size":
        engine["manifest"]["size"] += 1
    elif tamper == "engine-part-name":
        engine["parts"][0]["name"] += "x"
    elif tamper == "engine-part-size":
        engine["parts"][0]["size"] += 1
    elif tamper == "engine-part-digest":
        engine["parts"][0]["sha256"] = "0" * 64
    elif tamper == "engine-part-added":
        engine["parts"].append(dict(engine["parts"][-1]))
    else:
        engine["parts"].pop()
    with pytest.raises(ValueError, match="statement does not match"):
        verify_prepared._statement(
            "linux-x86_64-rocm-native", contract, "1.2.3-beta", "beta", TEST_KEY.public_key
        )


@pytest.mark.parametrize("field", ["statement_v2", "statement_v2_signature"])
def test_a_feed_without_the_version_2_statement_is_refused(tmp_path: Path, field: str) -> None:
    feed_path, folders = feed_with_every_target(tmp_path)
    feed = json.loads(feed_path.read_text())
    del feed["platforms"]["windows-x86_64-cpu-native"]["localsr"][field]
    feed_path.write_text(json.dumps(feed))
    with pytest.raises(ValueError, match="signed release statement"):
        verify_feed(feed_path, folders)


def test_add_statements_signs_both_formats_and_fills_in_what_an_older_feed_lacks(
    tmp_path: Path,
) -> None:
    _, feed = mac_feed(tmp_path)
    path = tmp_path / "old.json"
    contract = feed["platforms"]["darwin-aarch64-mps-native"]["localsr"]
    version_1 = (contract["statement"], contract["statement_signature"])
    contract.pop("statement_v2")
    contract.pop("statement_v2_signature")
    path.write_text(json.dumps(feed))
    assert update_signing.add_statements(path, sign=fake_sign) == ["darwin-aarch64-mps-native"]
    again = json.loads(path.read_text())["platforms"]["darwin-aarch64-mps-native"]["localsr"]
    assert (again["statement"], again["statement_signature"]) == version_1
    assert _statement_v2({"localsr": again})["schema_version"] == 2
    verify_prepared._statement(
        "darwin-aarch64-mps-native", again, "1.2.3-beta", "beta", TEST_KEY.public_key
    )
    # An entry with a statement but no signature for it is signed again, not skipped.
    for field in ("statement_signature", "statement_v2_signature"):
        entry = json.loads(path.read_text())
        entry["platforms"]["darwin-aarch64-mps-native"]["localsr"][field] = ""
        path.write_text(json.dumps(entry))
        assert update_signing.add_statements(path, sign=fake_sign) == ["darwin-aarch64-mps-native"]


# The updater's own tests (desktop/src-tauri/src/updates.rs) verify statements signed here,
# with the disposable key, so both implementations must agree on every byte.
RUST_FIXTURE = ROOT / "desktop/src-tauri/tests/fixtures/update-release.json"


def rust_fixture_statements(fixture: dict) -> dict:
    def file(name: str, size: int, digest: str) -> dict:
        return {"name": name, "url": f"https://example.test/{name}", "size": size, "sha256": digest}

    manifest = fixture["engine"]["manifest"].encode()
    engine = {
        "id": "linux-x86_64-rocm-0123456789ab",
        "manifest": file(
            "engine-payload.json", len(manifest), hashlib.sha256(manifest).hexdigest()
        ),
        "parts": [
            file(
                part["name"],
                len(base64.b64decode(part["data"])),
                part["sha256"],
            )
            for part in fixture["engine"]["parts"]
        ],
    }
    contract = {
        "channel": "beta",
        "backend": "rocm",
        "kind": "native",
        "protocol": 1,
        "engine_id": engine["id"],
        "size": len(fixture["package"]["data"].encode()),
        "unpacked_size": 1024,
        "sha256": fixture["package"]["sha256"],
        "engine": engine,
    }
    split = {**contract, "parts": [dict(part) for part in fixture["split"]["parts"]]}
    out = {}
    for name, entry in (("single", contract), ("split", split)):
        data = update_signing.release_statement_v2("0.3.7-beta", "linux-x86_64-rocm-native", entry)
        out[name] = (
            base64.b64encode(data).decode(),
            TEST_KEY.signature(data, "linux-x86_64-rocm-native.statement-v2.json"),
        )
    # Signed by the right key but not format 2: the client must refuse them on their content.
    key = "linux-x86_64-rocm-native"
    version_2 = json.loads(update_signing.release_statement_v2("0.3.7-beta", key, contract))
    for name, data in (
        ("format_1", update_signing.release_statement("0.3.7-beta", key, contract)),
        ("extra_field", update_signing._statement_bytes({**version_2, "extra": 1})),
        (
            "missing_parts",
            update_signing._statement_bytes({k: v for k, v in version_2.items() if k != "parts"}),
        ),
    ):
        out[name] = (base64.b64encode(data).decode(), TEST_KEY.signature(data, f"{name}.json"))
    return out


def test_the_updater_fixture_holds_the_statements_this_signer_writes() -> None:
    fixture = json.loads(RUST_FIXTURE.read_text())
    assert fixture["public_key"] == TEST_KEY.public_key
    signed = rust_fixture_statements(fixture)
    assert (fixture["statement_v2"], fixture["statement_v2_signature"]) == signed["single"]
    assert (fixture["split"]["statement_v2"], fixture["split"]["statement_v2_signature"]) == signed[
        "split"
    ]
    for name in ("format_1", "extra_field", "missing_parts"):
        assert (
            fixture["rejected"][name]["statement"],
            fixture["rejected"][name]["signature"],
        ) == signed[name]
    assert verify_prepared.minisign_verify(
        fixture["public_key"],
        fixture["statement_v2_signature"],
        [base64.b64decode(fixture["statement_v2"])],
    )


@pytest.mark.parametrize("target_id", ["linux-x86_64-cpu", "macos-arm64-mps"])
def test_engine_identity_survives_app_releases_but_tracks_engine_inputs(
    tmp_path: Path, target_id: str
) -> None:
    target = release_targets.target(target_id)
    for directory in ["src/localsr", "packaging", "scripts", "requirements/locks"]:
        shutil.copytree(ROOT / directory, tmp_path / directory, dirs_exist_ok=True)
    shutil.copyfile(
        ROOT / "requirements/build-tools.txt", tmp_path / "requirements/build-tools.txt"
    )
    shutil.copyfile(ROOT / "uv.lock", tmp_path / "uv.lock")
    shutil.copyfile(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    before = release_targets.engine_id(target, COMMIT, root=tmp_path)
    (tmp_path / "desktop").mkdir()
    (tmp_path / "desktop/app.ts").write_text("app changed")
    init = tmp_path / "src/localsr/__init__.py"
    init.write_text(init.read_text().replace("0.3.5-beta", "0.3.7-beta"))
    assert release_targets.engine_id(target, "f" * 40, root=tmp_path) == before
    # Release tooling is not engine content: editing it must not force a new engine.
    tooling = tmp_path / "scripts/release_targets.py"
    tooling.write_text(tooling.read_text() + "\n# release tooling edit\n")
    assert release_targets.engine_id(target, COMMIT, root=tmp_path) == before
    recipe = [
        "src/localsr/worker/server.py",
        "packaging/tauri_worker.spec",
        "src/localsr/__init__.py",
        # Scripts the worker build runs choose the native executables it bundles.
        "scripts/build_edit_runtime.py",
        "scripts/build_media_helper.py",
        "scripts/thin_macho_binaries.py",
        "scripts/build_source_bundle.py",
    ]
    recipe.append(
        "requirements/locks/linux-x86_64-cpu.txt"
        if target_id.startswith("linux")
        else "packaging/macos/media-helper/main.swift"
    )
    for name in recipe:
        source = tmp_path / name
        original = source.read_text()
        source.write_text(original + "\nENGINE_CHANGED = True\n")
        assert release_targets.engine_id(target, COMMIT, root=tmp_path) != before, name
        source.write_text(original)


# Round 3 (independent review of the release tools).
def test_round3_signer_refuses_a_build_from_another_commit(tmp_path: Path) -> None:
    downloads = platform_downloads(tmp_path)
    feed_path = tmp_path / "beta.json"
    mac_feed(tmp_path)
    directory = downloads / "linux-x86_64-cpu"
    report = directory / "LocalSR-v1.2.3-beta-Linux-CPU-x86_64.AppImage.smoke.json"
    for commit in ("b" * 40, None):
        smoke = json.loads(report.read_text())
        smoke.pop("commit")
        if commit:
            smoke["commit"] = commit
        report.write_text(json.dumps(smoke))
        with pytest.raises(ValueError, match="commit"):
            update_signing.add_platforms(
                downloads, feed_path, COMMIT, release_version="1.2.3-beta", sign=fake_sign
            )


def test_round3_smoke_report_records_the_commit_it_was_taken_for(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps(installed_smoke("1-beta")))
    smoke_installer.record_commit(report, COMMIT)
    assert json.loads(report.read_text())["commit"] == COMMIT


@pytest.mark.parametrize("failure", ["unpacked-too-small", "part-digest", "part-order"])
def test_round3_feed_check_compares_the_engine_manifest_with_the_feed(
    tmp_path: Path, failure: str
) -> None:
    feed_path, (downloads, mac) = feed_with_every_target(tmp_path)
    feed = json.loads(feed_path.read_text())
    contract = feed["platforms"]["darwin-aarch64-mps-native"]["localsr"]
    if failure == "unpacked-too-small":
        contract["unpacked_size"] = contract["size"]
    else:
        item = contract["engine"]["manifest"]
        manifest_path = mac / item["name"]
        payload = json.loads(manifest_path.read_text())
        if failure == "part-digest":
            payload["parts"][0]["sha256"] = "0" * 64
        else:
            payload["parts"][0]["filename"] = "other.part-0001"
        manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        fake_sign(manifest_path)
        item.update(
            size=manifest_path.stat().st_size,
            sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            signature=manifest_path.with_name(manifest_path.name + ".sig").read_text(),
        )
    contract.pop("statement")
    contract.pop("statement_signature")
    feed_path.write_text(json.dumps(feed))
    update_signing.add_statements(feed_path, sign=fake_sign)
    with pytest.raises(ValueError, match="engine manifest|unpacked"):
        verify_feed(feed_path, [downloads, mac])


def test_round3_feed_check_does_not_adopt_the_feeds_own_download_host(tmp_path: Path) -> None:
    feed_path, folders = feed_with_every_target(tmp_path)
    official = "https://github.com/HerRei/local-upscale/releases/download/v1.2.3-beta"
    staging = "https://staging.example.org/files"
    feed_path.write_text(feed_path.read_text().replace(official, staging))
    with pytest.raises(ValueError, match="HTTPS"):
        verify_feed(feed_path, folders)
    verify_feed(feed_path, folders, host=staging)


def test_round3_stable_release_signs_the_stable_channel(tmp_path: Path) -> None:
    downloads = platform_downloads(tmp_path, "1.2.3")
    feed_path = tmp_path / "beta.json"
    mac_feed(tmp_path, "1.2.3")
    update_signing.add_platforms(
        downloads, feed_path, COMMIT, release_version="1.2.3", sign=fake_sign
    )
    feed = json.loads(feed_path.read_text())
    for key, entry in feed["platforms"].items():
        if key.startswith("darwin"):
            continue
        contract = entry["localsr"]
        assert contract["channel"] == "stable", key
        assert json.loads(base64.b64decode(contract["statement"]))["channel"] == "stable"


def test_round3_statements_are_signed_only_for_entries_with_an_engine_package(
    tmp_path: Path,
) -> None:
    _, feed = mac_feed(tmp_path)
    entry = feed["platforms"]["darwin-aarch64-mps-native"]["localsr"]
    entry.pop("statement")
    entry.pop("statement_signature")
    entry.pop("engine")
    path = tmp_path / "unsigned.json"
    path.write_text(json.dumps(feed))
    with pytest.raises(ValueError, match="engine"):
        update_signing.add_statements(path, sign=fake_sign)
    assert json.loads(path.read_text()) == feed


def test_round3_engine_id_ignores_finder_files_and_line_endings_but_not_missing_inputs(
    tmp_path: Path,
) -> None:
    target = release_targets.target("linux-x86_64-cpu")
    for directory in ["src/localsr", "packaging", "scripts", "requirements/locks"]:
        shutil.copytree(ROOT / directory, tmp_path / directory, dirs_exist_ok=True)
    shutil.copyfile(
        ROOT / "requirements/build-tools.txt", tmp_path / "requirements/build-tools.txt"
    )
    before = release_targets.engine_id(target, COMMIT, root=tmp_path)
    (tmp_path / "packaging/hooks/.DS_Store").write_bytes(b"finder")
    leftover = tmp_path / "packaging/macos/media-helper/.build"
    leftover.mkdir(parents=True)
    (leftover / "output.o").write_bytes(b"build output")
    assert release_targets.engine_id(target, COMMIT, root=tmp_path) == before
    script = tmp_path / "packaging/nvidia/extra.cmd"
    script.write_bytes(b"echo one\necho two\n")
    unix = release_targets.engine_id(target, COMMIT, root=tmp_path)
    script.write_bytes(b"echo one\r\necho two\r\n")
    assert release_targets.engine_id(target, COMMIT, root=tmp_path) == unix
    shutil.rmtree(tmp_path / "packaging/ffmpeg")
    with pytest.raises(ValueError, match="missing"):
        release_targets.engine_id(target, COMMIT, root=tmp_path)


def test_round3_hard_linked_engine_files_are_archived_whole(tmp_path: Path) -> None:
    import os

    engine = fake_engine(tmp_path, "localsr-worker")
    os.link(engine / "lib/model.bin", engine / "lib/model-copy.bin")
    output = tmp_path / "out"
    written = engine_payload.prepare(engine, output, "p", backend="CPU", worker="localsr-worker")
    manifest = json.loads(written.read_text())
    engine_payload.validate_payload(manifest, output, "p", "CPU", "localsr-worker")


linux_symlinks = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")


@linux_symlinks
def test_engine_payload_archives_an_internal_file_link_as_a_regular_file(tmp_path: Path) -> None:
    import tarfile

    engine = fake_engine(tmp_path, "localsr-worker")
    (engine / "lib/libfoo.so.1").write_bytes(b"shared-library")
    (engine / "lib/libfoo.so").symlink_to("libfoo.so.1")
    (engine / "lib/chain.so").symlink_to("libfoo.so")
    output = tmp_path / "out"
    written = engine_payload.prepare(engine, output, "p", backend="CPU", worker="localsr-worker")
    manifest = json.loads(written.read_text())
    engine_payload.validate_payload(manifest, output, "p", "CPU", "localsr-worker")
    regular = [p for p in engine.rglob("*") if not p.is_dir()]
    assert manifest["file_count"] == len(regular)
    assert manifest["unpacked_bytes"] == sum(p.stat().st_size for p in regular)
    joined = b"".join((output / part["filename"]).read_bytes() for part in manifest["parts"])
    archive = tmp_path / "all.tar.gz"
    archive.write_bytes(joined)
    with tarfile.open(archive) as tar:
        for name in ("lib/libfoo.so", "lib/chain.so"):
            member = tar.getmember(name)
            assert member.isfile()
            assert tar.extractfile(member).read() == b"shared-library"


@linux_symlinks
@pytest.mark.parametrize("kind", ["external", "dangling", "loop", "directory", "outside-dir"])
def test_engine_payload_refuses_links_that_leave_dangle_or_point_at_directories(
    tmp_path: Path, kind: str
) -> None:
    engine = fake_engine(tmp_path, "localsr-worker")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    (tmp_path / "outside-dir").mkdir()
    link = engine / "lib/bad"
    if kind == "external":
        link.symlink_to(outside)
    elif kind == "dangling":
        link.symlink_to("missing.so")
    elif kind == "loop":
        link.symlink_to("bad")
    elif kind == "directory":
        link.symlink_to(engine / "lib")
    else:
        link.symlink_to(tmp_path / "outside-dir")
    with pytest.raises(ValueError, match="link"):
        engine_payload.prepare(
            engine, tmp_path / "out", "p", backend="CPU", worker="localsr-worker"
        )


@pytest.mark.parametrize("failure", ["first-part-is-zero", "doubled-slash"])
def test_round3_engine_parts_are_numbered_from_one_and_names_are_canonical(
    tmp_path: Path, failure: str
) -> None:
    import gzip
    import io
    import tarfile

    tar = io.BytesIO()
    names = ["localsr-worker", "lib//model.bin" if failure == "doubled-slash" else "lib/model.bin"]
    with tarfile.open(fileobj=tar, mode="w") as archive:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = 3
            archive.addfile(info, io.BytesIO(b"abc"))
    data = gzip.compress(tar.getvalue(), mtime=0)
    name = "engine.part-0000" if failure == "first-part-is-zero" else "engine.part-0001"
    (tmp_path / name).write_bytes(data)
    manifest = {
        "schema_version": 1,
        "format": "tar.gz.parts",
        "backend": "CPU",
        "file_count": 2,
        "unpacked_bytes": 6,
        "parts": [
            {"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        ],
    }
    with pytest.raises(ValueError):
        engine_payload.validate_payload(manifest, tmp_path, "engine", "CPU", "localsr-worker")


def test_round3_checksum_file_lines_are_unique_and_well_formed() -> None:
    digest = "a" * 64
    assert verify_prepared.parse_checksums([f"{digest}  one", f"{'b' * 64}  two"]) == {
        "one": digest,
        "two": "b" * 64,
    }
    for lines in (
        [f"{digest}  one", f"{'b' * 64}  one"],
        [f"{digest} one"],
        [f"{digest}  dir/one"],
        ["short  one"],
    ):
        with pytest.raises(ValueError, match="checksum"):
            verify_prepared.parse_checksums(lines)


@pytest.mark.skipif(os.name != "posix", reason="the signer runs on macOS and checks POSIX modes")
def test_round3_every_key_read_first_checks_the_key_folder_and_the_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import types

    folder = tmp_path / "keys"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    helper = folder / "update-keychain"
    monkeypatch.setattr(update_signing, "KEY_ROOT", folder)
    monkeypatch.setattr(update_signing, "HELPER", helper)
    calls: list[str] = []

    def run(command, **options):
        calls.append(command[0])
        return types.SimpleNamespace(returncode=44, stdout=b"")

    monkeypatch.setattr(update_signing.subprocess, "run", run)
    with pytest.raises(ValueError, match="private"):
        update_signing.load_identity()
    assert calls == []
    folder.chmod(0o700)
    assert update_signing.load_identity() is None
    assert calls == ["codesign", str(helper)]
