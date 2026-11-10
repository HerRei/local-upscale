"""The worker holds a request to the bounds protocol/worker-protocol.schema.json publishes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from localsr.worker.request_validation import (
    IMAGE_JOB_FIELDS,
    VIDEO_JOB_FIELDS,
    InvalidRequest,
    usable_job_id,
    validate_image_job,
    validate_video_job,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "protocol" / "worker-protocol.schema.json").read_text(
        encoding="utf-8"
    )
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
    validate_image_job(image_request(output_format="heic", output_scale=None, stages=[{}]))
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
