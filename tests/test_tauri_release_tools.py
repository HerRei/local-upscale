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
    manifest = {"release_policy": "v0.0.11-cross-alpha-exception"}
    readiness = {"beta_ready": False}

    assert (
        prepare_release.release_policy(manifest, readiness, "0.0.11-alpha")
        == prepare_release.CROSS_ALPHA_POLICY
    )
    with pytest.raises(ValueError, match="restricted"):
        prepare_release.release_policy(manifest, readiness, "0.0.12-alpha")
    with pytest.raises(ValueError, match="non-beta"):
        prepare_release.release_policy(manifest, {"beta_ready": True}, "0.0.11-alpha")


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
        "version": "0.0.11-alpha",
        "architecture": "arm64",
        "static_verified": True,
        "runtime_tested": False,
        "reason": "No native Apple-Silicon runner was available.",
    }
    assert (
        prepare_release.validate_smoke_evidence(
            "macos", evidence, prepare_release.CROSS_ALPHA_POLICY, "0.0.11-alpha"
        )
        == evidence
    )
    with pytest.raises(ValueError, match="acceptable"):
        prepare_release.validate_smoke_evidence(
            "windows", evidence, prepare_release.CROSS_ALPHA_POLICY, "0.0.11-alpha"
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
            "0.0.11-alpha",
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


def test_compacts_exactly_three_verified_installers(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    output = tmp_path / "output"
    entries = [
        {
            "filename": "LocalSR-v1-alpha-macOS-arm64.dmg",
            "platform": "macos",
            "architecture": "arm64",
            "backend": "MPS",
            "signing": "developer-id-notarized",
        },
        {
            "filename": "LocalSR-v1-alpha-Windows-x86_64.exe",
            "platform": "windows",
            "architecture": "x86_64",
            "backend": "CPU",
            "signing": "authenticode-valid",
        },
        {
            "filename": "LocalSR-v1-alpha-Linux-x86_64.AppImage",
            "platform": "linux",
            "architecture": "x86_64",
            "backend": "CPU",
            "signing": "sha256",
        },
    ]
    for index, entry in enumerate(entries):
        directory = staging / str(entry["platform"])
        directory.mkdir(parents=True)
        artifact = directory / str(entry["filename"])
        artifact.write_bytes(f"package-{index}".encode())
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(artifact.name + ".sha256").write_text(
            f"{digest}  {artifact.name}\n", encoding="utf-8"
        )
        signing: dict[str, object] = {"status": entry["signing"]}
        if entry["platform"] == "macos":
            signing |= {
                "developer_id": "Developer ID Application: Example",
                "team_id": "TEAM123456",
                "notarized": True,
                "stapled": True,
                "gatekeeper_accepted": True,
            }
        elif entry["platform"] == "windows":
            signing |= {
                "signer_subject": "CN=Example",
                "signer_thumbprint": "ABCD",
                "timestamped": True,
            }
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
            "timestamp": "2026-09-03T12:00:00+00:00",
            "package_smoke": installed_smoke("1-alpha"),
            "signing": signing,
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
        artifact.with_name(artifact.name + ".metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        artifact.with_name(artifact.name + ".architecture.json").write_text(
            json.dumps(architecture_evidence(artifact.name, str(entry["architecture"]))),
            encoding="utf-8",
        )

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "version": "1-alpha", "artifacts": entries}),
        encoding="utf-8",
    )
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps({"release": "1-alpha", "beta_ready": False, "gates": []}),
        encoding="utf-8",
    )
    index_path = prepare_release.prepare(staging, manifest, "v1-alpha", output, readiness)
    index = json.loads(index_path.read_text(encoding="utf-8"))

    assert len(index["installers"]) == 3
    assert index["beta_ready"] is False
    assert index["beta_readiness"]["release"] == "1-alpha"
    assert {item["preset"] for item in index["live_model_evidence"]["models"]} == {
        "Quick",
        "Best",
    }
    assert len((output / "SHA256SUMS").read_text().splitlines()) == 3
    assert len((output / "release-files.txt").read_text().splitlines()) == 5
    second_output = tmp_path / "output-rerun"
    prepare_release.prepare(staging, manifest, "v1-alpha", second_output, readiness)
    assert (output / "release-index.json").read_bytes() == (
        second_output / "release-index.json"
    ).read_bytes()


