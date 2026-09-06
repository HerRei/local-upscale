from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
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
publish_release = load_script("publish_tauri_release")


def write_pe(path: Path, machine: int) -> None:
    pe = bytearray(256)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 128)
    pe[128:132] = b"PE\0\0"
    struct.pack_into("<H", pe, 132, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pe)


def test_unsigned_cross_policy_is_restricted_to_exact_non_beta_alpha() -> None:
    manifest = {"release_policy": "v0.0.12-cross-alpha-exception"}
    readiness = {"beta_ready": False}

    assert (
        prepare_release.release_policy(manifest, readiness, "0.0.12-alpha")
        == prepare_release.CROSS_ALPHA_POLICY
    )
    with pytest.raises(ValueError, match="restricted"):
        prepare_release.release_policy(manifest, readiness, "0.0.13-alpha")
    with pytest.raises(ValueError, match="non-beta"):
        prepare_release.release_policy(manifest, {"beta_ready": True}, "0.0.12-alpha")


def test_unsigned_cross_policy_requires_explicit_signing_warning() -> None:
    evidence = {
        "status": "ad-hoc-alpha",
        "production_signed": False,
        "warning": "Testing-only ad-hoc signature",
    }
    assert (
        prepare_release.validate_signing_evidence(
            "macos", evidence, "ad-hoc-alpha", prepare_release.CROSS_ALPHA_POLICY
        )
        == evidence
    )
    with pytest.raises(ValueError, match="warning"):
        prepare_release.validate_signing_evidence(
            "macos",
            {"status": "ad-hoc-alpha", "production_signed": False},
            "ad-hoc-alpha",
            prepare_release.CROSS_ALPHA_POLICY,
        )


def test_cross_built_macos_static_smoke_is_never_reported_as_runtime_pass() -> None:
    evidence = {
        "passed": False,
        "mode": "cross-build-static",
        "version": "0.0.12-alpha",
        "architecture": "arm64",
        "static_verified": True,
        "runtime_tested": False,
        "reason": "No native Apple-Silicon runner was available.",
    }
    assert (
        prepare_release.validate_smoke_evidence(
            "macos", evidence, prepare_release.CROSS_ALPHA_POLICY, "0.0.12-alpha"
        )
        == evidence
    )
    with pytest.raises(ValueError, match="acceptable"):
        prepare_release.validate_smoke_evidence(
            "windows", evidence, prepare_release.CROSS_ALPHA_POLICY, "0.0.12-alpha"
        )


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


def test_release_matrix_rejects_weak_smoke_and_architecture_evidence() -> None:
    with pytest.raises(ValueError, match="acceptable"):
        prepare_release.validate_smoke_evidence(
            "linux",
            {"passed": True},
            prepare_release.SIGNED_POLICY,
            "0.0.12-alpha",
        )
    with pytest.raises(ValueError, match="different artifact"):
        prepare_release.validate_architecture_evidence(
            "LocalSR.AppImage",
            architecture_evidence("other.AppImage", "x86_64"),
            "x86_64",
        )


def cross_macos_provenance() -> dict[str, object]:
    return {
        "torch_version": "2.2.2",
        "torch_arm64_wheel": "torch-2.2.2-cp311-none-macosx_11_0_arm64.whl",
        "torch_arm64_sha256": "a" * 64,
        "universal2_wheel": ("torch-2.2.2-cp311-none-macosx_11_0_arm64.macosx_10_9_x86_64.whl"),
        "static_evidence": ["wheel", "merge", "Mach-O verification"],
        "openmp_normalization": {
            "schema_version": 1,
            "operation": "pair-torch-openmp-aliases",
            "source_sha256": {"torch/lib/libiomp5.dylib": "b" * 64},
            "outputs": [
                {
                    "path": f"library-{index}.dylib",
                    "architectures": ["arm64", "x86_64"],
                    "sha256": character * 64,
                }
                for index, character in enumerate(("c", "d", "e"))
            ],
        },
    }


def test_cross_macos_provenance_requires_exact_pins_and_dual_arch_openmp() -> None:
    evidence = cross_macos_provenance()
    assert prepare_release.validate_cross_macos_provenance(evidence) == evidence
    with pytest.raises(ValueError, match="torch 2.2.2"):
        prepare_release.validate_cross_macos_provenance(evidence | {"torch_version": "2.3.0"})
    malformed = dict(evidence)
    malformed["openmp_normalization"] = {
        **evidence["openmp_normalization"],
        "outputs": [
            {
                "path": "library.dylib",
                "architectures": ["x86_64"],
                "sha256": "f" * 64,
            }
        ],
    }
    with pytest.raises(ValueError, match="output evidence"):
        prepare_release.validate_cross_macos_provenance(malformed)


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
    entries = artifact_entries("1-alpha", alpha=False)
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
    from verify_prepared_release import verify

    staging, manifest, output, readiness = release_fixture(tmp_path)
    index_path = prepare_release.prepare(staging, manifest, "v1-alpha", output, readiness)
    index = json.loads(index_path.read_text())
    assert len(index["installers"]) == len(index["source_matrix"]) == 8
    assert len(index["public_assets"]) == 9
    assert index["beta_ready"] is False
    assert len((output / "release-files.txt").read_text().splitlines()) == 11
    verify(output, manifest, "c" * 40)
    assert len(publish_release.expected_assets(output)) == 11
    second = tmp_path / "rerun"
    prepare_release.prepare(staging, manifest, "v1-alpha", second, readiness)
    assert index_path.read_bytes() == (second / "release-index.json").read_bytes()


