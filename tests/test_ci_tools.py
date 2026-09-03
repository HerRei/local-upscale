from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import struct
import subprocess
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
prepare_release = load_script(ROOT / "scripts" / "prepare_release_assets.py")
confirm_release = load_script(ROOT / "scripts" / "confirm_release_assets.py")
scratch = None if sys.platform == "win32" else load_script(ROOT / "scripts" / "ci_scratch.py")
macho_tree = load_script(ROOT / "scripts" / "verify_macho_tree.py")
frozen_smoke = load_script(ROOT / "scripts" / "smoke_frozen_worker.py")
release_version = load_script(ROOT / "scripts" / "check_release_version.py")
beta_readiness = load_script(ROOT / "scripts" / "check_beta_readiness.py")
runner_preflight = load_script(ROOT / "scripts" / "release_runner_preflight.py")
release_scratch = load_script(ROOT / "scripts" / "release_scratch.py")


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
    assert release_version.check("v0.0.11-alpha", ROOT) == "0.0.11-alpha"


def test_beta_readiness_register_is_valid_and_honest():
    data = beta_readiness.validate(ROOT / "ci" / "beta-readiness.json", ROOT)
    assert data["release"] == "0.0.11-alpha"
    assert data["beta_ready"] is False
    statuses = {gate["id"]: gate["status"] for gate in data["gates"]}
    assert statuses["automated-release-integrity"] == "automated-pass"
    assert statuses["macos-production-trust"] == "waiting-credentials"
    assert statuses["legacy-runtime-support"] == "decision-required"
    assert statuses["security-monitoring"] == "decision-required"
    assert statuses["video-labs"] == "labs"


def test_compact_release_assets_embed_sidecars_and_support_split_bundles(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / ".complete").write_text("{}\n", encoding="utf-8")
    specs = [
        {
            "filename": "LocalSR-Linux-CPU-x86_64.tar.gz",
            "platform": "linux",
            "architecture": "x86_64",
            "backend": "CPU",
            "main": "LocalSR/LocalSR",
        },
        {
            "filename": "LocalSR-Windows-CUDA-x86_64.zip",
            "platform": "windows",
            "architecture": "x86_64",
            "backend": "CUDA",
            "main": "LocalSR/LocalSR.exe",
        },
    ]
    for spec, payload in zip(specs, (b"small archive", b"x" * (1024**2 + 3)), strict=True):
        artifact = root / spec["platform"] / spec["filename"]
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        artifact.with_name(artifact.name + ".sha256").write_text(
            f"{digest}  {artifact.name}\n", encoding="utf-8"
        )
        artifact.with_name(artifact.name + ".metadata.json").write_text(
            json.dumps(
                {
                    "artifact_filename": artifact.name,
                    "sha256": digest,
                    "platform": spec["platform"],
                    "architecture": spec["architecture"],
                    "backend": spec["backend"],
                }
            ),
            encoding="utf-8",
        )
        artifact.with_name(artifact.name + ".architecture.txt").write_text(
            "Result: PASS\nNative binaries inspected: 1\n", encoding="utf-8"
        )

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": specs}), encoding="utf-8")
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps({"release": "0.0.9-alpha", "beta_ready": False}), encoding="utf-8"
    )
    shell_installer = tmp_path / "install.sh"
    shell_installer.write_text('#!/bin/sh\nTAG="@LOCALSR_RELEASE_TAG@"\n', encoding="utf-8")
    powershell_installer = tmp_path / "install.ps1"
    powershell_installer.write_text('$Tag = "@LOCALSR_RELEASE_TAG@"\n', encoding="utf-8")
    output = root / "release-assets"

    index_path = prepare_release.prepare(
        root,
        output=output,
        manifest=manifest,
        readiness=readiness,
        tag="v0.0.9-alpha",
        shell_installer=shell_installer,
        powershell_installer=powershell_installer,
        part_mib=1,
    )
    index = json.loads(index_path.read_text(encoding="utf-8"))

    assert index["schema_version"] == 2
    assert index["release_tag"] == "v0.0.9-alpha"
    assert index["beta_readiness"]["beta_ready"] is False
    assert len(index["assets"]) == 7
    assert index["bundles"][0]["architecture_report"].startswith("Result: PASS")
    assert index["bundles"][0]["metadata"]["artifact_filename"] == specs[0]["filename"]
    assert index["bundles"][0]["assets"] == [specs[0]["filename"]]
    assert index["bundles"][1]["assets"] == [
        specs[1]["filename"] + ".part-0001",
        specs[1]["filename"] + ".part-0002",
    ]
    assert "@LOCALSR_RELEASE_TAG@" not in (output / "Install-LocalSR.sh").read_text()
    assert "v0.0.9-alpha" in (output / "Install-LocalSR.ps1").read_text()
    published_names = {item["filename"] for item in index["assets"]}
    assert not any(
        name.endswith((".sha256", ".metadata.json", ".architecture.txt", ".parts.json"))
        for name in published_names
    )
    checksum_lines = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert len(checksum_lines) == 5
    assert all(len(line.split("  ", 1)[0]) == 64 for line in checksum_lines)
    upload_names = [
        Path(line).name for line in (output / "release-files.txt").read_text().splitlines()
    ]
    assert upload_names[:4] == [
        "Install-LocalSR.sh",
        "Install-LocalSR.ps1",
        "SHA256SUMS",
        "release-index.json",
    ]


