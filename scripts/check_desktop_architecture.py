#!/usr/bin/env python3
"""Enforce the LocalSR desktop architecture and CI boundary.

The webview delegates native authority to Rust and inference to the Python
worker. Release packages are built by hand on GitHub-hosted runners or the Mac:
the Linux and Windows workflows verify every package before attaching it, and
the Mac release is signed and notarized.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RELEASE_PACKAGE_GATES = {
    "installed package smoke": "smoke_tauri_installer.py",
    "package architecture check": "verify_tauri_release_package.py",
    "codec allowlist": "verify_codec_allowlist.py",
    "NVIDIA redistribution policy": "verify_nvidia_redistributables.py",
    "release environment consistency check": "python -m pip check",
    "corresponding source bundle": "build_source_bundle.py",
}
# Every direct edition updates itself: its host carries the updater key and feed target,
# the installed smoke proves it, and its engine package goes with the updater files.
UPDATE_GATES = {
    "update build identity": "release_targets.py --update-env",
    "installed update identity": "--update-target",
    "updater engine payload": "prepare_engine_payload.py",
}
RELEASE_PACKAGE_WORKFLOWS = {
    "linux-appimages.yml": {
        "glibc ceiling": "verify_appimage_libc.py",
        "offline window check": "verify_appimage_window.py",
        **UPDATE_GATES,
    },
    "windows-installers.yml": UPDATE_GATES,
}
MACOS_RELEASE_GATES = {
    "Developer ID signing": "sign_macos_keychain.py",
    "notarization": "xcrun notarytool submit",
    "stapled tickets": "xcrun stapler staple",
    "Gatekeeper assessment": "spctl --assess",
    "codec allowlist": "verify_codec_allowlist.py",
    "worker smoke": "packaging/smoke_worker.py",
    "host smoke": "--headless-smoke-test",
    "signed update archive": "beta_update_signing.py sign",
    "signed release statement": "beta_update_signing.py statement",
    "corresponding source bundle": "build_source_bundle.py",
}

FORBIDDEN_WEBVIEW_PACKAGES = {
    "@tauri-apps/plugin-fs",
    "@tauri-apps/plugin-http",
    "@tauri-apps/plugin-process",
    "@tauri-apps/plugin-shell",
}
FORBIDDEN_TAURI_PLUGINS = {
    "tauri-plugin-fs",
    "tauri-plugin-http",
    "tauri-plugin-process",
    "tauri-plugin-shell",
}
FORBIDDEN_WEBVIEW_PERMISSIONS = ("fs:", "http:", "process:", "shell:")
FORBIDDEN_FRONTEND_PATTERNS = {
    "direct network access": re.compile(r"\bfetch\s*\("),
    "Node child processes": re.compile(r"(?:node:)?child_process"),
    "Tauri filesystem API": re.compile(r"@tauri-apps/(?:api/fs|plugin-fs)"),
    "Tauri HTTP API": re.compile(r"@tauri-apps/(?:api/http|plugin-http)"),
    "Tauri process API": re.compile(r"@tauri-apps/(?:api/process|plugin-process)"),
    "Tauri shell API": re.compile(r"@tauri-apps/(?:api/shell|plugin-shell)"),
}


def _read(root: Path, relative: str) -> str:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(
            f"required architecture file is unavailable: {relative}: {error}"
        ) from error


def _load_json(root: Path, relative: str) -> dict[str, object]:
    try:
        value = json.loads(_read(root, relative))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON in {relative}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{relative} must contain a JSON object")
    return value


def _load_toml(root: Path, relative: str) -> dict[str, object]:
    try:
        return tomllib.loads(_read(root, relative))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid TOML in {relative}: {error}") from error


def webview_authority_violations(
    package: dict[str, object],
    capability: dict[str, object],
    sources: dict[str, str],
) -> list[str]:
    """Return violations that would move native authority into JavaScript."""

    violations: list[str] = []
    dependencies = package.get("dependencies", {})
    if not isinstance(dependencies, dict):
        violations.append("desktop/package.json dependencies must be an object")
        dependencies = {}
    installed_forbidden = sorted(FORBIDDEN_WEBVIEW_PACKAGES.intersection(dependencies))
    if installed_forbidden:
        violations.append(
            "the webview must not install native-authority plugins: "
            + ", ".join(installed_forbidden)
        )

    permissions = capability.get("permissions", [])
    if not isinstance(permissions, list) or not all(
        isinstance(permission, str) for permission in permissions
    ):
        violations.append("desktop capabilities must contain a string permission list")
        permissions = []
    forbidden_permissions = sorted(
        permission
        for permission in permissions
        if permission.startswith(FORBIDDEN_WEBVIEW_PERMISSIONS)
    )
    if forbidden_permissions:
        violations.append(
            "the webview must not receive shell, process, HTTP, or filesystem permissions: "
            + ", ".join(forbidden_permissions)
        )

    for relative, contents in sorted(sources.items()):
        for label, pattern in FORBIDDEN_FRONTEND_PATTERNS.items():
            if pattern.search(contents):
                violations.append(f"{relative} contains forbidden {label}")
    return violations


def workflow_isolation_violations(preview: str) -> list[str]:
    """Return violations that couple preview packaging to public releases."""

    violations: list[str] = []
    preview_lower = preview.lower()
    if "contents: write" in preview_lower:
        violations.append("the preview workflow must not have release-write permission")
    if re.search(r"\bgh\s+release\b", preview, flags=re.IGNORECASE):
        violations.append("the preview workflow must not publish GitHub releases")
    if re.search(r"(?m)^\s{6,}tags:\s*$", preview):
        violations.append("the preview workflow must not run as a tag release pipeline")
    if "github.event_name == 'workflow_dispatch' && inputs.build_packages" not in preview:
        violations.append("preview installers must remain an explicit manual build")
    if "retention-days: 7" not in preview:
        violations.append("private preview artifacts must retain the seven-day limit")
    return violations


def _code(text: str) -> str:
    """The text without comment lines, so a comment can neither satisfy nor trip a check."""

    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def release_package_violations(name: str, workflow: str, gates: dict[str, str]) -> list[str]:
    """Return violations that could attach an unverified Linux or Windows package."""

    violations: list[str] = []
    code = _code(workflow)
    trigger_block = re.search(r"(?m)^on:\n((?:[ \t].*\n|\n)*)", code + "\n")
    triggers = set(
        re.findall(
            r"(?m)^  [\"']?([\w-]+)[\"']?\s*:", trigger_block.group(1) if trigger_block else ""
        )
    )
    if triggers != {"workflow_dispatch"}:
        violations.append(f"{name} must start only by hand, not on {sorted(triggers)}")
    if not re.search(r"(?m)^permissions:\n  contents: read$", code):
        violations.append(f"{name} must default to read-only contents")
    attach_job = re.search(r"(?ms)^  attach:\n(.*?)(?=^ {0,2}\S|\Z)", code)
    if (
        "write-all" in code
        or code.count("contents: write") != 1
        or attach_job is None
        or "contents: write" not in attach_job.group(1)
    ):
        violations.append(f"only the attach job of {name} may write release contents")
    for label, token in (RELEASE_PACKAGE_GATES | gates).items():
        if token not in code:
            violations.append(f"{name} is missing the {label}")
    if "--private-preview-media" in code:
        violations.append(f"{name} must not package the private preview media runtime")
    return violations


def macos_release_violations(script: str) -> list[str]:
    """Return violations that could publish an unsigned or unnotarized Mac release."""

    code = _code(script)
    return [
        f"the macOS release script is missing the {label}"
        for label, token in MACOS_RELEASE_GATES.items()
        if token not in code
    ]


def hosted_runner_violations(root: Path) -> list[str]:
    """Every workflow runs on GitHub-hosted runners; the self-hosted ones are retired."""

    return [
        f"{path.relative_to(root).as_posix()} targets a self-hosted runner"
        for path in sorted((root / ".github" / "workflows").glob("*.y*ml"))
        if "self-hosted" in _code(path.read_text(encoding="utf-8"))
    ]


def collect_violations(root: Path = ROOT) -> list[str]:
    """Validate the complete checked-in migration boundary."""

    violations: list[str] = []
    package = _load_json(root, "desktop/package.json")
    tauri = _load_json(root, "desktop/src-tauri/tauri.conf.json")
    capability = _load_json(root, "desktop/src-tauri/capabilities/default.json")
    cargo = _load_toml(root, "desktop/src-tauri/Cargo.toml")
    pyproject = _load_toml(root, "pyproject.toml")

    npm_version = package.get("version")
    tauri_version = tauri.get("version")
    cargo_package = cargo.get("package", {})
    python_project = pyproject.get("project", {})
    if not isinstance(cargo_package, dict) or not isinstance(python_project, dict):
        violations.append("Cargo and Python project metadata must be tables")
    else:
        versions = {
            str(npm_version),
            str(tauri_version),
            str(cargo_package.get("version")),
            str(python_project.get("version")),
        }
        if len(versions) != 1:
            violations.append("Python, npm, Cargo, and Tauri versions must remain synchronized")
        if cargo_package.get("license") != "MIT":
            violations.append("the Rust application control plane must remain MIT licensed")

    if package.get("private") is not True:
        violations.append("the desktop npm package must remain private and unpublished")
    if tauri.get("identifier") != "com.localsr.desktop.next":
        violations.append("the additive desktop must keep bundle id com.localsr.desktop.next")

    cargo_dependencies = cargo.get("dependencies", {})
    if isinstance(cargo_dependencies, dict):
        forbidden_rust_plugins = sorted(FORBIDDEN_TAURI_PLUGINS.intersection(cargo_dependencies))
        if forbidden_rust_plugins:
            violations.append(
                "the Tauri host must not register broad webview plugins: "
                + ", ".join(forbidden_rust_plugins)
            )

    frontend_sources = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted((root / "desktop" / "src").rglob("*"))
        if path.suffix in {".svelte", ".ts"} and not path.name.endswith(".test.ts")
    }
    violations.extend(webview_authority_violations(package, capability, frontend_sources))

    preview = _read(root, ".github/workflows/tauri-preview.yml")
    violations.extend(workflow_isolation_violations(preview))
    for name, gates in RELEASE_PACKAGE_WORKFLOWS.items():
        workflow = _read(root, f".github/workflows/{name}")
        violations.extend(release_package_violations(name, workflow, gates))
    violations.extend(macos_release_violations(_read(root, "scripts/release_macos_beta.sh")))
    violations.extend(hosted_runner_violations(root))

    build_script = _read(root, "scripts/build_tauri_preview.py")
    if 'BUILD_ROOT = ROOT / "build" / "tauri-preview"' not in build_script:
        violations.append("preview build output must stay below build/tauri-preview")
    if 'packaging" / "tauri_worker.spec"' not in build_script:
        violations.append("preview packaging must use the worker-only PyInstaller specification")

    worker_spec = _read(root, "packaging/tauri_worker.spec")
    for excluded_ui in ('"PySide6"',):
        if excluded_ui not in worker_spec:
            violations.append(f"worker package must continue to exclude {excluded_ui}")

    paths = _read(root, "desktop/src-tauri/src/paths.rs")
    if 'shared_root.join("next")' not in paths:
        violations.append("preview state must remain below the separate LocalSR/next root")
    if 'shared_root.join("models")' not in paths:
        violations.append("only the verified LocalSR/models cache may be shared")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        violations = collect_violations(args.root.resolve())
    except ValueError as error:
        print(f"Desktop architecture check failed:\n- {error}")
        return 1
    if violations:
        print("Desktop architecture check failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print(
        "Desktop architecture boundary valid: isolated webview, Rust authority, "
        "Python worker, and verified release builds."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