def test_rejects_release_without_real_quick_and_best_evidence(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    output = tmp_path / "output"
    entries = []
    for index, (platform, suffix, architecture, backend, signing_status) in enumerate(
        (
            ("macos", ".dmg", "arm64", "MPS", "developer-id-notarized"),
            ("windows", ".exe", "x86_64", "CPU", "authenticode-valid"),
            ("linux", ".AppImage", "x86_64", "CPU", "sha256"),
        )
    ):
        filename = f"LocalSR-v1-alpha-{platform}{suffix}"
        entry = {
            "filename": filename,
            "platform": platform,
            "architecture": architecture,
            "backend": backend,
            "signing": signing_status,
        }
        entries.append(entry)
        directory = staging / platform
        directory.mkdir(parents=True)
        artifact = directory / filename
        artifact.write_bytes(f"package-{index}".encode())
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(filename + ".sha256").write_text(
            f"{digest}  {filename}\n", encoding="utf-8"
        )
        signing: dict[str, object] = {"status": signing_status}
        if platform == "macos":
            signing |= {
                "developer_id": "Developer ID Application: Example",
                "team_id": "TEAM123456",
                "notarized": True,
                "stapled": True,
                "gatekeeper_accepted": True,
            }
        elif platform == "windows":
            signing |= {
                "signer_subject": "CN=Example",
                "signer_thumbprint": "ABCD",
                "timestamped": True,
            }
        metadata = {
            "schema_version": 1,
            "artifact_filename": filename,
            "artifact_size": artifact.stat().st_size,
            "sha256": digest,
            "platform": platform,
            "architecture": architecture,
            "backend": backend,
            "repository_commit": "d" * 40,
            "github_run_id": "1",
            "run_attempt": "1",
            "timestamp": "2026-09-03T12:00:00+00:00",
            "package_smoke": installed_smoke("1-alpha"),
            "signing": signing,
        }
        artifact.with_name(filename + ".metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        artifact.with_name(filename + ".architecture.json").write_text(
            json.dumps(architecture_evidence(filename, architecture)), encoding="utf-8"
        )

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "version": "1-alpha", "artifacts": entries}),
        encoding="utf-8",
    )
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps({"release": "1-alpha", "beta_ready": False, "gates": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Quick/Best"):
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
    entries = []
    for attempt, platform, filename, commit in (
        (1, "linux", "LocalSR-v0.0.11-alpha-Linux-x86_64.AppImage", "a" * 40),
        (1, "windows", "LocalSR-v0.0.11-alpha-Windows-x86_64.exe", "a" * 40),
        (2, "macos", "LocalSR-v0.0.11-alpha-macOS-arm64.dmg", "b" * 40),
    ):
        artifact = write_release_input(tmp_path, attempt, platform, filename)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(filename + ".sha256").write_text(
            f"{digest}  {filename}\n", encoding="utf-8"
        )
        metadata = {
            "schema_version": 1,
            "artifact_filename": filename,
            "artifact_size": artifact.stat().st_size,
            "sha256": digest,
            "platform": platform,
            "architecture": "arm64" if platform == "macos" else "x86_64",
            "backend": "MPS" if platform == "macos" else "CPU",
            "repository_commit": commit,
            "github_run_id": "1",
            "run_attempt": str(attempt),
            "timestamp": "2026-09-03T12:00:00+00:00",
            "package_smoke": installed_smoke("0.0.11-alpha"),
            "signing": {"status": "sha256"},
        }
        if platform == "linux":
            metadata["live_models"] = {
                "result": "PASS",
                "models": [
                    {
                        "preset": preset,
                        "model_id": model_id,
                        "sha256": character * 64,
                        "download_bytes": 1,
                        "output_shape": [1, 3, 4, 4],
                    }
                    for preset, model_id, character in (
                        ("Quick", "span_photo_x4", "a"),
                        ("Best", "realplksr_nomoswebphoto_x4", "b"),
                    )
                ],
            }
        artifact.with_name(filename + ".metadata.json").write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
        artifact.with_name(filename + ".architecture.json").write_text(
            json.dumps(
                architecture_evidence(filename, "arm64" if platform == "macos" else "x86_64")
            ),
            encoding="utf-8",
        )
        entries.append(
            {
                "filename": filename,
                "platform": platform,
                "architecture": "arm64" if platform == "macos" else "x86_64",
                "backend": "MPS" if platform == "macos" else "CPU",
                "signing": "sha256",
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "version": "0.0.11-alpha", "artifacts": entries}),
        encoding="utf-8",
    )
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps({"release": "0.0.11-alpha", "beta_ready": False}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="mixes commits"):
        prepare_release.prepare(tmp_path, manifest, "v0.0.11-alpha", tmp_path / "out", readiness)
