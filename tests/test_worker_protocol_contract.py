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
    VideoJobRequest,
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

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        engine.stdin.write(json.dumps({"type": "capabilities_request", "data": {}}) + "\n")
        engine.stdin.flush()
        assert done.wait(120), "the engine did not answer"
    finally:
        engine.kill()
        engine.wait()
        reader.join(timeout=5)
        assert not reader.is_alive()
    answer = answers[-1]
    assert answer["type"] == "capabilities_info", answer
    assert any(device["id"] == "cpu" for device in answer["data"]["devices"])
    assert "gpu_suggestion" in answer["data"]


def test_batch_request_is_additive_and_carries_individual_job_payloads():
    from localsr.protocol.messages import ImageBatchRequest

    jobs = [{"job_id": "first"}, {"job_id": "second"}]
    assert _message(ImageBatchRequest(jobs=jobs)) == {
        "type": "image_batch_request",
        "data": {"jobs": jobs},
    }
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    batch = schema["$defs"]["imageBatchRequest"]["allOf"][1]["properties"]
    assert batch["type"] == {"const": "image_batch_request"}
    items = batch["data"]["properties"]["jobs"]
    assert (items["minItems"], items["maxItems"]) == (2, 64)
    # Each photo is a complete job payload, as in a job_request.
    assert items["items"] == {"$ref": "#/$defs/jobRequest/allOf/1/properties/data"}
    assert {"$ref": "#/$defs/imageBatchRequest"} in schema["oneOf"]


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


def test_job_schemas_carry_heic_images_and_mov_prores_videos():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    text = json.dumps(schema)
    assert '"output_format": {"enum": ["png", "jpg", "tif", "webp", "heic"]}' in text
    assert '"container": {"enum": ["mp4", "mkv", "mov"]}' in text
    assert '"enum": ["auto", "av1", "vp9", "ffv1", "h264", "hevc", "prores"]' in text


def test_video_job_schema_declares_every_hdr_mode_the_engine_accepts():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    data = schema["$defs"]["videoJobRequest"]["allOf"][1]["properties"]["data"]["allOf"][1]
    assert set(data["properties"]["hdr_mode"]["enum"]) == {"reject", "tone_map", "preserve"}


def test_video_job_schema_declares_the_fields_the_host_sends_for_temporal_jobs():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    data = schema["$defs"]["videoJobRequest"]["allOf"][1]["properties"]["data"]["allOf"][1]
    # The worker looks a temporal job's model up by video_model_id, and puts its scratch
    # files in temporary_directory; the host sends both.
    assert data["properties"]["video_model_id"]["type"] == ["string", "null"]
    assert data["properties"]["temporary_directory"]["type"] == ["string", "null"]
    request = json.loads(
        VideoJobRequest(
            job_id="j",
            video_path="v.mp4",
            model_path="",
            output_video_path="o.mp4",
            container="mp4",
            crf=18,
            fps=None,
            device="cpu",
            tile_size=256,
            halo=16,
            precision="fp32",
            safe_memory=True,
            video_model_id="seedvr2_3b",
        ).to_json()
    )
    assert request["data"]["video_model_id"] == "seedvr2_3b"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_properties(schema: dict, node: dict) -> dict:
    """Every property a schema node declares, following `$ref` and `allOf`."""
    found: dict = {}
    if "$ref" in node:
        ref = node["$ref"].removeprefix("#/$defs/")
        if "/" not in ref:
            found.update(_schema_properties(schema, schema["$defs"][ref]))
    for part in node.get("allOf", []):
        found.update(_schema_properties(schema, part))
    found.update(node.get("properties", {}))
    return found


def _schema_data_fields(definition: str) -> set[str]:
    schema = _schema()
    envelope = _schema_properties(schema, schema["$defs"][definition])
    return set(_schema_properties(schema, envelope["data"]))


# Python request dataclass -> schema definition that describes its `data`.
REQUEST_SCHEMAS = {
    "HandshakeRequest": "handshakeRequest",
    "JobRequest": "jobRequest",
    "ImageBatchRequest": "imageBatchRequest",
    "EditJobRequest": "editJobRequest",
    "VideoJobRequest": "videoJobRequest",
    "BenchmarkRequest": "benchmarkRequest",
    "RegionRequest": "regionRequest",
    # preview_request and media_probe_request share one property bag, so the
    # dataclass may use a subset of it (see test below).
    "MediaProbeRequest": "pathRequest",
}
# Dataclass fields that stay out of the schema on purpose: none today.
DATACLASS_ONLY_FIELDS: dict[str, set[str]] = {}
# Schema properties without a dataclass field: the pathRequest bag also serves
# preview_request, and the host sends the external ffmpeg path with probes.
SCHEMA_ONLY_FIELDS = {
    "MediaProbeRequest": {
        "image_path",
        "comparison",
        "slot",
        "generation",
        "target_id",
        "external_ffmpeg",
    },
}