def test_release_asset_confirmation_requires_exact_set():
    index = {
        "assets": [
            {"filename": "Install-LocalSR.sh", "size": 100},
            {"filename": "release-index.json", "size": None},
        ]
    }
    release = {
        "assets": [
            {"name": "Install-LocalSR.sh", "size": 99},
            {"name": "release-index.json", "size": 250},
            {"name": "old.sha256", "size": 80},
        ]
    }

    assert confirm_release.compare(index, release) == {
        "missing": [],
        "wrong_size": ["Install-LocalSR.sh"],
        "wrong_digest": [],
        "unexpected": ["old.sha256"],
    }


def test_release_workflow_publishes_compact_verified_asset_set():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    publish = workflow.split("  publish-release:", 1)[1].split("  retention-maintenance:", 1)[0]
    assert '--tag "$GITHUB_REF_NAME"' in publish
    assert "--shell-installer install.sh" in publish
    assert "--powershell-installer install.ps1" in publish
    assert "--allow-unexpected" in publish
    assert "--unexpected-output" in publish
    assert 'gh release delete-asset "$TAG" "$stale_asset" --yes' in publish
    assert publish.count("scripts/confirm_release_assets.py") == 2


def test_release_installer_templates_have_one_tag_marker_and_compact_manifest_support():
    shell = (ROOT / "install.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
    for contents in (shell, powershell):
        assert contents.count("@LOCALSR_RELEASE_TAG@") == 1
        assert "release-index.json" in contents
        assert "SHA256SUMS" in contents
        assert "Assembled bundle checksum verification failed" in contents


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell parser is provided by Windows")
def test_windows_release_installer_parses():
    path = str(ROOT / "install.ps1").replace("'", "''")
    command = (
        "$tokens=$null; $errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -ne 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
    )


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


@pytest.mark.skipif(sys.platform == "win32", reason="SSD scratch management uses POSIX locks")
def test_scratch_prune_removes_only_allowed_active_run_components(tmp_path: Path):
    assert scratch is not None
    root = tmp_path / "scratch"
    scratch.managed_root(root, initialize=True)
    directory = root / "runs" / "123" / "1" / "linux" / "AMD-ROCm"
    (directory / "environment").mkdir(parents=True)
    (directory / "build").mkdir()
    (directory / "staging").mkdir()
    (directory / ".active").write_text("{}", encoding="utf-8")
    args = scratch.build_parser().parse_args(
        [
            "prune",
            "--root",
            str(root),
            "--run-id",
            "123",
            "--attempt",
            "1",
            "--platform",
            "linux",
            "--flavor",
            "AMD-ROCm",
            "--component",
            "environment",
            "--component",
            "build",
        ]
    )
    args.func(args)
    assert not (directory / "environment").exists()
    assert not (directory / "build").exists()
    assert (directory / "staging").is_dir()
    assert (directory / ".active").is_file()


def test_linux_release_bounds_cache_before_pruning_build_inputs():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    linux_job = workflow.split("  build-linux-flavors:", 1)[1].split("  build-windows-flavors:", 1)[
        0
    ]
    cache_trim = "ci_scratch.py cleanup --root /ci-scratch --cache-max-gib 2"
    prune = "ci_scratch.py prune"
    assert cache_trim in linux_job
    assert prune in linux_job
    assert linux_job.index(cache_trim) < linux_job.index(prune)


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


def test_release_runner_preflight_validates_receiver_shape_and_reserve():
    healthy = {
        "status": "ok",
        "free_bytes": 500,
        "free_percent": 40.0,
        "timestamp": "2026-09-03T00:00:00+00:00",
    }
    assert (
        runner_preflight.validate_health(healthy, minimum_free_bytes=400, minimum_free_percent=20)
        == healthy
    )
    with pytest.raises(RuntimeError, match="reserve is too low"):
        runner_preflight.validate_health(healthy, minimum_free_bytes=600, minimum_free_percent=20)
    with pytest.raises(ValueError, match="free_percent"):
        runner_preflight.validate_health(
            healthy | {"free_percent": "unknown"},
            minimum_free_bytes=1,
            minimum_free_percent=1,
        )


def test_release_runner_preflight_rejects_missing_tools_and_noncanonical_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner_preflight.shutil, "disk_usage", lambda _path: (1000, 100, 900))
    monkeypatch.setattr(
        runner_preflight.shutil,
        "which",
        lambda name: "/usr/bin/python3" if name == "python3" else None,
    )
    assert (
        runner_preflight.validate_runner("linux", tmp_path, 800, ["python3"])["platform"] == "linux"
    )
    with pytest.raises(RuntimeError, match="missing required tools: cargo"):
        runner_preflight.validate_runner("linux", tmp_path, 800, ["cargo"])
    with pytest.raises(ValueError, match="unsupported release platform"):
        runner_preflight.validate_runner("tauri-linux", tmp_path, 1, [])


