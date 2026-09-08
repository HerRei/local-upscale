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
        "name: Build & Release\nsteps:\n  - run: python scripts/build_tauri_preview.py\n",
        "permissions:\n  contents: write\nsteps:\n  - run: gh release create v1\n",
    )

    assert any("release.yml" in violation for violation in violations)
    assert any("release-write" in violation for violation in violations)
    assert any("publish GitHub releases" in violation for violation in violations)
    assert any("explicit manual build" in violation for violation in violations)


def test_rejects_unsigned_or_overwriting_desktop_release() -> None:
    violations = architecture.signed_release_violations(
        "on:\n  push:\n    tags:\n",
        "permissions:\n  contents: write\n  contents: write\nrun: gh release upload --clobber\n",
    )

    assert any("legacy Slint" in violation for violation in violations)
    assert any("fail-closed Tauri signing" in violation for violation in violations)
    assert any("must not overwrite" in violation for violation in violations)
    assert any("only the signed desktop publishing job" in violation for violation in violations)


def test_cross_alpha_release_must_remain_exact_and_non_overwriting() -> None:
    violations = architecture.cross_alpha_release_violations(
        "on:\n  push:\n    tags:\n      - v*\npermissions:\n  contents: write\n"
        "  contents: write\nrun: gh release upload --clobber\n"
    )

    assert any("exact v0.0.12 tag" in violation for violation in violations)
    assert any("must not overwrite" in violation for violation in violations)
    assert any("only the v0.0.12" in violation for violation in violations)


def test_every_workflow_upload_uses_the_canonical_platform_contract() -> None:
    assert architecture.workflow_upload_platform_violations(ROOT) == []


def test_publish_jobs_combine_attempts_and_use_the_provisioned_python() -> None:
    for workflow_name in ("desktop-release.yml", "v0.0.12-cross-alpha.yml"):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()

        assert 'RUN_ROOT="/mnt/hdd/ci-artifacts/$GITHUB_RUN_ID"' in workflow
        assert '"$HOME/.venv-ci/bin/python3.11" scripts/prepare_tauri_release_assets.py' in workflow
        assert 'ROOT="/mnt/hdd/ci-artifacts/$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT"' not in workflow
        assert "\n          python scripts/prepare_tauri_release_assets.py" not in workflow


def test_v0012_tag_has_one_exclusive_unsigned_publisher() -> None:
    signed = (ROOT / ".github/workflows/desktop-release.yml").read_text()
    cross = (ROOT / ".github/workflows/v0.0.12-cross-alpha.yml").read_text()

    assert '      - "v0.0.12-alpha"' in cross
    assert "github.ref == 'refs/tags/v0.0.12-alpha'" in cross
    assert '      - "!v0.0.12-alpha"' in signed
    assert signed.count("github.ref_name != 'v0.0.12-alpha'") >= 4
    assert "v0.0.12-alpha" not in (ROOT / ".github/workflows/release.yml").read_text()


def test_cross_build_pair_is_exact_and_excluded_from_dependabot() -> None:
    requirements = (ROOT / "requirements/macos-cross-v0.0.12-alpha.txt").read_text()
    dependabot = (ROOT / ".github/dependabot.yml").read_text()

    assert requirements.count("torch==2.2.2") == 1
    assert requirements.count("torchvision==0.17.2") == 1
    assert requirements.count("opencv-python-headless==4.9.0.80") == 1
    assert "v0.0.12-alpha" in requirements
    assert '"requirements/macos-cross-v0.0.12-alpha.txt"' in dependabot

    with (ROOT / "pyproject.toml").open("rb") as stream:
        extras = tomllib.load(stream)["project"]["optional-dependencies"]
    assert extras["face"] == ["opencv-python-headless==4.10.0.84"]

    for workflow_name in (
        "desktop-release.yml",
        "tauri-preview.yml",
        "v0.0.12-cross-alpha.yml",
    ):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text()
        assert ".[package,video,face]" in workflow or "backend_wheelhouse.py" in workflow


def test_cross_alpha_uses_bounded_release_retention_on_every_platform() -> None:
    workflow = (ROOT / ".github/workflows/v0.0.12-cross-alpha.yml").read_text()

    assert "ci_scratch.py" not in workflow
    assert workflow.count("release_scratch.py start") == 3
    assert workflow.count("release_scratch.py finish") == 3
    assert workflow.count("--retain-hours 48") == 3
    assert "--platform linux" in workflow
    assert "--platform windows" in workflow
    assert "--platform macos" in workflow


def test_release_builds_revalidate_runner_health_immediately_before_work() -> None:
    for workflow_name in ("desktop-release.yml", "v0.0.12-cross-alpha.yml"):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()

        assert workflow.count("Revalidate release runner immediately before build") == 3
        assert workflow.count("scripts/release_runner_preflight.py") >= 6


