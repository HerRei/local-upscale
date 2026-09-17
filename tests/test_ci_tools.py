from __future__ import annotations

import importlib.util
import json
import shutil
import struct
import subprocess
import sys
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
    # Match `python scripts/tool.py` without relying on another test module
    # to leave the scripts directory on the global import path.
    original_path = sys.path[:]
    try:
        sys.path.insert(0, str(path.parent))
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    return module


preflight = load_script(ROOT / "scripts" / "storage_preflight.py")
artifact_server = load_script(ROOT / "scripts" / "ci_artifact_server.py")
artifact_auth = load_script(ROOT / "scripts" / "artifact_auth.py")
artifact_upload = load_script(ROOT / "scripts" / "upload_artifacts.py")
maintenance = load_script(ROOT / "scripts" / "ci_artifact_maintenance.py")
scratch = None if sys.platform == "win32" else load_script(ROOT / "scripts" / "ci_scratch.py")
macho_tree = load_script(ROOT / "scripts" / "verify_macho_tree.py")
release_version = load_script(ROOT / "scripts" / "check_release_version.py")
public_beta = load_script(ROOT / "scripts" / "check_public_beta.py")
beta_readiness = load_script(ROOT / "scripts" / "check_beta_readiness.py")
runner_preflight = load_script(ROOT / "scripts" / "release_runner_preflight.py")
release_scratch = load_script(ROOT / "scripts" / "release_scratch.py")


def thin_macho(cpu: int) -> bytes:
    return b"\xcf\xfa\xed\xfe" + struct.pack("<I", cpu) + bytes(64)


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


def test_release_metadata_is_synchronized():
    assert public_beta.check("v0.1.3-beta", ROOT) == "0.1.3-beta"
    assert release_version.check("v0.1.3-beta", ROOT) == "0.1.3-beta"


@pytest.mark.parametrize("require_ready, expected_code", [(False, 0), (True, 1)])
def test_readiness_cli_uses_beta_register_and_keeps_publication_blocked(
    require_ready, expected_code
):
    command = [sys.executable, "-B", str(ROOT / "scripts/check_beta_readiness.py")]
    if require_ready:
        command.append("--require-beta-ready")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == expected_code, result.stderr
    assert "register valid for v0.1.3-beta" in result.stdout
    assert "publication-and-certification" in result.stdout


def copy_release_metadata(destination: Path) -> None:
    paths = [
        "pyproject.toml",
        "src/localsr/__init__.py",
        "README.md",
        "CHANGELOG.md",
        "docs/releasing.md",
        "desktop/package.json",
        "desktop/package-lock.json",
        "desktop/src-tauri/Cargo.toml",
        "desktop/src-tauri/Cargo.lock",
        "desktop/src-tauri/tauri.conf.json",
        "ci/tauri-targets.json",
        "ci/tauri-release-artifacts.json",
        "ci/beta-readiness.json",
        "ci/public-beta-release.json",
        "ci/public-beta-readiness.json",
        "packaging/updates/production.pub",
        "docs/releases/v0.1.3-beta.md",
        ".github/workflows/desktop-release.yml",
    ]
    for relative in paths:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)


def test_public_beta_rejects_wrong_tag_missing_backend_and_stale_lock(tmp_path):
    copy_release_metadata(tmp_path)
    with pytest.raises(ValueError, match="tag"):
        public_beta.check("v0.0.12-alpha", tmp_path)
    plan_path = tmp_path / "ci/public-beta-release.json"
    original = plan_path.read_text()
    plan = json.loads(original)
    plan["targets"].pop()
    plan_path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="every registered backend"):
        public_beta.check(root=tmp_path)
    plan_path.write_text(original)
    lock_path = tmp_path / "desktop/package-lock.json"
    lock = json.loads(lock_path.read_text())
    lock["packages"][""]["version"] = "0.0.12-alpha"
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="versions disagree"):
        public_beta.check(root=tmp_path)