def test_request_dataclass_fields_equal_the_schema_data_properties():
    import dataclasses

    from localsr.protocol import messages

    for class_name, definition in REQUEST_SCHEMAS.items():
        fields = {f.name for f in dataclasses.fields(getattr(messages, class_name))}
        declared = _schema_data_fields(definition)
        assert fields - DATACLASS_ONLY_FIELDS.get(class_name, set()) == declared - (
            SCHEMA_ONLY_FIELDS.get(class_name, set())
        ), class_name


def test_every_python_response_type_is_in_the_schema_response_enum_and_back():
    import re

    schema = _schema()
    response = _schema_properties(schema, schema["$defs"]["workerResponse"])["type"]["enum"]
    messages = (ROOT / "src" / "localsr" / "protocol" / "messages.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'json\.dumps\(\{"type": "([a-z_]+)"', messages))
    # Types that have their own schema definitions are not in the response enum.
    own_definitions = {
        _schema_properties(schema, schema["$defs"][name])["type"]["const"]
        for name in schema["$defs"]
        if "type" in _schema_properties(schema, schema["$defs"][name])
        and "const" in _schema_properties(schema, schema["$defs"][name])["type"]
    }
    request_enums = {
        value
        for name in ("emptyRequest", "pathRequest")
        for value in _schema_properties(schema, schema["$defs"][name])["type"]["enum"]
    }
    assert set(response) == emitted - own_definitions - request_enums
    assert len(response) == len(set(response))


# Response types the worker sends that neither the Rust host (worker.rs) nor the
# reducer (state.ts) acts on. `log` carries the worker's own diagnostics, such as
# a malformed request or a lost GPU; the host drops them today.
RESPONSES_NOBODY_HANDLES = {"log"}


def test_every_response_type_has_a_host_or_ui_handler_except_the_listed_ones():
    schema = _schema()
    response = _schema_properties(schema, schema["$defs"]["workerResponse"])["type"]["enum"]
    host = (ROOT / "desktop" / "src-tauri" / "src" / "worker.rs").read_text(encoding="utf-8")
    reducer = (ROOT / "desktop" / "src" / "lib" / "state.ts").read_text(encoding="utf-8")
    unhandled = {
        name for name in response if f'"{name}"' not in host and f"'{name}'" not in reducer
    }
    assert unhandled == RESPONSES_NOBODY_HANDLES


def _rust_struct_fields(source: str) -> dict[str, set[str]]:
    import re

    structs = {}
    for match in re.finditer(
        r"pub struct (\w+) \{\n(.*?)\n\}", source.split("#[cfg(test)]")[0], re.S
    ):
        names: set[str] = set()
        skipped, renamed = False, None
        for line in match.group(2).splitlines():
            line = line.strip()
            if line == "#[serde(skip)]":
                skipped = True
            elif rename := re.match(r'#\[serde\(rename = "(\w+)"\)\]', line):
                renamed = rename.group(1)
            elif field := re.match(r"pub (\w+):", line):
                if not skipped:
                    names.add(renamed or field.group(1))
                skipped, renamed = False, None
        structs[match.group(1)] = names
    return structs


def _ts_interface_fields(source: str) -> dict[str, set[str]]:
    import re

    return {
        match.group(1): set(re.findall(r"^  (\w+)\??:", match.group(2), re.M))
        for match in re.finditer(r"export interface (\w+) \{\n(.*?)\n\}", source, re.S)
    }


# Deliberate differences between types.rs and types.ts, as (rust-only, ts-only).
# MediaItem: the two probe fields only the webview tracks while a probe runs.
# VideoComparisonSources: the host sends file paths, api.ts turns them into URLs.
RUST_TS_DIFFERENCES = {
    "MediaItem": (set(), {"probe_stage", "probe_started_at"}),
    "VideoComparisonSources": ({"original_path", "enhanced_path"}, set()),
}


def test_rust_and_typescript_contract_types_have_the_same_field_names():
    rust = _rust_struct_fields(
        (ROOT / "desktop" / "src-tauri" / "src" / "types.rs").read_text(encoding="utf-8")
    )
    typescript = _ts_interface_fields(
        (ROOT / "desktop" / "src" / "lib" / "types.ts").read_text(encoding="utf-8")
    )
    shared = set(rust) & set(typescript)
    # Guard against the parsers silently matching nothing.
    assert {"AppSnapshot", "UiSettings", "CatalogModel", "WorkerEnvelope"} <= shared
    for name in sorted(shared):
        rust_only, ts_only = RUST_TS_DIFFERENCES.get(name, (set(), set()))
        assert rust[name] - typescript[name] == rust_only, name
        assert typescript[name] - rust[name] == ts_only, name
