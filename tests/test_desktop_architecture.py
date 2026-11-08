from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_desktop_architecture", ROOT / "scripts" / "check_desktop_architecture.py"
)
assert SPEC and SPEC.loader
architecture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(architecture)


def test_repository_preserves_the_additive_desktop_boundary() -> None:
    assert architecture.collect_violations(ROOT) == []


def test_rejects_native_authority_inside_the_webview() -> None:
    violations = architecture.webview_authority_violations(
        {
            "dependencies": {
                "@tauri-apps/plugin-shell": "2.0.0",
                "@tauri-apps/plugin-fs": "2.0.0",
            }
        },
        {"permissions": ["core:default", "shell:allow-execute", "fs:allow-home-read"]},
        {
            "desktop/src/unsafe.ts": (
                "import { Command } from '@tauri-apps/plugin-shell';\n"
                "export const models = fetch('https://example.invalid/catalog');\n"
            )
        },
    )

    assert any("native-authority plugins" in violation for violation in violations)
    assert any("filesystem permissions" in violation for violation in violations)
    assert any("direct network access" in violation for violation in violations)
    assert any("Tauri shell API" in violation for violation in violations)


def test_rejects_preview_release_coupling() -> None:
    violations = architecture.workflow_isolation_violations(
        "permissions:\n  contents: write\nsteps:\n  - run: gh release create v1\n",
    )

    assert any("release-write" in violation for violation in violations)
    assert any("publish GitHub releases" in violation for violation in violations)
    assert any("explicit manual build" in violation for violation in violations)


RELEASE_WORKFLOW = """name: Release packages
on:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-22.04
    steps:
      - run: |
          python -m pip check
          python scripts/verify_codec_allowlist.py
          python scripts/smoke_tauri_installer.py
          python scripts/verify_tauri_release_package.py
          python scripts/verify_nvidia_redistributables.py
          python scripts/build_source_bundle.py
  attach:
    runs-on: ubuntu-24.04
    permissions:
      contents: write
"""


def test_accepts_a_verified_manual_release_workflow() -> None:
    assert architecture.release_package_violations("w.yml", RELEASE_WORKFLOW, {}) == []


def test_rejects_unverified_or_automatic_release_workflows() -> None:
    weakened = (
        RELEASE_WORKFLOW.replace("  workflow_dispatch:", "  workflow_dispatch:\n  push:")
        .replace("permissions:\n  contents: read", "permissions:\n  contents: write")
        .replace("          python scripts/smoke_tauri_installer.py\n", "")
        .replace("python -m pip check", "python build.py --private-preview-media")
    )
    violations = architecture.release_package_violations(
        "w.yml", weakened, {"glibc ceiling": "verify_appimage_libc.py"}
    )

    assert any("must start only by hand" in violation for violation in violations)
    assert any("read-only contents" in violation for violation in violations)
    assert any("only the attach job" in violation for violation in violations)
    assert any("installed package smoke" in violation for violation in violations)
    assert any("consistency check" in violation for violation in violations)
    assert any("glibc ceiling" in violation for violation in violations)
    assert any("private preview media" in violation for violation in violations)


@pytest.mark.parametrize(
    "trigger",
    ["  push:\n", '  "push":\n', "  repository_dispatch:\n", "  pull_request_target:\n"],
)
def test_release_workflows_start_only_by_hand(trigger: str) -> None:
    automatic = RELEASE_WORKFLOW.replace(
        "  workflow_dispatch:\n", "  workflow_dispatch:\n" + trigger
    )
    violations = architecture.release_package_violations("w.yml", automatic, {})

    assert any("must start only by hand" in violation for violation in violations)


def test_only_the_attach_job_may_write_release_contents() -> None:
    moved = RELEASE_WORKFLOW.replace("    permissions:\n      contents: write\n", "").replace(
        "    runs-on: ubuntu-22.04\n",
        "    runs-on: ubuntu-22.04\n    permissions:\n      contents: write\n",
    )
    broad = RELEASE_WORKFLOW.replace(
        "    runs-on: ubuntu-22.04\n", "    runs-on: ubuntu-22.04\n    permissions: write-all\n"
    )
    for workflow in (moved, broad):
        violations = architecture.release_package_violations("w.yml", workflow, {})
        assert any("only the attach job" in violation for violation in violations)


def test_comments_neither_satisfy_nor_trip_a_release_gate() -> None:
    commented = RELEASE_WORKFLOW.replace(
        "          python scripts/smoke_tauri_installer.py\n",
        "          # python scripts/smoke_tauri_installer.py --private-preview-media\n",
    )
    violations = architecture.release_package_violations("w.yml", commented, {})

    assert violations == ["w.yml is missing the installed package smoke"]


