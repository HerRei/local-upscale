from __future__ import annotations

import importlib.util
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

    assert any("exact v0.0.10 tag" in violation for violation in violations)
    assert any("must not overwrite" in violation for violation in violations)
    assert any("only the v0.0.10" in violation for violation in violations)


def test_cross_alpha_uses_supported_artifact_transfer_platforms() -> None:
    workflow = (ROOT / ".github" / "workflows" / "v0.0.10-cross-alpha.yml").read_text()

    assert "--platform tauri-alpha-" not in workflow
    assert '--attempt "$GITHUB_RUN_ATTEMPT" --platform linux \\' in workflow
    assert "--attempt $env:GITHUB_RUN_ATTEMPT --platform windows $artifact" in workflow
    assert '--attempt "$GITHUB_RUN_ATTEMPT" --platform macos \\' in workflow


def test_publish_jobs_combine_attempts_and_use_the_provisioned_python() -> None:
    for workflow_name in ("desktop-release.yml", "v0.0.10-cross-alpha.yml"):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()

        assert 'RUN_ROOT="/mnt/hdd/ci-artifacts/$GITHUB_RUN_ID"' in workflow
        assert '"$HOME/.venv-ci/bin/python3.11" scripts/prepare_tauri_release_assets.py' in workflow
        assert 'ROOT="/mnt/hdd/ci-artifacts/$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT"' not in workflow
        assert "\n          python scripts/prepare_tauri_release_assets.py" not in workflow


def test_tauri_preview_bounds_the_linux_cargo_cache_only_under_pressure() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tauri-preview.yml").read_text()

    assert "Bound the Linux Cargo cache under storage pressure" in workflow
    assert "if: always() && runner.os == 'Linux'" in workflow
    assert 'EXPECTED="$RUNNER_WORKSPACE/.localsr-tauri-target/linux-x64"' in workflow
    assert 'test "$CARGO_TARGET_DIR" = "$EXPECTED"' in workflow
    assert 'find "$CARGO_TARGET_DIR" -depth -delete' in workflow
    assert "--ssd-peak-gib 8" in workflow


def test_rust_and_npm_forbidden_plugin_names_cover_both_ecosystems() -> None:
    assert "@tauri-apps/plugin-shell" in architecture.FORBIDDEN_WEBVIEW_PACKAGES
    assert "tauri-plugin-shell" in architecture.FORBIDDEN_TAURI_PLUGINS
    assert architecture.FORBIDDEN_WEBVIEW_PACKAGES.isdisjoint(architecture.FORBIDDEN_TAURI_PLUGINS)