@pytest.mark.parametrize(
    "failure", ["model", "backend", "payload", "wheelhouse", "missing-target", "xpu-runtime"]
)
def test_rejects_incomplete_release_evidence(tmp_path: Path, failure: str) -> None:
    staging, manifest, output, readiness = release_fixture(tmp_path)
    match = {
        "model": "Quick/Best",
        "backend": "backend identity",
        "payload": "payload digest",
        "wheelhouse": "wheelhouse provenance",
        "missing-target": "every target",
        "xpu-runtime": "bundled Intel runtime",
    }[failure]
    if failure == "missing-target":
        data = json.loads(manifest.read_text())
        data["artifacts"].pop()
        manifest.write_text(json.dumps(data))
    elif failure == "payload":
        next(staging.rglob("*.part-0001")).write_bytes(b"corrupted")
    elif failure == "xpu-runtime":
        path = next(staging.rglob("*Linux-Intel*.metadata.json"))
        data = json.loads(path.read_text())
        data["backend_probe"].pop("xpu_runtime_files_verified")
        data["package_smoke"]["backend_probe"].pop("xpu_runtime_files_verified")
        path.write_text(json.dumps(data))
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


def test_draft_resume_uploads_only_missing_matching_assets(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    expected = {
        first.name: (first, hashlib.sha256(first.read_bytes()).hexdigest()),
        second.name: (second, hashlib.sha256(second.read_bytes()).hexdigest()),
    }
    release = {
        "tagName": "v1-alpha",
        "targetCommitish": "a" * 40,
        "isDraft": True,
        "isPrerelease": True,
        "assets": [
            {
                "name": first.name,
                "digest": "sha256:" + expected[first.name][1],
            }
        ],
    }

    assert publish_release.plan_draft_resume(
        release, expected, tag="v1-alpha", commit="a" * 40
    ) == [second]

    first_publication = release | {"assets": []}
    assert publish_release.plan_draft_resume(
        first_publication, expected, tag="v1-alpha", commit="a" * 40
    ) == [first, second]


def test_draft_resume_fails_closed_on_digest_commit_or_published_state(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "asset"
    asset.write_bytes(b"expected")
    expected = {asset.name: (asset, hashlib.sha256(asset.read_bytes()).hexdigest())}
    base = {
        "tagName": "v1-alpha",
        "targetCommitish": "a" * 40,
        "isDraft": True,
        "isPrerelease": True,
        "assets": [{"name": asset.name, "digest": "sha256:" + "0" * 64}],
    }
    with pytest.raises(ValueError, match="digest mismatch"):
        publish_release.plan_draft_resume(base, expected, tag="v1-alpha", commit="a" * 40)
    with pytest.raises(ValueError, match="tag does not match"):
        publish_release.plan_draft_resume(
            base | {"tagName": "v2"}, expected, tag="v1-alpha", commit="a" * 40
        )
    with pytest.raises(ValueError, match="target commit"):
        publish_release.plan_draft_resume(
            base | {"targetCommitish": "b" * 40},
            expected,
            tag="v1-alpha",
            commit="a" * 40,
        )
    with pytest.raises(ValueError, match="immutable"):
        publish_release.plan_draft_resume(
            base | {"isDraft": False}, expected, tag="v1-alpha", commit="a" * 40
        )

    matching = base | {
        "targetCommitish": "main",
        "assets": [{"name": asset.name, "digest": "sha256:" + expected[asset.name][1]}],
    }
    assert (
        publish_release.plan_draft_resume(matching, expected, tag="v1-alpha", commit="a" * 40) == []
    )


def test_published_release_requires_exact_title_state_and_assets(tmp_path: Path) -> None:
    asset = tmp_path / "asset"
    asset.write_bytes(b"expected")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    expected = {asset.name: (asset, digest)}
    release = {
        "tagName": "v1-alpha",
        "name": "LocalSR v1-alpha",
        "targetCommitish": "main",
        "isDraft": False,
        "isPrerelease": True,
        "assets": [{"name": asset.name, "digest": f"sha256:{digest}"}],
    }
    publish_release.verify_published_release(
        release,
        expected,
        tag="v1-alpha",
        commit="a" * 40,
        title="LocalSR v1-alpha",
    )
    with pytest.raises(ValueError, match="identity"):
        publish_release.verify_published_release(
            release | {"name": "Wrong"},
            expected,
            tag="v1-alpha",
            commit="a" * 40,
            title="LocalSR v1-alpha",
        )


def test_release_tag_must_resolve_to_the_expected_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        publish_release,
        "run",
        lambda _command, **_kwargs: type(
            "Result", (), {"stdout": "b" * 40 + "\n", "stderr": "", "returncode": 0}
        )(),
    )
    with pytest.raises(ValueError, match="expected release commit"):
        publish_release.verify_local_tag("v1-alpha", "a" * 40)


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
