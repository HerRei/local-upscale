import json
import tomllib
from pathlib import Path

from localsr import __version__
from localsr.protocol.messages import (
    PROTOCOL_VERSION,
    BenchmarkCompleted,
    BenchmarkRequest,
    BenchmarkStageCompleted,
    BenchmarkStageProgress,
    BenchmarkStageStarted,
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


def test_preview_request_schema_supports_identified_pair_slots():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    fields = schema["$defs"]["pathRequest"]["allOf"][1]["properties"]["data"]["properties"]
    assert fields["slot"]["enum"] == ["showing", "against", "slot1", "slot2", "slot3", "slot4"]
    assert fields["generation"]["minimum"] == 0
    assert fields["target_id"]["type"] == "string"


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
    assert tauri["identifier"] != "com.localsr.desktop"


def test_handshake_and_engine_info_are_language_neutral_envelopes():
    request = _message(HandshakeRequest(client_name="test-host", client_version="0.0.11-alpha"))
    assert request == {
        "type": "handshake_request",
        "data": {
            "client_name": "test-host",
            "client_version": "0.0.11-alpha",
            "protocol_version": 1,
        },
    }
    response = _message(
        EngineInfo(
            protocol_version=1,
            minimum_protocol_version=1,
            engine_id="localsr.pytorch-spandrel",
            engine_version="0.0.11-alpha",
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


def test_benchmark_protocol_carries_a_versioned_real_workload_result():
    request = _message(
        BenchmarkRequest(
            job_id="benchmark-1",
            model_path="quick.pth",
            model_id="span_photo_x4",
            model_name="SPAN Quick",
            device="cpu",
            workload="v2",
        )
    )
    assert request["type"] == "benchmark_request"
    assert request["data"]["workload"] == "v2"
    completed = _message(
        BenchmarkCompleted(
            job_id="benchmark-1",
            result={"workload_version": "localsr-benchmark-v1", "score": 42.5},
        )
    )
    assert completed["data"]["result"]["workload_version"] == "localsr-benchmark-v1"


def test_benchmark_v2_stage_envelopes_are_additive_protocol_messages():
    common = {
        "job_id": "benchmark-1",
        "device": "mps",
        "stage": "mps:s1-classroom",
        "stage_index": 2,
        "stage_count": 10,
        "percentage": 15.0,
    }
    started = _message(BenchmarkStageStarted(**common))
    progress = _message(BenchmarkStageProgress(**common, completed_units=3, total_units=0))
    completed = _message(BenchmarkStageCompleted(**common))
    assert started["type"] == "benchmark_stage_started"
    assert progress["type"] == "benchmark_stage_progress"
    assert progress["data"]["completed_units"] == 3
    assert completed["type"] == "benchmark_stage_completed"


def _schema_message_types(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        type_rule = node.get("properties", {}).get("type")
        if isinstance(type_rule, dict):
            if isinstance(type_rule.get("const"), str):
                found.add(type_rule["const"])
            found.update(value for value in type_rule.get("enum", []) if isinstance(value, str))
        for value in node.values():
            found |= _schema_message_types(value)
    elif isinstance(node, list):
        for value in node:
            found |= _schema_message_types(value)
    return found


def test_every_python_protocol_message_type_is_declared_in_the_schema():
    import re

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    messages = (ROOT / "src" / "localsr" / "protocol" / "messages.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'json\.dumps\(\{"type": "([a-z_]+)"', messages))
    assert emitted
    assert emitted - _schema_message_types(schema) == set()


def test_the_engine_answers_a_capabilities_request_with_its_hardware():
    # Through the real engine process: a report key the message did not know made the
    # engine drop the whole answer, and the app showed no hardware at all (0.3.2).
    import os
    import subprocess
    import sys
    import threading

    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUNBUFFERED="1")
    engine = subprocess.Popen(
        [sys.executable, "-m", "localsr.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    answers = []
    done = threading.Event()

    def read():
        for line in engine.stdout:
            answers.append(json.loads(line))
            if answers[-1]["type"] in ("capabilities_info", "log") and (
                answers[-1]["type"] == "capabilities_info"
                or answers[-1]["data"].get("level") == "error"
            ):
                done.set()
                return

    threading.Thread(target=read, daemon=True).start()
    try:
        engine.stdin.write(json.dumps({"type": "capabilities_request", "data": {}}) + "\n")
        engine.stdin.flush()
        assert done.wait(120), "the engine did not answer"
    finally:
        engine.kill()
        engine.wait()
    answer = answers[-1]
    assert answer["type"] == "capabilities_info", answer
    assert any(device["id"] == "cpu" for device in answer["data"]["devices"])
    assert "gpu_suggestion" in answer["data"]


def test_compare_region_protocol_has_identity_and_bounds():
    from localsr.protocol.messages import RegionFailed, RegionReady, RegionRequest

    request = RegionRequest("image.png", "slot1", 2, "job", 3, 1, 2, 30, 40, 60, 80)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    fields = schema["$defs"]["regionRequest"]["allOf"][1]["properties"]["data"]["required"]
    assert set(_message(request)["data"]) == set(fields)
    for result in (
        RegionReady("image.png", "slot1", 2, "job", 3, "pixels"),
        RegionFailed("image.png", "slot1", 2, "job", 3, "error"),
    ):
        assert _message(result)["data"]["request_id"] == 3