def test_beta_readiness_register_is_valid_and_honest():
    data = beta_readiness.validate(ROOT / "ci" / "public-beta-readiness.json", ROOT)
    assert data["release"] == "0.1.3-beta"
    assert data["beta_ready"] is False
    statuses = {gate["id"]: gate["status"] for gate in data["gates"]}
    assert statuses["batch-rendering-and-comparison"] == "manual-pass"
    assert statuses["publication-and-certification"] == "decision-required"
    assert statuses["hdr-and-temporal-quality"] == "labs"
    assert not next(gate for gate in data["gates"] if gate["id"] == "hdr-and-temporal-quality")[
        "blocking"
    ]


def test_optional_labs_do_not_block_an_otherwise_ready_release(tmp_path):
    data = json.loads((ROOT / "ci/public-beta-readiness.json").read_text())
    for gate in data["gates"]:
        if gate["blocking"]:
            gate["status"] = "automated-pass"
    data["beta_ready"] = True
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps(data))
    assert beta_readiness.validate(path, ROOT)["beta_ready"]
    optional_lab = next(
        gate for gate in data["gates"] if not gate["blocking"] and gate["status"] == "labs"
    )
    optional_lab["blocking"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="disagrees"):
        beta_readiness.validate(path, ROOT)


def test_manual_acceptance_requires_evidence(tmp_path):
    data = json.loads((ROOT / "ci/public-beta-readiness.json").read_text())
    data["gates"][0]["status"] = "manual-pass"
    data["gates"][0].pop("evidence", None)
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="evidence"):
        beta_readiness.validate(path, ROOT)
    data["gates"][0]["evidence"] = "docs/acceptance/local-run.json"
    path.write_text(json.dumps(data))
    beta_readiness.validate(path, ROOT)


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


def test_macos_signing_secrets_are_not_job_scoped():
    workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
    macos_job = workflow.split("  build-macos-arm64:", 1)[1].split("  verify-release-matrix:", 1)[0]
    job_configuration = macos_job.split("    steps:", 1)[0]
    signing_step = macos_job.split(
        "      - name: Build, sign, notarize, staple, and smoke-test the DMG", 1
    )[1].split("      - name:", 1)[0]
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
        assert f"secrets.{name}" in signing_step


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


def test_release_scratch_can_force_clean_unretained_marked_runs(tmp_path: Path):
    root = tmp_path / "ci-scratch"
    interrupted = release_scratch.start(root, "12", "1", "windows", "cuda")
    (interrupted / "build" / "large.bin").write_bytes(b"x")

    assert release_scratch.cleanup(root, force=True) == []
    assert interrupted.exists()

    removed = release_scratch.cleanup(root, force=True, include_unretained=True)

    assert removed == [interrupted]
    assert not interrupted.exists()


def test_release_scratch_can_clean_legacy_numeric_run_directories(tmp_path: Path):
    root = tmp_path / "ci-scratch"
    release_scratch.managed_root(root, initialize=True)
    legacy = root / "34123456789"
    legacy.mkdir()
    (legacy / "large.bin").write_bytes(b"x")
    named_cache = root / "wheelhouses"
    named_cache.mkdir()
    nested_numeric = root / "r" / "34123456789"
    nested_numeric.mkdir(parents=True)

    removed = release_scratch.cleanup(root, include_legacy_runs=True)

    assert removed == [legacy]
    assert not legacy.exists()
    assert named_cache.exists()
    assert nested_numeric.exists()


def test_release_scratch_can_force_clean_orphaned_markerless_run_directories(tmp_path: Path):
    root = tmp_path / "ci-scratch"
    marked = release_scratch.start(root, "12", "1", "windows", "cuda")
    orphan = root / "r" / "11"
    orphan.mkdir(parents=True)
    (orphan / "large.bin").write_bytes(b"x")

    with pytest.raises(ValueError, match="requires --force"):
        release_scratch.cleanup(root, include_orphaned_runs=True)

    removed = release_scratch.cleanup(root, force=True, include_orphaned_runs=True)

    assert removed == [orphan]
    assert not orphan.exists()
    assert marked.exists()


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
