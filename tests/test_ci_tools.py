from __future__ import annotations

import importlib.util
import json
import shutil
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The release under test; bump_version.py moves it, these tests follow.
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


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


macho_tree = load_script(ROOT / "scripts" / "verify_macho_tree.py")
release_version = load_script(ROOT / "scripts" / "check_release_version.py")
public_beta = load_script(ROOT / "scripts" / "check_public_beta.py")
beta_readiness = load_script(ROOT / "scripts" / "check_beta_readiness.py")


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
    assert public_beta.check(f"v{VERSION}", ROOT) == VERSION
    assert release_version.check(f"v{VERSION}", ROOT) == VERSION


@pytest.mark.parametrize("require_ready, expected_code", [(False, 0), (True, 1)])
def test_readiness_cli_uses_beta_register_and_keeps_publication_blocked(
    require_ready, expected_code
):
    command = [sys.executable, "-B", str(ROOT / "scripts/check_beta_readiness.py")]
    if require_ready:
        command.append("--require-beta-ready")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == expected_code, result.stderr
    assert f"register valid for v{VERSION}" in result.stdout
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
        "ci/public-beta-release.json",
        "ci/public-beta-readiness.json",
        "packaging/updates/production.pub",
        f"docs/releases/v{VERSION}.md",
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
    assert data["release"] == VERSION
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
