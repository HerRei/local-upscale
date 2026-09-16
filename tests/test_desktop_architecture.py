from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

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


def test_rejects_unsigned_or_overwriting_desktop_release() -> None:
    violations = architecture.signed_release_violations(
        "permissions:\n  contents: write\n  contents: write\nrun: gh release upload --clobber\n",
    )

    assert any("fail-closed Tauri signing" in violation for violation in violations)
    assert any("must not overwrite" in violation for violation in violations)
    assert any("only the signed desktop publishing job" in violation for violation in violations)


def test_every_workflow_upload_uses_the_canonical_platform_contract() -> None:
    assert architecture.workflow_upload_platform_violations(ROOT) == []


def test_publish_jobs_combine_attempts_and_use_the_provisioned_python() -> None:
    workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text()

    assert 'RUN_ROOT="/mnt/hdd/ci-artifacts/$GITHUB_RUN_ID"' in workflow
    assert '"$HOME/.venv-ci/bin/python3.11" scripts/prepare_tauri_release_assets.py' in workflow
    assert 'ROOT="/mnt/hdd/ci-artifacts/$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT"' not in workflow
    assert "\n          python scripts/prepare_tauri_release_assets.py" not in workflow


def test_release_workflows_install_the_pinned_face_extra() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        extras = tomllib.load(stream)["project"]["optional-dependencies"]
    assert extras["face"] == ["opencv-python-headless==4.10.0.84"]

    for workflow_name in ("desktop-release.yml", "tauri-preview.yml"):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text()
        assert ".[package,video,face]" in workflow or "backend_wheelhouse.py" in workflow


def test_signed_release_uses_bounded_release_retention_on_every_platform() -> None:
    workflow = (ROOT / ".github/workflows/desktop-release.yml").read_text()

    assert "ci_scratch.py" not in workflow
    # Linux and Windows build on persistent self-hosted runners, so their
    # release scratch needs explicit, bounded retention.
    assert workflow.count("release_scratch.py start") == 2
    assert workflow.count("release_scratch.py finish") == 2
    assert workflow.count("--retain-hours 48") == 2
    assert "--platform linux" in workflow
    assert "--platform windows" in workflow
    assert "--platform macos" in workflow
    # macOS builds on an ephemeral GitHub-hosted runner that is discarded with
    # the job, so its scratch is bounded by construction and must stay under
    # RUNNER_TEMP rather than persistent storage.
    macos_job = workflow.split("  build-macos-arm64:", 1)[1].split("  verify-release-matrix:", 1)[0]
    assert "runs-on: macos-latest" in macos_job
    assert "release_scratch.py" not in macos_job
    assert "$RUNNER_TEMP" in macos_job
    assert "/Volumes/CISCRATCH" not in macos_job


def test_release_builds_revalidate_runner_health_immediately_before_work() -> None:
    workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text()

    assert workflow.count("Revalidate release runner immediately before build") == 3
    assert workflow.count("scripts/release_runner_preflight.py") >= 6


def test_signed_windows_cleanup_survives_thumbprint_validation_failure() -> None:
    workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text()
    persisted = workflow.index('"LOCALSR_IMPORTED_CERTIFICATE=$($certificate.Thumbprint)"')
    thumbprint_check = workflow.index("Imported certificate thumbprint mismatch")
    cleanup = workflow.index("Cert:\\CurrentUser\\My\\$env:LOCALSR_IMPORTED_CERTIFICATE")

    assert persisted < thumbprint_check < cleanup
    assert "--secret release-certificate.pfx" in workflow


def test_signed_macos_gate_fails_closed_without_an_unregistered_runner_label() -> None:
    workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text()

    assert "[self-hosted, macOS, ARM64]" not in workflow
    assert '["self-hosted","macOS","ARM64"]' not in workflow
    assert workflow.count("A registered native macOS ARM64 runner is required") >= 3
    # The signed macOS preflight and build must route to a label that always
    # has capacity, or a release would queue forever. GitHub-hosted
    # macos-latest is genuinely arm64 and never lacks a runner, so no
    # self-hosted macOS label may remain in the release workflow.
    assert workflow.count("macos-latest") >= 2
    assert "self-hosted, macOS" not in workflow
    assert '"self-hosted","macOS"' not in workflow


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


def test_tauri_preview_keeps_the_linux_cargo_cache_off_the_small_root_ssd() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text()

    assert "CARGO_CACHE=/mnt/hdd/ci-cache/localsr-tauri-target/linux-x64" in workflow
    assert 'mkdir -p "$CARGO_CACHE"' in workflow
    assert 'echo "CARGO_TARGET_DIR=$CARGO_CACHE" >> "$GITHUB_ENV"' in workflow


def test_macmini_heavy_workflows_and_preview_guests_are_serialized() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    preview = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text()
    release = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text()

    # CI and Preview may cancel only older runs of themselves. They must not
    # share GitHub's one-pending-run concurrency queue with a release.
    assert "group: localsr-ci-${{ github.ref }}" in ci
    assert "group: localsr-tauri-${{ github.ref }}" in preview
    assert "group: localsr-macmini-heavy" in release
    assert "cancel-in-progress: false" in release

    assert "name: Signed Tauri Release\n" in release
    for workflow in (ci, preview):
        assert '--blocked-workflow "Signed Tauri Release"' in workflow
    assert '--blocked-workflow "Tauri Next Preview"' in ci
    assert '--ignore-head-sha "${{ github.event.pull_request.head.sha || github.sha }}"' in ci
    assert ci.count("--only-older-runs") == 2
    assert '--blocked-workflow "CI"' in preview
    assert preview.count("--only-older-runs") == 2
    assert (
        '--always-block-head-sha "${{ github.event.pull_request.head.sha || github.sha }}"'
        in preview
    )
    assert "runs-on: ubuntu-latest" in ci
    assert "runs-on: ubuntu-latest" in preview
    assert "runs-on: ubuntu-latest" in release
    assert preview.count("max-parallel: 1") == 2


def test_isolated_workflow_copies_preserve_github_for_actionlint() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "--exclude .git " not in ci
    assert "--exclude '/.git/'" in ci
    assert "-config-file .github/actionlint.yaml" in ci
    assert ".github/workflows/*.yml" in ci


def test_rust_and_npm_forbidden_plugin_names_cover_both_ecosystems() -> None:
    assert "@tauri-apps/plugin-shell" in architecture.FORBIDDEN_WEBVIEW_PACKAGES
    assert "tauri-plugin-shell" in architecture.FORBIDDEN_TAURI_PLUGINS
    assert architecture.FORBIDDEN_WEBVIEW_PACKAGES.isdisjoint(architecture.FORBIDDEN_TAURI_PLUGINS)
