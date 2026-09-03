#!/usr/bin/env python3
"""Build LocalSR Next Preview without changing the existing release artifacts."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
BUILD_ROOT = ROOT / "build" / "tauri-preview"
WORKER_DIST = BUILD_ROOT / "worker-dist"
WORKER_WORK = BUILD_ROOT / "worker-build"
ENGINE_DIR = WORKER_DIST / "engine"
CONFIG_PATH = BUILD_ROOT / "tauri-worker.conf.json"


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def rust_build_environment() -> dict[str, str]:
    """Return an environment where Cargo is visible to the Tauri CLI.

    GUI terminals and automation shells do not always source rustup's shell
    setup. Resolve the selected toolchain explicitly without changing the
    user's login-shell configuration.
    """
    env = os.environ.copy()
    if shutil.which("cargo", path=env.get("PATH")):
        return env
    rustup = shutil.which("rustup", path=env.get("PATH"))
    if not rustup:
        raise SystemExit("Cargo is missing. Install the stable Rust toolchain with rustup first.")
    result = subprocess.run(
        [rustup, "which", "cargo"],
        check=True,
        capture_output=True,
        text=True,
    )
    cargo = Path(result.stdout.strip())
    if not cargo.is_file():
        raise SystemExit(f"rustup returned an unavailable Cargo executable: {cargo}")
    env["PATH"] = os.pathsep.join([str(cargo.parent), env.get("PATH", "")])
    return env


def tauri_build_environment() -> dict[str, str]:
    """Return the environment used by the native Tauri bundler.

    linuxdeploy inspects every ELF file in an AppDir, including the already
    self-contained PyInstaller worker.  NumPy and other wheels deliberately
    give their private libraries unique names and resolve them from adjacent
    ``*.libs`` directories.  Expose those directories while *building* the
    AppImage so linuxdeploy can resolve the same private dependencies instead
    of failing after the Rust application has compiled.

    This path is only used by the packager.  It is not written into the app or
    inherited by the installed worker at runtime.
    """

    env = rust_build_environment()
    if not sys.platform.startswith("linux") or not ENGINE_DIR.is_dir():
        return env

    library_dirs = sorted(
        {str(path.parent) for path in ENGINE_DIR.rglob("*.so*") if path.is_file()}
    )
    existing = env.get("LD_LIBRARY_PATH")
    if existing:
        library_dirs.append(existing)
    if library_dirs:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(library_dirs)
    return env


def npm_executable() -> str:
    """Resolve npm to a directly executable path on every supported host."""

    executable = shutil.which("npm")
    if executable:
        return executable
    raise SystemExit("npm is missing. Install the locked frontend toolchain first.")


def worker_executable() -> Path:
    name = "localsr-worker.exe" if os.name == "nt" else "localsr-worker"
    return ENGINE_DIR / name


def worker_target_arch(target: str | None) -> str | None:
    if not target:
        return None
    if target.startswith("aarch64-"):
        return "arm64"
    if target.startswith("x86_64-"):
        return "x86_64"
    return None


def build_worker(target: str | None = None) -> None:
    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError as error:
            raise SystemExit(
                "PyInstaller is missing. Install the package build dependencies first: "
                "python -m pip install -e '.[package,video,face]'"
            ) from error
    WORKER_DIST.mkdir(parents=True, exist_ok=True)
    WORKER_WORK.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    if target_arch := worker_target_arch(target):
        environment["LOCALSR_TARGET_ARCH"] = target_arch
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(WORKER_DIST),
            "--workpath",
            str(WORKER_WORK),
            str(ROOT / "packaging" / "tauri_worker.spec"),
        ],
        env=environment,
    )
    executable = worker_executable()
    if not executable.is_file():
        raise SystemExit(f"worker build did not create {executable}")


def write_bundle_overlay(*, require_signing: bool = False) -> None:
    if not ENGINE_DIR.is_dir():
        raise SystemExit(f"missing worker engine directory: {ENGINE_DIR}")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    bundle: dict[str, object] = {
        "resources": {
            f"{ENGINE_DIR.as_posix()}/": "engine/",
            (ROOT / "LICENSE").as_posix(): "LICENSE",
            (ROOT / "THIRD_PARTY_NOTICES.md").as_posix(): "THIRD_PARTY_NOTICES.md",
        },
    }
    if sys.platform == "darwin":
        apple_signing = os.environ.get("APPLE_SIGNING_IDENTITY") or os.environ.get(
            "APPLE_CERTIFICATE"
        )
        apple_id_notary = all(
            os.environ.get(name) for name in ("APPLE_ID", "APPLE_PASSWORD", "APPLE_TEAM_ID")
        )
        api_notary = all(
            os.environ.get(name)
            for name in ("APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_KEY_PATH")
        )
        if require_signing and not (apple_signing and (apple_id_notary or api_notary)):
            raise SystemExit("production macOS signing and notarization credentials are required")
        if not apple_signing:
            # Tauri recommends a final ad-hoc seal for credential-free Apple
            # Silicon previews. A real identity supplied by CI overrides this.
            bundle["macOS"] = {"signingIdentity": "-"}
    if os.name == "nt":
        thumbprint = os.environ.get("WINDOWS_CERTIFICATE_THUMBPRINT", "").strip()
        timestamp_url = os.environ.get("WINDOWS_TIMESTAMP_URL", "").strip()
        if require_signing and (not thumbprint or not timestamp_url):
            raise SystemExit(
                "WINDOWS_CERTIFICATE_THUMBPRINT and WINDOWS_TIMESTAMP_URL are required"
            )
        if thumbprint and timestamp_url:
            bundle["windows"] = {
                "certificateThumbprint": thumbprint,
                "digestAlgorithm": "sha256",
                "timestampUrl": timestamp_url,
            }
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "bundle": bundle,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Build an unoptimized desktop binary")
    parser.add_argument("--skip-worker", action="store_true", help="Reuse an existing engine build")
    parser.add_argument("--skip-checks", action="store_true")
    parser.add_argument(
        "--require-signing",
        action="store_true",
        help="Refuse release bundles unless platform production signing is configured",
    )
    parser.add_argument("--target", help="Native Rust target triple")
    bundle_mode = parser.add_mutually_exclusive_group()
    bundle_mode.add_argument(
        "--bundles",
        help="Comma-separated Tauri bundle formats (for example dmg, nsis, or appimage)",
    )
    bundle_mode.add_argument(
        "--no-bundle", action="store_true", help="Compile without an installer"
    )
    args = parser.parse_args()

    run([sys.executable, str(ROOT / "scripts" / "export_desktop_catalog.py")])
    if not args.skip_worker:
        build_worker(args.target)
    write_bundle_overlay(require_signing=args.require_signing)

    if not (DESKTOP / "node_modules").is_dir():
        run([npm_executable(), "ci"], cwd=DESKTOP)
    if not args.skip_checks:
        run([npm_executable(), "run", "check"], cwd=DESKTOP)
        run([npm_executable(), "test"], cwd=DESKTOP)

    command = [
        npm_executable(),
        "run",
        "tauri",
        "--",
        "build",
        "--ci",
        "--config",
        str(CONFIG_PATH),
    ]
    if args.debug:
        command.append("--debug")
    if args.target:
        command.extend(["--target", args.target])
    if args.bundles:
        command.extend(["--bundles", args.bundles])
    if args.no_bundle:
        command.append("--no-bundle")
    run(command, cwd=DESKTOP, env=tauri_build_environment())
    print(
        f"LocalSR Next Preview built for {platform.system()} {platform.machine()}. "
        "The legacy Slint app and its artifacts were not modified.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