def test_cross_alpha_windows_preflight_cleans_managed_scratch_before_reserve_check() -> None:
    workflow = (ROOT / ".github" / "workflows" / "v0.0.12-cross-alpha.yml").read_text()
    preflight = workflow[
        workflow.index("Verify Windows receiver, storage, VC runtime, and tools") : workflow.index(
            "Verify macOS receiver, storage, and tools"
        )
    ]

    cleanup = preflight.index("release_scratch.py cleanup --root C:\\lsr-ci")
    reserve_check = preflight.index("ensure_windows_scratch.ps1 -MinimumFreeGiB 20")

    assert "--include-unretained" in preflight
    assert cleanup < reserve_check


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
    assert workflow.count("localsr-macos-cross-builder") >= 2


def test_desktop_catalog_fails_closed_for_unresolved_checkpoint_rights() -> None:
    manifest = json.loads(
        (ROOT / "desktop/src-tauri/resources/model-catalog.json").read_text(encoding="utf-8")
    )
    models = {model["model_id"]: model for model in manifest["models"]}

    face = models["hat_s_x4_face"]
    assert face["automated_download_allowed"] is False
    assert face["commercial_use_allowed"] is None
    assert face["terms_acceptance_required"] is True
    assert face["support_tier"] == "labs"

    for model_id in ("realplksr_hfa2k_anime_x4", "realplksr_nomoswebphoto_x4"):
        assert models[model_id]["automated_download_allowed"] is False
        assert models[model_id]["commercial_use_allowed"] is None


def test_tauri_preview_keeps_the_linux_cargo_cache_off_the_small_root_ssd() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text()

    assert "CARGO_CACHE=/mnt/hdd/ci-cache/localsr-tauri-target/linux-x64" in workflow
    assert 'mkdir -p "$CARGO_CACHE"' in workflow
    assert 'echo "CARGO_TARGET_DIR=$CARGO_CACHE" >> "$GITHUB_ENV"' in workflow


def test_macmini_heavy_workflows_and_preview_guests_are_serialized() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    preview = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text()
    cross = (ROOT / ".github" / "workflows" / "v0.0.12-cross-alpha.yml").read_text()

    # CI and Preview may cancel only older runs of themselves. They must not
    # share GitHub's one-pending-run concurrency queue with a release.
    assert "group: localsr-ci-${{ github.ref }}" in ci
    assert "group: localsr-tauri-${{ github.ref }}" in preview
    assert "group: localsr-macmini-heavy" in cross
    assert "cancel-in-progress: false" in cross

    assert '--blocked-workflow "v0.0.12 Mac mini Cross Alpha"' in ci
    assert '--blocked-workflow "Tauri Next Preview"' in ci
    assert '--ignore-head-sha "${{ github.event.pull_request.head.sha || github.sha }}"' in ci
    assert ci.count("--only-older-runs") == 2
    assert '--blocked-workflow "CI"' in preview
    assert preview.count("--only-older-runs") == 2
    assert (
        '--always-block-head-sha "${{ github.event.pull_request.head.sha || github.sha }}"'
        in preview
    )
    assert '--blocked-workflow "CI"' in cross
    assert "--mode fail" in cross
    assert "runs-on: ubuntu-latest" in ci
    assert "runs-on: ubuntu-latest" in preview
    assert "runs-on: ubuntu-latest" in cross
    assert preview.count("max-parallel: 1") == 2


def test_isolated_workflow_copies_preserve_github_for_actionlint() -> None:
    for workflow_name in ("ci.yml", "release.yml", "v0.0.12-cross-alpha.yml"):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
        assert "--exclude .git " not in workflow
        if "rsync -a" in workflow:
            assert "--exclude '/.git/'" in workflow

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "-config-file .github/actionlint.yaml" in ci
    assert ".github/workflows/*.yml" in ci


def test_rust_and_npm_forbidden_plugin_names_cover_both_ecosystems() -> None:
    assert "@tauri-apps/plugin-shell" in architecture.FORBIDDEN_WEBVIEW_PACKAGES
    assert "tauri-plugin-shell" in architecture.FORBIDDEN_TAURI_PLUGINS
    assert architecture.FORBIDDEN_WEBVIEW_PACKAGES.isdisjoint(architecture.FORBIDDEN_TAURI_PLUGINS)


def test_v0012_cross_alpha_fail_fast_macmini_dependency_chain() -> None:
    workflow = (ROOT / ".github/workflows/v0.0.12-cross-alpha.yml").read_text()

    def get_needs(job: str) -> str:
        import re

        match = re.search(
            rf"^  {job}:\n(?:^    .*\n)*?^    needs:\s*\[(.*?)\]", workflow, re.MULTILINE
        )
        assert match is not None, f"Could not find needs for {job}"
        return match.group(1).strip()

    assert get_needs("build-macos-cross") == "preflight"
    assert set(get_needs("build-windows").split(", ")) == {
        "build-macos-cross",
        "coordinate-heavy-work",
    }
    assert set(get_needs("build-linux").split(", ")) == {"build-windows", "coordinate-heavy-work"}
    assert get_needs("verify-release-matrix") == "build-linux"
