from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "required_checks", ROOT / "scripts" / "required_checks.py"
)
required = importlib.util.module_from_spec(spec)
spec.loader.exec_module(required)
WORKFLOWS = ROOT / ".github" / "workflows"


def test_reads_the_real_preview_path_filter_and_ci_has_none():
    preview = (WORKFLOWS / "tauri-preview.yml").read_text()
    for event in ("pull_request", "push"):
        paths = required.trigger_paths(preview, event)
        assert paths and "desktop/**" in paths and "pyproject.toml" in paths
    assert required.trigger_paths((WORKFLOWS / "ci.yml").read_text(), "pull_request") is None


def test_every_watched_workflow_exists_with_its_display_name():
    for workflow, name in required.WATCHED.items():
        text = (WORKFLOWS / workflow).read_text()
        assert text.startswith(f"name: {name}\n")
    # required.yml must listen to exactly these names, or reruns won't refresh the status.
    gate = (WORKFLOWS / "required.yml").read_text()
    for name in required.WATCHED.values():
        assert f'"{name}"' in gate


def test_globs_follow_github_semantics():
    assert required.glob_regex("desktop/**").match("desktop/src/lib/a.ts")
    assert required.glob_regex("src/localsr/core/video_*.py").match("src/localsr/core/video_io.py")
    assert not required.glob_regex("src/localsr/core/video_*.py").match(
        "src/localsr/core/x/video_a.py"
    )
    assert not required.glob_regex("pyproject.toml").match("pyproject.toml.bak")


def test_preview_applies_only_to_its_paths():
    preview = (WORKFLOWS / "tauri-preview.yml").read_text()
    assert required.applies(preview, "pull_request", ["desktop/package.json"])
    assert not required.applies(preview, "pull_request", ["docs/testing.md"])
    ci = (WORKFLOWS / "ci.yml").read_text()
    assert required.applies(ci, "pull_request", ["docs/testing.md"])


def run(status: str, conclusion: str | None) -> dict:
    return {"status": status, "conclusion": conclusion}


def test_decide():
    ok = run("completed", "success")
    assert required.decide({"CI": ok})[0] == "success"
    assert required.decide({"CI": ok, "Tauri Next Preview": None})[0] == "pending"
    assert required.decide({"CI": run("in_progress", None)})[0] == "pending"
    for bad in ("failure", "cancelled", "timed_out", "skipped", "startup_failure"):
        state, description = required.decide({"CI": run("completed", bad)})
        assert state == "failure" and bad in description
    # A failure wins over a still-running workflow: the PR can't merge either way.
    assert required.decide({"CI": run("completed", "failure"), "P": None})[0] == "failure"


def test_verifier_applies_only_to_its_paths():
    verifier = (WORKFLOWS / "verifier.yml").read_text()
    for event in ("pull_request", "push"):
        assert required.trigger_paths(verifier, event) == [
            "packaging/updates/verifier/**",
            ".github/workflows/verifier.yml",
        ]
        assert required.applies(verifier, event, ["packaging/updates/verifier/src/main.rs"])
        assert required.applies(verifier, event, [".github/workflows/verifier.yml"])
        assert not required.applies(verifier, event, ["docs/updates.md"])


def test_ci_covers_release_pushes_pull_requests_and_all_rust_hosts():
    ci = (WORKFLOWS / "ci.yml").read_text()
    triggers = ci.split("permissions:", 1)[0]
    assert 'branches: [main, "release/**"]' in triggers
    assert "  pull_request:\n  workflow_dispatch:" in triggers
    for job, next_job in (("test-linux", "test-windows"), ("test-windows", "test-macos")):
        section = ci.split(f"  {job}:\n", 1)[1].split(f"  {next_job}:\n", 1)[0]
        assert "cargo clippy --all-targets -- -D warnings" in section
    assert "cargo clippy --all-targets -- -D warnings" in ci.split("  test-macos:\n", 1)[1]
    assert "-m pyright --pythonpath" in ci
    assert "--random-order --random-order-seed=38037" in ci
    assert "-m pytest tests/" in ci  # Includes test_worker_protocol_contract.py.
