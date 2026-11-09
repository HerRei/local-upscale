from __future__ import annotations

import hashlib
import importlib.util
import json
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

engine_payload = load_script("prepare_engine_payload")
release_targets = load_script("release_targets")
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


def test_ci_build_identity_uses_the_production_key_and_a_commit_engine_id() -> None:
    environment = release_targets.update_environment("windows-x86_64-cuda", COMMIT)
    assert environment == {
        "LOCALSR_UPDATE_PUBLIC_KEY": (ROOT / "packaging/updates/production.pub")
        .read_text()
        .strip(),
        "LOCALSR_UPDATE_BACKEND": "cuda",
        "LOCALSR_UPDATE_KIND": "native",
        "LOCALSR_ENGINE_ID": "windows-x86_64-cuda-0123456789ab",
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
        "LOCALSR_ENGINE_ID=linux-x86_64-rocm-0123456789ab",
    ]
    assert lines[0].startswith("LOCALSR_UPDATE_PUBLIC_KEY=") and "\n" not in lines[0]


def test_mac_release_script_agrees_with_the_registry_identity() -> None:
    script = (ROOT / "scripts/release_macos_beta.sh").read_text()
    mac = release_targets.target("macos-arm64-mps")
    assert f"LOCALSR_UPDATE_BACKEND={mac['update']['backend']}" in script
    assert f"LOCALSR_UPDATE_KIND={mac['update']['kind']}" in script
    assert 'LOCALSR_ENGINE_ID="macos-arm64-mps-${COMMIT:0:12}"' in script
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
        "update": release_targets.expected_update_identity(target, COMMIT)
    }


def test_installed_smoke_must_carry_the_build_update_identity() -> None:
    report = update_smoke("linux-x86_64-cuda", "1-beta")
    smoke_installer.check_update_identity(report, "linux-x86_64-cuda", COMMIT)
    assert report["update"] == {
        "enabled": True,
        "target": "linux-x86_64-cuda-native",
        "backend": "cuda",
        "kind": "native",
        "engine_id": "linux-x86_64-cuda-0123456789ab",
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
