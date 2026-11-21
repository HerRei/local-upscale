"""Properties of the GitHub workflows and their gate scripts that must not drift."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# GitHub appends only `exit $LASTEXITCODE` to a pwsh script, so a native command that fails
# before the last line is ignored unless the script checks for it. Every step in a job that
# can run under pwsh therefore holds one native command, or checks each exit code.
NATIVE = {"npm", "npx", "cargo", "python", "pip", "node", "git", "choco", "rustup"}
CHECKED = re.compile(r"\$LASTEXITCODE|PSNativeCommandUseErrorActionPreference|&&")


def jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([\w-]+):\n", body)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def steps(job: str) -> list[str]:
    return re.split(r"(?m)^      - ", job)[1:]


def pwsh_steps() -> list[tuple[str, str, str]]:
    found = []
    for workflow in WORKFLOWS:
        for name, job in jobs(workflow.read_text(encoding="utf-8")).items():
            shells = set(re.findall(r"(?m)^\s+shell: (\S+)", job))
            if "pwsh" not in shells and "${{ matrix.shell }}" not in shells:
                continue
            for step in steps(job):
                if "pwsh" not in shells and "matrix.shell" not in "".join(shells):
                    continue
                condition = re.search(r"(?m)^\s+if: (.+)$", step)
                if condition and re.search(r"runner\.os == '(Linux|macOS)'", condition.group(1)):
                    continue
                run = re.search(r"(?m)^        run: \|\n((?:          .*\n|\n)+)", step + "\n")
                if run:
                    found.append((workflow.name, name, run.group(1)))
    return found


def native_lines(script: str) -> list[str]:
    lines = [
        line.strip()
        for line in script.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return [line for line in lines if line.split()[0] in NATIVE]


def test_steps_that_can_run_under_pwsh_do_not_hide_a_failing_command():
    steps_found = pwsh_steps()
    assert steps_found, "the workflow scan found no pwsh steps"
    unchecked = [
        f"{workflow} {job}: {native_lines(script)[0]}"
        for workflow, job, script in steps_found
        if len(native_lines(script)) > 1 and not CHECKED.search(script)
    ]
    assert not unchecked


def load(path: str):
    spec = importlib.util.spec_from_file_location("_wf_" + Path(path).stem, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preview_isolation_check_reads_code_not_comments():
    architecture = load("scripts/check_desktop_architecture.py")
    preview = (
        "# retention-days: 7\n"
        "on:\n  workflow_dispatch:\n"
        "jobs:\n  b:\n    if: github.event_name == 'workflow_dispatch' && inputs.build_packages\n"
        "    steps:\n      - uses: actions/upload-artifact@v7\n        with:\n"
        "          retention-days: 90\n"
    )
    violations = architecture.workflow_isolation_violations(preview)
    assert any("seven-day" in violation for violation in violations)

    commented = preview.replace("retention-days: 90", "retention-days: 7")
    commented += "          # a gh release would belong in the release workflows\n"
    assert architecture.workflow_isolation_violations(commented) == []
