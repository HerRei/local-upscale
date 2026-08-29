#!/usr/bin/env python3
"""Install the LocalSR macOS GitHub runner as a pre-login LaunchDaemon.

GitHub's stock macOS service is a LaunchAgent in the runner user's Aqua
session. A headless VM can therefore be fully booted while the runner stays
offline at the login window. This helper validates the existing GitHub
service, installs a root-owned LaunchDaemon that still runs as the unprivileged
runner user, and can hand the live listener over after the maintenance job
finishes.
"""

from __future__ import annotations

import argparse
import grp
import os
import plistlib
import pwd
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

DAEMON_LABEL = "com.localsr.github-actions-runner"
DAEMON_PATH = Path("/Library/LaunchDaemons") / f"{DAEMON_LABEL}.plist"
SERVICE_DIR = Path("/Library/LocalSR")
WRAPPER_PATH = SERVICE_DIR / "github-actions-runner-service"
HANDOFF_PATH = SERVICE_DIR / "github-actions-runner-handoff"
HANDOFF_LOG = Path("/var/log/localsr-actions-runner-handoff.log")


class ConfigurationError(RuntimeError):
    """Raised when the existing runner service is unsafe or unexpected."""


@dataclass(frozen=True)
class RunnerContext:
    root: Path
    runsvc: Path
    user: str
    group: str
    uid: int
    home: Path
    agent_path: Path
    agent_label: str
    log_dir: Path


def fail(message: str) -> NoReturn:
    raise ConfigurationError(message)


def _require_regular_owned_file(
    path: Path,
    uid: int,
    *,
    executable: bool = False,
    allow_root: bool = False,
) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        fail(f"required file is missing: {path}")
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        fail(f"required path must be a regular non-symlink file: {path}")
    allowed_uids = {uid, 0} if allow_root else {uid}
    if details.st_uid not in allowed_uids:
        fail(f"required file is not owned by runner uid {uid}: {path}")
    if details.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        fail(f"required file is group/world writable: {path}")
    if executable and not details.st_mode & stat.S_IXUSR:
        fail(f"required file is not executable by its owner: {path}")


def runner_root_from_temp(runner_temp: str) -> Path:
    raw_temp = Path(runner_temp).expanduser()
    if raw_temp.is_symlink():
        fail(f"RUNNER_TEMP may not be a symlink: {raw_temp}")
    temp_path = raw_temp.resolve(strict=True)
    if len(temp_path.parents) < 2 or temp_path.name != "_temp":
        fail(f"RUNNER_TEMP does not have the expected <runner>/_work/_temp layout: {temp_path}")
    if temp_path.parent.name != "_work":
        fail(f"RUNNER_TEMP parent is not _work: {temp_path}")
    return temp_path.parents[1]


def load_context(runner_root: Path | None = None) -> RunnerContext:
    user_record = pwd.getpwuid(os.getuid())
    user = user_record.pw_name
    group = grp.getgrgid(user_record.pw_gid).gr_name
    home = Path(user_record.pw_dir).resolve(strict=True)

    if runner_root is None:
        runner_temp = os.environ.get("RUNNER_TEMP", "")
        if not runner_temp:
            fail("RUNNER_TEMP is required when --runner-root is omitted")
        root = runner_root_from_temp(runner_temp)
    else:
        if runner_root.is_symlink():
            fail(f"runner root may not be a symlink: {runner_root}")
        root = runner_root.expanduser().resolve(strict=True)

    root_details = root.lstat()
    if not stat.S_ISDIR(root_details.st_mode) or root_details.st_uid != user_record.pw_uid:
        fail(f"runner root must be a directory owned by {user}: {root}")
    if root_details.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        fail(f"runner root is group/world writable: {root}")

    runsvc = root / "runsvc.sh"
    _require_regular_owned_file(runsvc, user_record.pw_uid, executable=True)

    service_pointer = root / ".service"
    _require_regular_owned_file(service_pointer, user_record.pw_uid)
    service_value = service_pointer.read_text(encoding="utf-8").strip()
    if not service_value:
        fail(f"runner service pointer is empty: {service_pointer}")
    agent_raw = Path(service_value).expanduser()
    if not agent_raw.is_absolute() or agent_raw.is_symlink():
        fail(f"runner service pointer must name an absolute non-symlink path: {service_value}")
    agent_path = agent_raw.resolve(strict=True)
    expected_agent_parent = (home / "Library" / "LaunchAgents").resolve(strict=True)
    if agent_path.parent != expected_agent_parent:
        fail(f"runner service is not inside the runner user's LaunchAgents directory: {agent_path}")
    if not agent_path.name.startswith("actions.runner.") or agent_path.suffix != ".plist":
        fail(f"unexpected GitHub runner LaunchAgent name: {agent_path.name}")
    _require_regular_owned_file(agent_path, user_record.pw_uid, allow_root=True)

    with agent_path.open("rb") as stream:
        agent = plistlib.load(stream)
    agent_label = agent.get("Label")
    if not isinstance(agent_label, str) or agent_label != agent_path.stem:
        fail("GitHub runner LaunchAgent label does not match its filename")
    arguments = agent.get("ProgramArguments")
    if not isinstance(arguments, list) or not arguments:
        fail("GitHub runner LaunchAgent has no ProgramArguments")
    configured_runsvc = Path(str(arguments[0])).expanduser().resolve(strict=True)
    if configured_runsvc != runsvc:
        fail(f"GitHub runner LaunchAgent invokes an unexpected program: {configured_runsvc}")
    configured_user = agent.get("UserName")
    if configured_user not in (None, user):
        fail(f"GitHub runner LaunchAgent names unexpected user {configured_user!r}")

    return RunnerContext(
        root=root,
        runsvc=runsvc,
        user=user,
        group=group,
        uid=user_record.pw_uid,
        home=home,
        agent_path=agent_path,
        agent_label=agent_label,
        log_dir=home / "Library" / "Logs" / DAEMON_LABEL,
    )


