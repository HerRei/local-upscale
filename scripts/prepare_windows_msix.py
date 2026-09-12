#!/usr/bin/env python3
"""Prepare a desktop-only Store MSIX layout from an existing Windows build.

This tool never builds, installs, signs or publishes the application. MakeAppx
and installed Windows acceptance are separate steps. The existing .12 release
configuration and artifacts are not used as output locations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ROOT / "packaging/windows/msix/identity.json"
FOUNDATION = "http://schemas.microsoft.com/appx/manifest/foundation/windows10"
UAP = "http://schemas.microsoft.com/appx/manifest/uap/windows10"
RESCAP = "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
LOGOS = {"StoreLogo.png": 50, "Square44x44Logo.png": 44, "Square150x150Logo.png": 150}
for prefix, namespace in (("", FOUNDATION), ("uap", UAP), ("rescap", RESCAP)):
    ET.register_namespace(prefix, namespace)


def load_identity(path: Path = IDENTITY) -> dict:
    identity = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "name",
        "publisher",
        "publisher_display_name",
        "display_name",
        "package_family_name",
        "store_id",
    }
    if not isinstance(identity, dict) or set(identity) != required:
        raise ValueError("Invalid Store identity fields")
    if identity["schema_version"] != 1 or any(
        not isinstance(identity[key], str) or not identity[key].strip()
        for key in required - {"schema_version"}
    ):
        raise ValueError("Invalid Store identity values")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{2,49}", identity["name"]):
        raise ValueError("Invalid Store package name")
    if not re.fullmatch(
        r"CN=[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", identity["publisher"]
    ):
        raise ValueError("Expected the publisher CN assigned by Partner Center")
    if not re.fullmatch(r"[A-Z0-9]{12}", identity["store_id"]):
        raise ValueError("Invalid Store ID")
    # Windows derives this suffix from the exact, case-sensitive publisher DN.
    digest = hashlib.sha256(identity["publisher"].encode("utf-16le")).digest()[:8]
    bits = "".join(f"{byte:08b}" for byte in digest) + "0"
    alphabet = "0123456789abcdefghjkmnpqrstvwxyz"
    suffix = "".join(alphabet[int(bits[i : i + 5], 2)] for i in range(0, 65, 5))
    if identity["package_family_name"] != f"{identity['name']}_{suffix}":
        raise ValueError("Publisher and package name do not match the supplied package family")
    return identity


def validate_version(version: str) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.0", version):
        raise ValueError("Store package version must have four numbers and end in .0")
    numbers = [int(part) for part in version.split(".")]
    if not 1 <= numbers[0] <= 65535 or any(number > 65535 for number in numbers):
        raise ValueError("Store package version must start above zero and use 16-bit components")


def manifest(identity: dict, version: str) -> bytes:
    validate_version(version)

    def node(parent, name, attrs=None, text=None, namespace=FOUNDATION):
        element = ET.SubElement(parent, f"{{{namespace}}}{name}", attrs or {})
        element.text = text
        return element

    package = ET.Element(f"{{{FOUNDATION}}}Package", {"IgnorableNamespaces": "uap rescap"})
    node(
        package,
        "Identity",
        {
            "Name": identity["name"],
            "Publisher": identity["publisher"],
            "Version": version,
            "ProcessorArchitecture": "x64",
        },
    )
    properties = node(package, "Properties")
    node(properties, "DisplayName", text=identity["display_name"])
    node(properties, "PublisherDisplayName", text=identity["publisher_display_name"])
    node(properties, "Description", text="Local image and video restoration")
    node(properties, "Logo", text="Assets\\StoreLogo.png")
    dependencies = node(package, "Dependencies")
    node(
        dependencies,
        "TargetDeviceFamily",
        {
            "Name": "Windows.Desktop",
            "MinVersion": "10.0.19041.0",
            "MaxVersionTested": "10.0.19041.0",
        },
    )
    resources = node(package, "Resources")
    node(resources, "Resource", {"Language": "en-US"})
    applications = node(package, "Applications")
    application = node(
        applications,
        "Application",
        {
            "Id": "LocalSR",
            "Executable": "localsr-next.exe",
            "EntryPoint": "Windows.FullTrustApplication",
        },
    )
    node(
        application,
        "VisualElements",
        {
            "DisplayName": identity["display_name"],
            "Description": "Local image and video restoration",
            "Square150x150Logo": "Assets\\Square150x150Logo.png",
            "Square44x44Logo": "Assets\\Square44x44Logo.png",
            "BackgroundColor": "transparent",
        },
        namespace=UAP,
    )
    capabilities = node(package, "Capabilities")
    node(capabilities, "Capability", {"Name": "runFullTrust"}, namespace=RESCAP)
    ET.indent(package, space="  ")
    return ET.tostring(package, encoding="utf-8", xml_declaration=True) + b"\n"


def require_x64(path: Path) -> None:
    with path.open("rb") as source:
        header = source.read(65536)
    if len(header) < 64 or header[:2] != b"MZ":
        raise ValueError(f"Expected a Windows PE binary: {path.name}")
    offset = struct.unpack_from("<I", header, 60)[0]
    if (
        offset + 6 > len(header)
        or header[offset : offset + 4] != b"PE\0\0"
        or struct.unpack_from("<H", header, offset + 4)[0] != 0x8664
    ):
        raise ValueError(f"Expected an x64 Windows binary: {path.name}")


def prepare(
    output: Path,
    version: str,
    *,
    app: Path | None = None,
    engine: Path | None = None,
    backend: str | None = None,
) -> dict:
    identity = load_identity()
    xml = manifest(identity, version)
    if any(value is not None for value in (app, engine, backend)) and not all(
        value is not None for value in (app, engine, backend)
    ):
        raise ValueError(
            "Supply --app, --engine and --backend together, or none for a manifest preview"
        )
    if backend is not None and backend not in {"cpu", "cuda", "directml"}:
        raise ValueError("Unsupported Windows backend")
    if output.exists() or output.is_symlink():
        raise ValueError("Output already exists; choose a new staging directory")
    if app is not None and engine is not None:
        if not app.is_file() or app.is_symlink() or not engine.is_dir() or engine.is_symlink():
            raise ValueError("App and engine must be ordinary local build artifacts")
        if app.name.lower() != "localsr-next.exe":
            raise ValueError("Supply the compiled localsr-next.exe host, not an installer")
        if output.resolve().is_relative_to(engine.resolve()):
            raise ValueError("Output must be outside the source engine")
        require_x64(app)
        require_x64(engine / "localsr-worker.exe")
        if not (engine / "_internal").is_dir():
            raise ValueError("Supply the complete frozen engine, including _internal")
        seen = set()
        for file in engine.rglob("*"):
            relative = file.relative_to(engine).as_posix()
            if file.is_symlink() or not (file.is_file() or file.is_dir()):
                raise ValueError(f"Unsupported engine entry: {relative}")
            if relative.lower() in seen:
                raise ValueError(f"Case-colliding engine entry: {relative}")
            seen.add(relative.lower())
            if file.suffix.lower() in {".exe", ".dll", ".pyd"}:
                require_x64(file)
            if file.suffix.lower() in {".pth", ".safetensors", ".gguf"}:
                raise ValueError("Model checkpoints must stay outside the application package")
    output.mkdir(parents=True)
    layout = output / "layout"
    layout.mkdir()
    try:
        (layout / "AppxManifest.xml").write_bytes(xml)
        assets = layout / "Assets"
        assets.mkdir()
        for name, size in LOGOS.items():
            source = ROOT / "packaging/windows/msix/Assets" / name
            header = source.read_bytes()[:24]
            if header[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", header[16:24]) != (
                size,
                size,
            ):
                raise ValueError(f"Invalid Store logo: {name}")
            shutil.copy2(source, assets / name)
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(ROOT / name, layout / name)
        if app is not None and engine is not None:
            shutil.copy2(app, layout / "localsr-next.exe")
            shutil.copytree(engine, layout / "engine")
        (output / "tauri-store.conf.json").write_text(
            json.dumps(
                {
                    "productName": identity["display_name"],
                    "bundle": {
                        "publisher": identity["publisher_display_name"],
                        "createUpdaterArtifacts": False,
                    },
                    "plugins": {"updater": {"pubkey": "", "endpoints": []}},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        report = {
            "schema_version": 1,
            "store_id": identity["store_id"],
            "package_family_name": identity["package_family_name"],
            "package_version": version,
            "backend": backend,
            "has_binaries": app is not None,
            "windows_runtime_tested": False,
            "store_certified": False,
        }
        (output / "preparation.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        return report
    except Exception:
        shutil.rmtree(output)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--version", required=True, help="Explicit Store package version, e.g. 1.0.0.0"
    )
    parser.add_argument("--app", type=Path)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--backend", choices=("cpu", "cuda", "directml"))
    args = parser.parse_args()
    report = prepare(
        args.output, args.version, app=args.app, engine=args.engine, backend=args.backend
    )
    print(json.dumps(report, indent=2))
    print(
        "Prepared a local layout. Windows packaging, runtime acceptance and Store certification remain separate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
