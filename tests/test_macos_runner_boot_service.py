from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "macos_runner_boot_service.py"
pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="macOS LaunchDaemon tooling requires POSIX account modules"
)


def load_script():
    spec = importlib.util.spec_from_file_location("macos_runner_boot_service", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def service():
    return load_script()


@pytest.fixture
def context(service, tmp_path: Path):
    root = tmp_path / "actions-runner"
    home = tmp_path / "runner-home"
    root.mkdir()
    home.mkdir()
    return service.RunnerContext(
        root=root,
        runsvc=root / "runsvc.sh",
        user="ci-runner",
        group="staff",
        uid=502,
        home=home,
        agent_path=home / "Library/LaunchAgents/actions.runner.test.plist",
        agent_label="actions.runner.test",
        log_dir=home / "Library/Logs/com.localsr.github-actions-runner",
    )


def test_daemon_runs_boot_wrapper_as_unprivileged_runner(service, context) -> None:
    payload = service.daemon_payload(context)

    assert payload["Label"] == "com.localsr.github-actions-runner"
    assert payload["ProgramArguments"] == ["/Library/LocalSR/github-actions-runner-service"]
    assert payload["UserName"] == "ci-runner"
    assert payload["GroupName"] == "staff"
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["ProcessType"] == "Background"
    assert payload["EnvironmentVariables"] == {
        "ACTIONS_RUNNER_SVC": "1",
        "HOME": str(context.home),
    }
    encoded = plistlib.dumps(payload)
    assert plistlib.loads(encoded) == payload


def test_wrapper_waits_for_scratch_then_execs_runsvc(service, context) -> None:
    script = service.wrapper_script(context)

    assert "[ -d /Volumes/CISCRATCH ]" in script
    assert "[ -w /Volumes/CISCRATCH ]" in script
    assert "within 10 minutes" in script
    assert f"cd {context.root}" in script
    assert f"exec {context.runsvc}" in script


def test_handoff_stops_only_expected_agent_and_reboots_when_requested(service, context) -> None:
    script = service.handoff_script(context, reboot=True)

    assert "/bin/sleep 60" in script
    assert "gui/502/actions.runner.test" in script
    assert "user/502/actions.runner.test" in script
    assert str(context.root / "bin/Runner.Listener") in script
    assert "system/com.localsr.github-actions-runner" in script
    assert "/sbin/shutdown -r now" in script


def test_handoff_can_avoid_reboot(service, context) -> None:
    assert "/sbin/shutdown" not in service.handoff_script(context, reboot=False)


def test_runner_root_requires_standard_temp_layout(service, tmp_path: Path) -> None:
    runner_root = tmp_path / "actions-runner"
    runner_temp = runner_root / "_work" / "_temp"
    runner_temp.mkdir(parents=True)

    assert service.runner_root_from_temp(str(runner_temp)) == runner_root


def test_runner_root_rejects_unexpected_temp_layout(service, tmp_path: Path) -> None:
    wrong_temp = tmp_path / "tmp"
    wrong_temp.mkdir()

    with pytest.raises(service.ConfigurationError, match="expected"):
        service.runner_root_from_temp(str(wrong_temp))
