"""The worker holds a request to the bounds protocol/worker-protocol.schema.json publishes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from localsr.worker.request_validation import (
    EDIT_JOB_FIELDS,
    IMAGE_JOB_FIELDS,
    VIDEO_JOB_FIELDS,
    InvalidRequest,
    usable_job_id,
    validate_edit_job,
    validate_image_job,
    validate_probe,
    validate_video_job,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "protocol" / "worker-protocol.schema.json").read_text(encoding="utf-8"))
SAMPLES = json.loads(
    (ROOT / "tests" / "fixtures" / "job_request_samples.json").read_text(encoding="utf-8")
)


def image_request(**overrides):
    data = {
        "job_id": "job-1",
        "image_path": "/in/a.png",
        "model_path": "/models/m.safetensors",
        "output_path": "/out/a.png",
        "output_format": "png",
        "device": "cpu",
        "tile_size": 256,
        "halo": 16,
        "precision": "fp32",
        "jpeg_quality": 90,
        "preserve_metadata": True,
        "safe_memory": True,
        "neural_engine": False,
        "preview_enabled": True,
        "preview_max_fps": 2.0,
        "preview_max_dimension": 320,
        "output_scale": 4,
        "face_fidelity": 0.7,
        "face_model_path": None,
        "stages": None,
    }
    data.update(overrides)
    return data


def video_request(**overrides):
    data = {
        "job_id": "job-1",
        "video_path": "/in/a.mp4",
        "model_path": "/models/m.safetensors",
        "output_video_path": "/out/a.mp4",
        "container": "mp4",
        "video_codec": "av1",
        "crf": 18,
        "fps": None,
        "device": "cpu",
        "tile_size": 256,
        "halo": 16,
        "precision": "fp32",
        "safe_memory": True,
        "start_frame": None,
        "end_frame": None,
        "deflicker_window": 3,
        "model_kind": "spandrel_image",
        "hdr_mode": "tone_map",
        "interpolation": "off",
        "interpolation_order": "after_upscale",
        "output_scale": 4,
    }
    data.update(overrides)
    return data


def test_the_requests_the_host_sends_are_accepted():
    validate_image_job(image_request())
    validate_image_job(
        image_request(
            output_format="heic",
            output_scale=None,
            stages=[{"kind": "upscale", "model_id": "m", "model_path": "/models/m.safetensors"}],
        )
    )
    validate_video_job(video_request())
    validate_video_job(video_request(fps=29.97, start_frame=0, end_frame=9, container="mov"))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("tile_size", -5),
        ("tile_size", 0),
        ("tile_size", 31),
        ("tile_size", 2049),
        ("tile_size", "64"),
        ("tile_size", 64.5),
        ("tile_size", True),
        ("tile_size", None),
        ("halo", -1),
        ("halo", 257),
        ("halo", 10**6),
        ("precision", "bogus"),
        ("jpeg_quality", -1),
        ("jpeg_quality", 0),
        ("jpeg_quality", 101),
        ("jpeg_quality", 10**9),
        ("jpeg_quality", float("nan")),
        ("jpeg_quality", "90"),
        ("output_scale", 0),
        ("output_scale", -1),
        ("output_scale", 9),
        ("output_scale", 2.5),
        ("output_scale", True),
        ("output_scale", "2"),
        ("output_format", "pdf"),
        ("output_format", "ico"),
        ("output_format", "gif"),
        ("output_format", ""),
        ("output_format", ".png"),
        ("preview_max_fps", 0),
        ("preview_max_fps", 6),
        ("preview_max_dimension", 63),
        ("face_fidelity", 1.5),
        ("safe_memory", "yes"),
        ("device", ""),
        ("image_path", ""),
        ("output_path", ""),
        ("job_id", ""),
        ("job_id", ["a"]),
        ("job_id", 5),
    ],
)
def test_an_image_request_outside_the_schema_is_refused_with_its_field_named(field, bad):
    with pytest.raises(InvalidRequest, match=field):
        validate_image_job(image_request(**{field: bad}))


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("crf", -1),
        ("crf", 52),
        ("fps", 0),
        ("fps", -5),
        ("fps", float("nan")),
        ("start_frame", -5),
        ("end_frame", -1),
        ("container", "avi"),
        ("video_codec", "xyz"),
        ("model_kind", "other"),
        ("deflicker_window", 0),
        ("deflicker_window", 10),
        ("interpolation", "x3"),
        ("hdr_mode", "keep"),
        ("tile_size", 10**9),
    ],
)
def test_a_video_request_outside_the_schema_is_refused_with_its_field_named(field, bad):
    with pytest.raises(InvalidRequest, match=field):
        validate_video_job(video_request(**{field: bad}))


@pytest.mark.parametrize("missing", ["job_id", "image_path", "tile_size", "jpeg_quality"])
def test_a_missing_required_field_is_named(missing):
    data = image_request()
    del data[missing]
    with pytest.raises(InvalidRequest, match=f'missing "{missing}"'):
        validate_image_job(data)


def test_an_output_name_the_file_system_cannot_hold_is_refused_before_any_work(tmp_path):
    longest = "y" * 251 + ".png"
    validate_image_job(image_request(output_path=str(tmp_path / longest)))
    with pytest.raises(InvalidRequest, match="256 characters long.*at most 255"):
        validate_image_job(image_request(output_path=str(tmp_path / ("y" + longest))))
    with pytest.raises(InvalidRequest, match="at most 255"):
        validate_video_job(video_request(output_video_path=str(tmp_path / ("y" * 252 + ".mp4"))))


def test_a_request_that_is_not_an_object_is_refused():
    with pytest.raises(InvalidRequest, match="data object"):
        validate_image_job("a string")


def test_usable_job_id_is_a_non_empty_text_only():
    assert usable_job_id({"job_id": "a"}) == "a"
    for data in ({"job_id": ""}, {"job_id": ["a"]}, {"job_id": 3}, {}, None, "x"):
        assert usable_job_id(data) is None


def test_the_table_agrees_with_the_published_schema():
    base = SCHEMA["$defs"]["baseJobData"]["properties"]
    published = {
        "tile_size": (32, 2048),
        "halo": (0, 256),
        "preview_max_dimension": (64, 640),
        "face_fidelity": (0, 1),
    }
    fields = {field.name: field for field in IMAGE_JOB_FIELDS}
    for name, (low, high) in published.items():
        assert (base[name]["minimum"], base[name]["maximum"]) == (low, high)
        assert (fields[name].minimum, fields[name].maximum) == (low, high)
    assert tuple(base["precision"]["enum"]) == fields["precision"].choices
    video = SCHEMA["$defs"]["videoJobRequest"]["allOf"][1]["properties"]["data"]["allOf"][1][
        "properties"
    ]
    video_fields = {field.name: field for field in VIDEO_JOB_FIELDS}
    for name in ("container", "video_codec", "model_kind", "hdr_mode", "interpolation"):
        assert tuple(video[name]["enum"]) == video_fields[name].choices
    assert (video["crf"]["minimum"], video["crf"]["maximum"]) == (
        video_fields["crf"].minimum,
        video_fields["crf"].maximum,
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_pipe_as_a_source_is_refused_before_it_is_opened(tmp_path):
    pipe = tmp_path / "source"
    os.mkfifo(pipe)
    with pytest.raises(InvalidRequest, match="pipe, device or socket"):
        validate_image_job(image_request(image_path=str(pipe)))
    with pytest.raises(InvalidRequest, match="pipe, device or socket"):
        validate_video_job(video_request(video_path=str(pipe)))
    with pytest.raises(InvalidRequest, match="pipe, device or socket"):
        validate_probe({"media_path": str(pipe)})
    # A missing file or a folder keeps the reader's own message.
    validate_image_job(image_request(image_path=str(tmp_path / "missing.png")))
    validate_image_job(image_request(image_path=str(tmp_path)))


@pytest.mark.parametrize(
    "make_request,validate",
    [(image_request, validate_image_job), (video_request, validate_video_job)],
)
def test_checkpoint_digest_must_be_sha256(make_request, validate):
    validate(make_request(approved_checkpoint_sha256="a" * 64))
    with pytest.raises(InvalidRequest, match="approved_checkpoint_sha256"):
        validate(make_request(approved_checkpoint_sha256="not-a-sha256"))


def _schema_props(definition: str) -> tuple[dict, set[str]]:
    def _collect(node: dict, props: dict, required: set[str]) -> None:
        if "$ref" in node:
            target: object = SCHEMA
            for part in node["$ref"].removeprefix("#/").split("/"):
                target = target[int(part)] if isinstance(target, list) else target[part]
            assert isinstance(target, dict)
            _collect(target, props, required)
        for part in node.get("allOf", []):
            _collect(part, props, required)
        required.update(node.get("required", ()))
        props.update(node.get("properties", {}))

    envelope_props: dict = {}
    _collect(SCHEMA["$defs"][definition], envelope_props, set())
    props: dict = {}
    required: set[str] = set()
    _collect(envelope_props["data"], props, required)
    return props, required


@pytest.mark.parametrize(
    ("definition", "fields"),
    [
        ("jobRequest", IMAGE_JOB_FIELDS),
        ("videoJobRequest", VIDEO_JOB_FIELDS),
        ("editJobRequest", EDIT_JOB_FIELDS),
    ],
)
def test_derived_fields_match_schema_properties_and_required(definition, fields):
    props, required = _schema_props(definition)
    assert {field.name for field in fields} == set(props)
    assert {field.name for field in fields if field.required} == required


def test_rust_job_request_samples_are_accepted_and_bad_types_rejected(monkeypatch):
    from localsr.worker import server as worker_server

    validators = {
        "job_request": validate_image_job,
        "video_job_request": validate_video_job,
        "edit_job_request": validate_edit_job,
    }
    accepted: list[str] = []
    monkeypatch.setattr(worker_server, "send_message", lambda msg: accepted.append(msg.to_json()))

    for name, sample in SAMPLES.items():
        req_type = sample["type"]
        data = sample["data"]
        validators[req_type](data)
        if req_type in ("job_request", "video_job_request"):
            assert worker_server._accepts(data, validators[req_type]), name

    # Reject a field with the wrong/unknown type and an unknown field name.
    bad_image = {**SAMPLES["image_upscale_fixes_and_face"]["data"], "tile_size": "256"}
    assert not worker_server._accepts(bad_image, validate_image_job)
    assert "tile_size" in accepted[-1]

    bad_video = {**SAMPLES["video_temporal_model"]["data"], "crf": "18"}
    assert not worker_server._accepts(bad_video, validate_video_job)
    assert "crf" in accepted[-1]

    with pytest.raises(InvalidRequest, match="steps"):
        validate_edit_job({**SAMPLES["edit_catalog_model"]["data"], "steps": "40"})

    with pytest.raises(InvalidRequest, match="unknown_field"):
        validate_image_job({**SAMPLES["image_upscale_fixes_and_face"]["data"], "unknown_field": 1})

    assert not worker_server._accepts(
        {**SAMPLES["video_interpolation_deflicker_audio"]["data"], "unknown_field": 1},
        validate_video_job,
    )


def test_old_0_6_1_requests_are_accepted():
    from localsr.core.pipeline import resolve_image_pipeline

    old_image = {
        "job_id": "061-img",
        "image_path": "/in/photo.png",
        "model_path": "/models/span.pth",
        "output_path": "/out/photo_upscaled_4x.png",
        "scratch_directory": "/work/061",
        "output_format": "png",
        "device": "mps",
        "tile_size": 256,
        "halo": 32,
        "precision": "fp32",
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": False,
        "neural_engine": True,
        "preview_enabled": True,
        "preview_max_fps": 2.0,
        "preview_max_dimension": 320,
        "output_scale": 4,
        "face_model_path": None,
        "face_fidelity": 0.7,
        "stages": [
            {
                "kind": "upscale",
                "role": "primary",
                "fix": None,
                "model_id": "span_photo_x4",
                "model_path": "/models/span.pth",
            }
        ],
        "allow_unverified_checkpoint": False,
        "approved_checkpoint_sha256": None,
    }
    validate_image_job(old_image)
    pipeline = resolve_image_pipeline(
        raw_stages=old_image["stages"],
        model_path=old_image["model_path"],
        output_scale=old_image["output_scale"],
    )
    assert pipeline.primary is not None and pipeline.primary.kind == "upscale"

    old_video = {
        "job_id": "061-vid",
        "video_path": "/in/clip.mp4",
        "hdr_mode": "tone_map",
        "model_path": "/models/span.pth",
        "output_video_path": "/out/clip_video_4x.mp4",
        "container": "mp4",
        "video_codec": "av1",
        "external_ffmpeg": "",
        "crf": 18,
        "fps": None,
        "device": "cpu",
        "tile_size": 256,
        "halo": 32,
        "precision": "fp32",
        "safe_memory": False,
        "neural_engine": True,
        "keyframe_interval": None,
        "start_frame": None,
        "end_frame": None,
        "face_model_path": None,
        "face_fidelity": 0.7,
        "deflicker": False,
        "deflicker_window": 3,
        "model_kind": "spandrel_image",
        "bundle_dir": None,
        "temporal_window": 0,
        "temporal_overlap": 0,
        "target_resolution": 0,
        "output_scale": 4,
        "stages": old_image["stages"],
        "allow_unverified_checkpoint": False,
        "approved_checkpoint_sha256": None,
        "preview_enabled": True,
        "preview_max_fps": 2.0,
        "preview_max_dimension": 320,
        "interpolation": "off",
        "interpolation_order": "after_upscale",
        "interpolation_model_id": None,
        "interpolation_bundle_dir": None,
        "audio_denoise": False,
        "audio_denoise_model_id": None,
        "audio_denoise_bundle_dir": None,
    }
    validate_video_job(old_video)
    validate_edit_job(SAMPLES["edit_catalog_model"]["data"])


def test_the_packaged_schema_copy_matches_the_protocol_schema():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    packaged = root / "src" / "localsr" / "protocol" / "worker-protocol.schema.json"
    source = root / "protocol" / "worker-protocol.schema.json"
    assert packaged.read_bytes() == source.read_bytes(), (
        "Copy protocol/worker-protocol.schema.json to src/localsr/protocol/ after editing it."
    )


def test_edit_placement_fields_validated_against_schema():
    base = SAMPLES["edit_catalog_model"]["data"]
    for choice in ("auto", "gpu", "cpu"):
        validate_edit_job({**base, "edit_text_encoder": choice, "edit_vae": choice})
    for bad in ("tpu", 123, None):
        with pytest.raises(InvalidRequest, match="edit_text_encoder"):
            validate_edit_job({**base, "edit_text_encoder": bad})
        with pytest.raises(InvalidRequest, match="edit_vae"):
            validate_edit_job({**base, "edit_vae": bad})


def test_auto_precision_is_accepted_by_schema_and_validators():
    assert "auto" in SCHEMA["$defs"]["baseJobData"]["properties"]["precision"]["enum"]
    validate_image_job(image_request(precision="auto"))
    validate_video_job(video_request(precision="auto"))