def daemon_payload(context: RunnerContext) -> dict[str, object]:
    return {
        "Label": DAEMON_LABEL,
        "ProgramArguments": [str(WRAPPER_PATH)],
        "UserName": context.user,
        "GroupName": context.group,
        "WorkingDirectory": str(context.root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(context.log_dir / "stdout.log"),
        "StandardErrorPath": str(context.log_dir / "stderr.log"),
        "EnvironmentVariables": {
            "ACTIONS_RUNNER_SVC": "1",
            "HOME": str(context.home),
        },
        "ProcessType": "Background",
    }


def wrapper_script(context: RunnerContext) -> str:
    root = shlex.quote(str(context.root))
    runsvc = shlex.quote(str(context.runsvc))
    return f"""#!/bin/bash
set -eu

# The release workflows require the dedicated guest SSD. Do not advertise
# this runner until macOS has mounted it and the runner user can write to it.
attempt=0
while [ "$attempt" -lt 300 ]; do
    if [ -d /Volumes/CISCRATCH ] && [ -w /Volumes/CISCRATCH ]; then
        break
    fi
    attempt=$((attempt + 1))
    /bin/sleep 2
done

if [ ! -d /Volumes/CISCRATCH ] || [ ! -w /Volumes/CISCRATCH ]; then
    echo "CISCRATCH did not become writable within 10 minutes" >&2
    exit 75
fi

cd {root}
exec {runsvc}
"""


def handoff_script(context: RunnerContext, *, reboot: bool) -> str:
    gui_service = shlex.quote(f"gui/{context.uid}/{context.agent_label}")
    user_service = shlex.quote(f"user/{context.uid}/{context.agent_label}")
    old_listener = shlex.quote(str(context.root / "bin" / "Runner.Listener"))
    daemon_path = shlex.quote(str(DAEMON_PATH))
    system_service = shlex.quote(f"system/{DAEMON_LABEL}")
    handoff_path = shlex.quote(str(HANDOFF_PATH))
    reboot_block = ""
    if reboot:
        reboot_block = """
echo "Boot service is active; rebooting to prove pre-login startup"
/bin/sleep 20
/sbin/shutdown -r now
"""
    return f"""#!/bin/bash
set -eu
exec >>{shlex.quote(str(HANDOFF_LOG))} 2>&1

echo "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ) waiting for the maintenance worker to exit"
/bin/sleep 60
attempt=0
while /usr/bin/pgrep -u {context.uid} -f '/bin/Runner.Worker' >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 600 ]; then
        echo "Runner.Worker did not exit within 10 minutes" >&2
        exit 75
    fi
    /bin/sleep 1
done
/bin/sleep 10

# Stop the already-loaded login agent only after its workflow worker is gone.
/bin/launchctl bootout {gui_service} 2>/dev/null || true
/bin/launchctl bootout {user_service} 2>/dev/null || true
/bin/sleep 3
if /usr/bin/pgrep -u {context.uid} -f {old_listener} >/dev/null 2>&1; then
    /usr/bin/pkill -TERM -u {context.uid} -f {old_listener} || true
    /bin/sleep 3
fi

/bin/launchctl bootout {system_service} 2>/dev/null || true
/bin/launchctl bootstrap system {daemon_path}
/bin/launchctl kickstart -k {system_service}
/bin/sleep 10
/bin/launchctl print {system_service}
/bin/rm -f {handoff_path}
{reboot_block}"""


def _run(command: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def _sudo(*arguments: str, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return _run(["/usr/bin/sudo", "-n", *arguments], capture_output=capture_output)


def _write_temp(contents: bytes, directory: Path, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix="localsr-runner-", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(mode)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _backup_if_present(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    _sudo("/bin/cp", "-p", str(path), str(backup))
    print(f"Preserved previous service file as {backup}")


def install(context: RunnerContext, *, handoff: bool, reboot: bool) -> None:
    if sys.platform != "darwin":
        fail("installation is supported only on macOS")
    _sudo("/usr/bin/true")

    runner_temp = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())).resolve(strict=True)
    context.log_dir.mkdir(parents=True, exist_ok=True)
    context.log_dir.chmod(0o750)

    plist_bytes = plistlib.dumps(daemon_payload(context), fmt=plistlib.FMT_XML, sort_keys=False)
    wrapper_bytes = wrapper_script(context).encode("utf-8")
    plist_temp = _write_temp(plist_bytes, runner_temp, 0o644)
    wrapper_temp = _write_temp(wrapper_bytes, runner_temp, 0o755)
    try:
        _run(["/usr/bin/plutil", "-lint", str(plist_temp)])
        _sudo(
            "/usr/bin/install",
            "-d",
            "-o",
            "root",
            "-g",
            "wheel",
            "-m",
            "0755",
            str(SERVICE_DIR),
        )
        _backup_if_present(DAEMON_PATH)
        _backup_if_present(WRAPPER_PATH)
        _sudo(
            "/usr/bin/install",
            "-o",
            "root",
            "-g",
            "wheel",
            "-m",
            "0755",
            str(wrapper_temp),
            str(WRAPPER_PATH),
        )
        _sudo(
            "/usr/bin/install",
            "-o",
            "root",
            "-g",
            "wheel",
            "-m",
            "0644",
            str(plist_temp),
            str(DAEMON_PATH),
        )
        _sudo("/usr/bin/plutil", "-lint", str(DAEMON_PATH))
        _sudo("/bin/launchctl", "disable", f"gui/{context.uid}/{context.agent_label}")
        _sudo("/bin/launchctl", "enable", f"system/{DAEMON_LABEL}")
    finally:
        plist_temp.unlink(missing_ok=True)
        wrapper_temp.unlink(missing_ok=True)

    print(f"Installed root-owned boot daemon {DAEMON_PATH}")
    print(f"The daemon executes {context.runsvc} as unprivileged user {context.user}")
    print(f"Disabled the login-scoped service {context.agent_label} for future sessions")

    if not handoff:
        print("A reboot is required before the new boot daemon takes ownership of the runner")
        return

    handoff_bytes = handoff_script(context, reboot=reboot).encode("utf-8")
    handoff_temp = _write_temp(handoff_bytes, runner_temp, 0o700)
    try:
        _sudo(
            "/usr/bin/install",
            "-o",
            "root",
            "-g",
            "wheel",
            "-m",
            "0700",
            str(handoff_temp),
            str(HANDOFF_PATH),
        )
    finally:
        handoff_temp.unlink(missing_ok=True)

    environment = os.environ.copy()
    environment.pop("RUNNER_TRACKING_ID", None)
    handoff_process = subprocess.Popen(
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/env",
            "-u",
            "RUNNER_TRACKING_ID",
            "/usr/bin/nohup",
            str(HANDOFF_PATH),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=environment,
    )
    time.sleep(1)
    handoff_status = handoff_process.poll()
    if handoff_status is not None:
        fail(f"post-job service handoff exited prematurely with status {handoff_status}")
    print(f"Scheduled post-job service handoff; diagnostics will be written to {HANDOFF_LOG}")
    if reboot:
        print("The guest will reboot after the daemon starts to prove pre-login recovery")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install",))
    parser.add_argument("--runner-root", type=Path)
    parser.add_argument("--expected-runner-name", required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--handoff", action="store_true")
    parser.add_argument("--reboot-after-handoff", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        actual_runner = os.environ.get("RUNNER_NAME", "")
        if actual_runner != args.expected_runner_name:
            fail(
                f"refusing unexpected runner {actual_runner!r}; expected {args.expected_runner_name!r}"
            )
        actual_repository = os.environ.get("GITHUB_REPOSITORY", "")
        if actual_repository != args.expected_repository:
            fail(
                f"refusing unexpected repository {actual_repository!r}; "
                f"expected {args.expected_repository!r}"
            )
        if args.reboot_after_handoff and not args.handoff:
            fail("--reboot-after-handoff requires --handoff")
        context = load_context(args.runner_root)
        install(context, handoff=args.handoff, reboot=args.reboot_after_handoff)
    except (
        ConfigurationError,
        OSError,
        plistlib.InvalidFileException,
        subprocess.CalledProcessError,
    ) as error:
        print(f"macOS runner boot-service installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