def test_release_scratch_cleans_success_and_retains_failures_for_48_hours(
    tmp_path: Path,
):
    root = tmp_path / "ci-scratch"
    successful = release_scratch.start(root, "12", "1", "linux", "alpha")
    (successful / "staging" / "report.json").write_text("{}", encoding="utf-8")
    assert release_scratch.finish(successful, root, "success") is None
    assert not successful.exists()

    failed = release_scratch.start(root, "12", "2", "windows", "alpha")
    (failed / "release-certificate.pfx").write_bytes(b"secret")
    retained = release_scratch.finish(
        failed,
        root,
        "failure",
        secrets=["release-certificate.pfx"],
    )
    assert retained == failed
    assert not (failed / "release-certificate.pfx").exists()
    marker = json.loads((failed / ".retained.json").read_text(encoding="utf-8"))
    assert marker["status"] == "failure"
    assert marker["contains_secrets"] is False
    assert marker["expires_at"] > marker["retained_at"]


def test_release_scratch_rejects_broad_and_unowned_cleanup_targets(tmp_path: Path):
    with pytest.raises(ValueError, match="unmanaged"):
        release_scratch.managed_root(tmp_path, initialize=True)
    root = tmp_path / "ci-scratch"
    target = release_scratch.start(root, "9", "1", "linux", "alpha")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    with pytest.raises(ValueError, match="outside managed runs"):
        release_scratch.finish(outside, root, "success")
    (target / release_scratch.RUN_MARKER).unlink()
    with pytest.raises(ValueError, match="ownership marker"):
        release_scratch.finish(target, root, "success")


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
