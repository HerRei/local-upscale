from __future__ import annotations

import importlib.util
import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_windows_msix", ROOT / "scripts/prepare_windows_msix.py"
)
assert SPEC and SPEC.loader
msix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(msix)


def pe(path: Path, machine: int = 0x8664) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(128)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 64)
    header[64:68] = b"PE\0\0"
    struct.pack_into("<H", header, 68, machine)
    path.write_bytes(header)
    return path


def artifacts(tmp_path: Path) -> tuple[Path, Path]:
    app = pe(tmp_path / "binaries/localsr-next.exe")
    engine = tmp_path / "engine"
    pe(engine / "localsr-worker.exe")
    pe(engine / "_internal/runtime.dll")
    return app, engine


def test_store_identity_and_manifest_preview(tmp_path: Path) -> None:
    output = tmp_path / "preview"
    result = msix.prepare(output, "1.0.0.0")
    root = ET.parse(output / "layout/AppxManifest.xml").getroot()
    ns = {"m": msix.FOUNDATION, "r": msix.RESCAP, "u": msix.UAP}
    identity = root.find("m:Identity", ns)
    assert identity.attrib == {
        "Name": "HerRei.LocalSR",
        "Version": "1.0.0.0",
        "Publisher": "CN=4A2AE7A2-7D02-49CF-8F16-C9209C756516",
        "ProcessorArchitecture": "x64",
    }
    assert root.find("m:Properties/m:PublisherDisplayName", ns).text == "HerRei"
    assert root.find("m:Dependencies/m:TargetDeviceFamily", ns).get("Name") == "Windows.Desktop"
    assert (
        root.find("m:Applications/m:Application", ns).get("EntryPoint")
        == "Windows.FullTrustApplication"
    )
    application = root.find("m:Applications/m:Application", ns)
    assert application.get(f"{{{msix.UAP10}}}RuntimeBehavior") == "packagedClassicApp"
    assert application.get(f"{{{msix.UAP10}}}TrustLevel") == "mediumIL"
    assert [node.get("Name") for node in root.findall("m:Capabilities/r:Capability", ns)] == [
        "runFullTrust"
    ]
    for logo in msix.LOGOS:
        assert (output / "layout/Assets" / logo).is_file()
    assert result["package_family_name"] == "HerRei.LocalSR_tvsg0jvwy7150"
    assert result["store_id"] == "9NTG848ZQTCQ"
    assert result["has_binaries"] is False
    assert result["windows_runtime_tested"] is False
    overlay = json.loads((output / "tauri-store.conf.json").read_text())
    assert overlay["bundle"]["createUpdaterArtifacts"] is False
    assert overlay["plugins"]["updater"]["pubkey"] == ""


def test_publisher_typo_cannot_silently_change_store_identity(tmp_path: Path) -> None:
    identity = msix.load_identity()
    identity["publisher"] = identity["publisher"].replace("49CF", "49CE")
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(identity))
    with pytest.raises(ValueError, match="package family"):
        msix.load_identity(path)


@pytest.mark.parametrize(
    "version",
    ["0.0.12-alpha", "0.0.12.0", "1.0.0", "1.0.0.1", "1.65536.0.0", "65536.0.0.0", "1.-1.0.0"],
)
def test_rejects_invalid_store_versions(version: str) -> None:
    with pytest.raises(ValueError, match="version"):
        msix.manifest(msix.load_identity(), version)


def test_stages_complete_engine_without_claiming_runtime_acceptance(tmp_path: Path) -> None:
    app, engine = artifacts(tmp_path)
    output = tmp_path / "store"
    result = msix.prepare(output, "1.0.0.0", app=app, engine=engine, backend="cpu")
    assert (output / "layout/engine/_internal/runtime.dll").read_bytes() == (
        engine / "_internal/runtime.dll"
    ).read_bytes()
    assert (output / "layout/localsr-next.exe").read_bytes() == app.read_bytes()
    assert result["has_binaries"] is True
    assert result["windows_runtime_tested"] is False
    assert result["store_certified"] is False


@pytest.mark.parametrize("machine", [0xAA64, 0x014C])
def test_rejects_other_architectures_in_engine(tmp_path: Path, machine: int) -> None:
    app, engine = artifacts(tmp_path)
    pe(engine / "_internal/runtime.dll", machine)
    with pytest.raises(ValueError, match="x64"):
        msix.prepare(tmp_path / "store", "1.0.0.0", app=app, engine=engine, backend="cuda")
    assert not (tmp_path / "store").exists()


def test_missing_runtime_is_not_a_complete_engine(tmp_path: Path) -> None:
    app = pe(tmp_path / "localsr-next.exe")
    worker = pe(tmp_path / "engine/localsr-worker.exe")
    with pytest.raises(ValueError, match="complete frozen engine"):
        msix.prepare(tmp_path / "store", "1.0.0.0", app=app, engine=worker.parent, backend="cpu")


def test_checkpoint_is_not_silently_redistributed(tmp_path: Path) -> None:
    app, engine = artifacts(tmp_path)
    (engine / "face.pth").write_bytes(b"user model")
    with pytest.raises(ValueError, match="checkpoints"):
        msix.prepare(tmp_path / "store", "1.0.0.0", app=app, engine=engine, backend="cpu")


def test_only_exact_reviewed_text_embeddings_are_allowed(tmp_path: Path) -> None:
    app, engine = artifacts(tmp_path)
    relative = "_internal/localsr/video_models/seedvr2/pos_emb.safetensors"
    conditioning = engine / relative
    conditioning.parent.mkdir(parents=True)
    source = ROOT / "src/localsr/video_models/seedvr2/pos_emb.safetensors"
    conditioning.write_bytes(source.read_bytes())
    assert msix.is_bundled_conditioning(relative, conditioning)
    assert not msix.is_bundled_conditioning("_internal/other.safetensors", conditioning)
    msix.prepare(tmp_path / "valid", "1.0.0.0", app=app, engine=engine, backend="directml")
    conditioning.write_bytes(b"different model under the same name")
    with pytest.raises(ValueError, match="checkpoints"):
        msix.prepare(tmp_path / "invalid", "1.0.0.0", app=app, engine=engine, backend="directml")


def test_existing_output_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / "store"
    output.mkdir()
    saved = output / "previous-package.msix"
    saved.write_bytes(b"existing package")
    with pytest.raises(ValueError, match="already exists"):
        msix.prepare(output, "1.0.0.0")
    assert saved.read_bytes() == b"existing package"


def test_copy_failure_removes_only_new_layout(tmp_path: Path, monkeypatch) -> None:
    app, engine = artifacts(tmp_path)
    original = app.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(msix.shutil, "copytree", fail)
    output = tmp_path / "store"
    with pytest.raises(OSError, match="disk full"):
        msix.prepare(output, "1.0.0.0", app=app, engine=engine, backend="cpu")
    assert not output.exists()
    assert app.read_bytes() == original
    assert (engine / "_internal/runtime.dll").is_file()


def test_manifest_preview_cannot_accept_partial_binary_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="together"):
        msix.prepare(tmp_path / "store", "1.0.0.0", backend="cpu")


def test_output_inside_source_engine_is_rejected(tmp_path: Path) -> None:
    app, engine = artifacts(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        msix.prepare(engine / "store", "1.0.0.0", app=app, engine=engine, backend="cpu")
