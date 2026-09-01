import json
import tomllib
from pathlib import Path

from localsr import __version__
from localsr.protocol.messages import (
    PROTOCOL_VERSION,
    EngineInfo,
    HandshakeRequest,
    JobRequest,
    MediaInfo,
    MediaProbeRequest,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "protocol" / "worker-protocol.schema.json"


def _message(value):
    return json.loads(value.to_json())


def test_protocol_schema_and_python_version_stay_synchronized():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["x-protocol-version"] == PROTOCOL_VERSION == 1
    rust_types = (ROOT / "desktop" / "src-tauri" / "src" / "types.rs").read_text(encoding="utf-8")
    assert f"pub const PROTOCOL_VERSION: u32 = {PROTOCOL_VERSION};" in rust_types


def test_additive_desktop_metadata_uses_one_version():
    npm = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    tauri = json.loads(
        (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    cargo = tomllib.loads(
        (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    )
    assert npm["version"] == tauri["version"] == cargo["package"]["version"] == __version__
    assert tauri["identifier"] == "com.localsr.desktop.next"
    legacy_bundle = (ROOT / "packaging" / "localsr.spec").read_text(encoding="utf-8")
    assert 'bundle_identifier="com.localsr.desktop"' in legacy_bundle
    assert tauri["identifier"] != "com.localsr.desktop"


def test_handshake_and_engine_info_are_language_neutral_envelopes():
    request = _message(HandshakeRequest(client_name="test-host", client_version="0.0.9-alpha"))
    assert request == {
        "type": "handshake_request",
        "data": {
            "client_name": "test-host",
            "client_version": "0.0.9-alpha",
            "protocol_version": 1,
        },
    }
    response = _message(
        EngineInfo(
            protocol_version=1,
            minimum_protocol_version=1,
            engine_id="localsr.pytorch-spandrel",
            engine_version="0.0.9-alpha",
            features=["image", "video_frame"],
            model_formats=[".safetensors"],
            video_engines=["spandrel_image"],
        )
    )
    assert response["type"] == "engine_info"
    assert response["data"]["protocol_version"] == 1


def test_media_probe_supports_image_and_video_metadata():
    request = _message(MediaProbeRequest(media_path="clip.mkv", max_dimension=1200))
    assert request["type"] == "media_probe_request"
    assert request["data"]["max_dimension"] == 1200
    response = _message(
        MediaInfo(
            media_path="clip.mkv",
            media_kind="video",
            width=1920,
            height=1080,
            frame_count=240,
            fps=24.0,
            duration_seconds=10.0,
        )
    )
    assert response["data"]["frame_count"] == 240
    assert response["data"]["duration_seconds"] == 10.0


def test_pickle_opt_in_is_explicit_per_job():
    request = _message(
        JobRequest(
            job_id="job-1",
            image_path="image.png",
            model_path="custom.pth",
            output_path="output.png",
            output_format="png",
            device="cpu",
            tile_size=128,
            halo=16,
            precision="fp32",
            jpeg_quality=98,
            preserve_metadata=True,
            safe_memory=True,
            allow_unverified_checkpoint=True,
        )
    )
    assert request["data"]["allow_unverified_checkpoint"] is True
