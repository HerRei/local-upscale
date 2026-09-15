#!/usr/bin/env python3
"""Enforce the LocalSR desktop architecture and CI boundary.

The webview delegates native authority to Rust and inference to the Python
worker. Public Tauri releases use their own fail-closed signed pipeline.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_UPLOAD_PLATFORMS = {"linux", "macos", "windows"}

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


def signed_release_violations(desktop_release: str) -> list[str]:
    """Return violations that could publish the wrong host or unsigned installers."""

    violations: list[str] = []
    required = {
        "signed release workflow tag trigger": '      - "v*"',
        "fail-closed Tauri signing": "--require-signing",
        "Developer ID verification": "verify_macos_tauri_signing.py",
        "Authenticode verification": "verify_windows_tauri_signing.ps1",
        "installed package smoke": "smoke_tauri_installer.py",
        "real empty-cache Quick/Best inference": "validate_live_models.py",
        "embedded live-model release evidence": "--live-model-report",
        "patched release dependency assertion": "diffusers.__version__",
        "release environment consistency check": "python -m pip check",
        "compact three-installer manifest": "ci/tauri-release-artifacts.json",
        "explicit prerelease publication": "publish_tauri_release.py",
        "tag-derived release title": 'TITLE="LocalSR $TAG"',
    }
    for label, token in required.items():
        if token not in desktop_release:
            violations.append(f"signed desktop release is missing {label}")
    if "--clobber" in desktop_release:
        violations.append("signed desktop releases must not overwrite published assets")
    if desktop_release.count("contents: write") != 1:
        violations.append("only the signed desktop publishing job may write release contents")
    return violations


def workflow_upload_platform_violations(root: Path) -> list[str]:
    """Inspect every upload_artifacts.py call site for the wire-level enum."""

    violations: list[str] = []
    workflows = root / ".github" / "workflows"
    for path in sorted(workflows.glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "upload_artifacts.py" not in line:
                continue
            invocation = " ".join(lines[index : index + 12])
            match = re.search(r"--platform\s+['\"]?([A-Za-z0-9_.-]+)", invocation)
            relative = path.relative_to(root)
            if match is None:
                violations.append(
                    f"{relative}:{index + 1} upload call has no literal --platform value"
                )
                continue
            platform = match.group(1)
            if platform not in CANONICAL_UPLOAD_PLATFORMS:
                violations.append(
                    f"{relative}:{index + 1} upload platform {platform!r} is not canonical"
                )
    return violations


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
    desktop_release = _read(root, ".github/workflows/desktop-release.yml")
    violations.extend(workflow_isolation_violations(preview))
    violations.extend(signed_release_violations(desktop_release))
    violations.extend(workflow_upload_platform_violations(root))

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
        "Python worker, and fail-closed signed Tauri release."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