def test_rejects_an_unsigned_or_unnotarized_macos_release() -> None:
    script = (ROOT / "scripts" / "release_macos_beta.sh").read_text(encoding="utf-8")
    assert architecture.macos_release_violations(script) == []

    unsigned = script.replace("xcrun notarytool submit", "true").replace(
        "python scripts/sign_macos_keychain.py", "# python scripts/sign_macos_keychain.py"
    )
    violations = architecture.macos_release_violations(unsigned)
    assert any("Developer ID signing" in violation for violation in violations)
    assert any("notarization" in violation for violation in violations)


def test_rejects_workflows_on_self_hosted_runners(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "hosted.yml").write_text(
        "# was self-hosted\njobs:\n  a:\n    runs-on: macos-latest\n"
    )
    (workflows / "local.yml").write_text("jobs:\n  a:\n    runs-on: [self-hosted, Linux]\n")
    (workflows / "other.yaml").write_text("jobs:\n  a:\n    runs-on: [self-hosted, linux]\n")

    assert architecture.hosted_runner_violations(tmp_path) == [
        ".github/workflows/local.yml targets a self-hosted runner",
        ".github/workflows/other.yaml targets a self-hosted runner",
    ]


def test_release_workflows_install_the_pinned_face_extra() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        extras = tomllib.load(stream)["project"]["optional-dependencies"]
    assert extras["face"] == ["opencv-python-headless==4.10.0.84"]

    for workflow_name in ("linux-appimages.yml", "windows-installers.yml", "tauri-preview.yml"):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text()
        assert ".[package,video,face]" in workflow or "backend_wheelhouse.py" in workflow


def test_desktop_catalog_fails_closed_for_unresolved_checkpoint_rights() -> None:
    manifest = json.loads(
        (ROOT / "desktop/src-tauri/resources/model-catalog.json").read_text(encoding="utf-8")
    )
    models = {model["model_id"]: model for model in manifest["models"]}

    for model_id in ("hat_s_x4_face", "hat_l_x4_face"):
        face = models[model_id]
        assert face["automated_download_allowed"] is False
        assert face["commercial_use_allowed"] is None
        assert face["terms_acceptance_required"] is True
        assert face["support_tier"] == "labs"
        primary = models[face["pair_with"]]
        assert primary["pair_with"] == face["model_id"]
        assert primary["native_scale"] == face["native_scale"]
        assert "face" not in primary["purposes"]

    for model_id in ("realplksr_hfa2k_anime_x4", "realplksr_nomoswebphoto_x4"):
        assert models[model_id]["automated_download_allowed"] is True
        assert models[model_id]["terms_acceptance_required"] is False
        assert models[model_id]["commercial_use_allowed"] is True
        assert models[model_id]["license_name"] == "CC BY 4.0"
        assert models[model_id]["attribution_required"] is True
        assert models[model_id]["redistribution_allowed"] is True
        assert models[model_id]["support_tier"] == "labs"


def test_tauri_preview_keeps_cargo_output_in_the_ephemeral_workspace() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text()

    assert "CARGO_TARGET_DIR: ${{ github.workspace }}/desktop/src-tauri/target" in workflow
    assert "/mnt/hdd" not in workflow
    assert "CARGO_CACHE" not in workflow


def test_hosted_pr_workflows_run_in_parallel_on_fresh_runners() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    preview = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text()

    # CI and Preview may cancel only older runs of themselves.
    assert "group: localsr-ci-${{ github.ref }}" in ci
    assert "group: localsr-tauri-${{ github.ref }}" in preview
    for workflow in (ci, preview):
        assert "self-hosted" not in workflow
        assert "--system-site-packages" not in workflow

    assert "needs:" not in ci
    assert ci.count("runs-on: ubuntu-24.04") == 2
    assert "runs-on: windows-2022" in ci
    assert "runs-on: macos-latest" in ci
    assert preview.count("needs:") == 1
    assert "needs: verify" in preview
    assert "max-parallel:" not in preview
    assert preview.count("runner: '[\"ubuntu-24.04\"]'") == 2
    assert preview.count("runner: '[\"windows-2022\"]'") == 2
    assert "runner: '[\"macos-latest\"]'" in preview


def test_hosted_lint_checks_workflows_in_the_checkout() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "rsync" not in ci
    assert "-config-file .github/actionlint.yaml" in ci
    assert ".github/workflows/*.yml" in ci


def test_rust_and_npm_forbidden_plugin_names_cover_both_ecosystems() -> None:
    assert "@tauri-apps/plugin-shell" in architecture.FORBIDDEN_WEBVIEW_PACKAGES
    assert "tauri-plugin-shell" in architecture.FORBIDDEN_TAURI_PLUGINS
    assert architecture.FORBIDDEN_WEBVIEW_PACKAGES.isdisjoint(architecture.FORBIDDEN_TAURI_PLUGINS)
