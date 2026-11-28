"""Check a worker request against the bounds protocol/worker-protocol.schema.json publishes.

The desktop app validates its settings before it sends a request, but the worker is a
separate process with its own input: a value the schema forbids must end the job with
one clear sentence before any image is decoded or model loaded, not as a black picture,
a multi-gigabyte allocation or a raw Python error halfway through.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from localsr.core.image_formats import require_ordinary_source
from localsr.core.output_publish import require_output_name


class InvalidRequest(ValueError):
    """The request breaks the published protocol; the message is shown to the user."""


@dataclass(frozen=True)
class ObjectSpec:
    fields: tuple[Field, ...]
    any_of_required: tuple[tuple[str, ...], ...] = ()
    reject_unknown: bool = False
    field_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # "str", "int", "number", "bool", "choice", "int_choice", "array" or "object"
    required: bool = False
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    above: float | None = None  # exclusive lower bound
    choices: tuple[str, ...] = ()
    int_choices: tuple[int, ...] = ()
    non_empty: bool = False
    max_length: int | None = None
    pattern: str | None = None
    min_items: int | None = None
    max_items: int | None = None
    item_spec: ObjectSpec | None = None
    value_field: Field | None = None


def _schema_path() -> Path:
    # The package carries a copy (wheel and CLI installs have no repository around them);
    # tests/test_request_validation.py keeps it identical to protocol/.
    packaged = Path(__file__).resolve().parents[1] / "protocol" / "worker-protocol.schema.json"
    if packaged.is_file():
        return packaged
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "protocol" / "worker-protocol.schema.json"
        if bundled.is_file():
            return bundled
    return Path(__file__).resolve().parents[3] / "protocol" / "worker-protocol.schema.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def _resolve_ref(schema: dict[str, Any], ref: str) -> dict[str, Any]:
    target: Any = schema
    for part in ref.removeprefix("#/").split("/"):
        target = target[int(part)] if isinstance(target, list) else target[part]
    return target


def _collect_object_parts(
    schema: dict[str, Any],
    node: dict[str, Any],
    properties: dict[str, dict[str, Any]],
    required: set[str],
    any_of_required: list[tuple[str, ...]],
) -> bool:
    reject_unknown = (
        node.get("additionalProperties") is False or node.get("unevaluatedProperties") is False
    )
    if "$ref" in node:
        reject_unknown = (
            _collect_object_parts(
                schema,
                _resolve_ref(schema, node["$ref"]),
                properties,
                required,
                any_of_required,
            )
            or reject_unknown
        )
    for part in node.get("allOf", []):
        reject_unknown = (
            _collect_object_parts(schema, part, properties, required, any_of_required)
            or reject_unknown
        )
    if "anyOf" in node:
        options = tuple(
            entry["required"][0]
            for entry in node["anyOf"]
            if isinstance(entry, dict) and len(entry.get("required", [])) == 1
        )
        if options:
            any_of_required.append(options)
    required.update(node.get("required", ()))
    properties.update(node.get("properties", {}))
    return reject_unknown


def _field_from_schema(
    schema: dict[str, Any],
    name: str,
    prop: dict[str, Any],
    *,
    required: bool,
) -> Field:
    raw_type = prop.get("type")
    types = (raw_type,) if isinstance(raw_type, str) else tuple(raw_type or ())
    nullable = "null" in types
    non_null_types = [entry for entry in types if entry != "null"]

    if "enum" in prop:
        values = tuple(prop["enum"])
        if values and all(isinstance(v, bool) or not isinstance(v, int) for v in values):
            return Field(
                name,
                "choice",
                required=required,
                nullable=nullable,
                choices=tuple(str(v) for v in values),
            )
        return Field(
            name,
            "int_choice",
            required=required,
            nullable=nullable,
            int_choices=tuple(int(v) for v in values),
        )

    json_type = non_null_types[0] if non_null_types else "str"
    if json_type == "string":
        return Field(
            name,
            "str",
            required=required,
            nullable=nullable,
            non_empty=int(prop.get("minLength", 0)) >= 1,
            max_length=prop.get("maxLength"),
            pattern=prop.get("pattern"),
        )
    if json_type == "boolean":
        return Field(name, "bool", required=required, nullable=nullable)
    if json_type in ("integer", "number"):
        return Field(
            name,
            "int" if json_type == "integer" else "number",
            required=required,
            nullable=nullable,
            minimum=prop.get("minimum"),
            maximum=prop.get("maximum"),
            above=prop.get("exclusiveMinimum"),
        )
    if json_type == "array":
        items = prop.get("items")
        item_spec = _compile_object_spec(schema, items) if isinstance(items, dict) else None
        return Field(
            name,
            "array",
            required=required,
            nullable=nullable,
            min_items=prop.get("minItems"),
            max_items=prop.get("maxItems"),
            item_spec=item_spec,
        )
    if json_type == "object":
        extra = prop.get("additionalProperties")
        value_field = (
            _field_from_schema(schema, f"{name} value", extra, required=False)
            if isinstance(extra, dict)
            else None
        )
        return Field(
            name,
            "object",
            required=required,
            nullable=nullable,
            value_field=value_field,
        )
    raise ValueError(f"Unsupported schema type {raw_type!r} for {name}")


def _compile_object_spec(schema: dict[str, Any], node: dict[str, Any]) -> ObjectSpec:
    properties: dict[str, dict[str, Any]] = {}
    required: set[str] = set()
    any_of_required: list[tuple[str, ...]] = []
    reject_unknown = _collect_object_parts(schema, node, properties, required, any_of_required)
    fields = tuple(
        _field_from_schema(schema, name, prop, required=name in required)
        for name, prop in properties.items()
    )
    return ObjectSpec(
        fields=fields,
        any_of_required=tuple(any_of_required),
        reject_unknown=reject_unknown,
        field_names=frozenset(properties),
    )


def _compile_request_spec(schema: dict[str, Any], definition: str) -> ObjectSpec:
    properties: dict[str, dict[str, Any]] = {}
    required: set[str] = set()
    any_of_required: list[tuple[str, ...]] = []
    _collect_object_parts(
        schema, schema["$defs"][definition], properties, required, any_of_required
    )
    return _compile_object_spec(schema, properties["data"])


_SCHEMA = _load_schema()
_IMAGE_JOB_SPEC = _compile_request_spec(_SCHEMA, "jobRequest")
_VIDEO_JOB_SPEC = _compile_request_spec(_SCHEMA, "videoJobRequest")
_EDIT_JOB_SPEC = _compile_request_spec(_SCHEMA, "editJobRequest")
_PATCH_SPEC = _compile_request_spec(_SCHEMA, "patchRequest")
_PATCH_REGION_SPEC = _compile_object_spec(
    _SCHEMA,
    _SCHEMA["$defs"]["patchRequest"]["allOf"][1]["properties"]["data"]["properties"]["region"],
)

IMAGE_JOB_FIELDS = _IMAGE_JOB_SPEC.fields
VIDEO_JOB_FIELDS = _VIDEO_JOB_SPEC.fields
EDIT_JOB_FIELDS = _EDIT_JOB_SPEC.fields

PROBE_FIELDS = (
    Field("media_path", "str", required=True),
    Field("max_dimension", "int", minimum=64, maximum=2048),
)


def _describe(field: Field) -> str:
    if field.kind == "choice":
        return "one of " + ", ".join(field.choices)
    if field.kind == "int_choice":
        return "one of " + ", ".join(str(choice) for choice in field.int_choices)
    if field.kind == "bool":
        return "true or false"
    if field.kind == "str":
        return "a non-empty text" if field.non_empty else "text"
    if field.kind == "array":
        return "a list"
    if field.kind == "object":
        return "an object"
    noun = "a whole number" if field.kind == "int" else "a number"
    if field.minimum is not None and field.maximum is not None:
        return f"{noun} from {field.minimum:g} to {field.maximum:g}"
    if field.minimum is not None:
        return f"{noun} of at least {field.minimum:g}"
    if field.above is not None and field.maximum is not None:
        return f"{noun} above {field.above:g} and at most {field.maximum:g}"
    if field.above is not None:
        return f"{noun} above {field.above:g}"
    return noun


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _acceptable(field: Field, value: Any) -> bool:
    if field.kind == "str":
        if not isinstance(value, str):
            return False
        if field.non_empty and not value:
            return False
        if field.max_length is not None and len(value) > field.max_length:
            return False
        return field.pattern is None or re.fullmatch(field.pattern, value) is not None
    if field.kind == "bool":
        return isinstance(value, bool)
    if field.kind == "choice":
        return isinstance(value, str) and value in field.choices
    if field.kind == "int_choice":
        return not isinstance(value, bool) and isinstance(value, int) and value in field.int_choices
    if field.kind == "array":
        if not isinstance(value, list):
            return False
        if field.min_items is not None and len(value) < field.min_items:
            return False
        return field.max_items is None or len(value) <= field.max_items
    if field.kind == "object":
        if not isinstance(value, dict):
            return False
        if field.value_field is not None:
            return all(
                isinstance(key, str) and _acceptable(field.value_field, item)
                for key, item in value.items()
            )
        return True
    if isinstance(value, bool):
        return False
    if field.kind == "int":
        if not isinstance(value, int):
            return False
    elif not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if field.minimum is not None and value < field.minimum:
        return False
    if field.maximum is not None and value > field.maximum:
        return False
    return field.above is None or value > field.above


def _validate_object(data: Any, spec: ObjectSpec, request: str) -> None:
    if not isinstance(data, dict):
        raise InvalidRequest(f"The {request} has no data object.")
    if spec.reject_unknown:
        for key in data:
            if key not in spec.field_names:
                raise InvalidRequest(f'The {request} has an unknown field "{key}".')
    for group in spec.any_of_required:
        if not any(name in data for name in group):
            options = " or ".join(f'"{name}"' for name in group)
            raise InvalidRequest(f"The {request} is missing {options}.")
    for field in spec.fields:
        if field.name not in data:
            if field.required:
                raise InvalidRequest(f'The {request} is missing "{field.name}".')
            continue
        value = data[field.name]
        if value is None and field.nullable:
            continue
        if not _acceptable(field, value):
            if field.kind == "str" and field.pattern is not None and isinstance(value, str):
                raise InvalidRequest(f'The {request} has an invalid "{field.name}".')
            raise InvalidRequest(
                f'The {request} has an invalid "{field.name}": it must be '
                f"{_describe(field)}, not {_shown(value)}."
            )
        if field.kind == "array" and field.item_spec is not None:
            for item in value:
                try:
                    _validate_object(item, field.item_spec, f'{request} "{field.name}" entry')
                except InvalidRequest as error:
                    raise InvalidRequest(
                        f'The {request} has an invalid "{field.name}" entry: {error}'
                    ) from None


def validate_fields(data: Any, fields: tuple[Field, ...], request: str) -> None:
    """Raise InvalidRequest for the first field that is missing, mistyped or out of range."""
    _validate_object(data, ObjectSpec(fields=fields), request)


def _check_paths(source: str, output: str) -> None:
    try:
        require_ordinary_source(source)
        require_output_name(output)
    except ValueError as error:
        raise InvalidRequest(str(error)) from None


def _check_stage_masks(data: dict[str, Any]) -> None:
    stages = data.get("stages")
    if not isinstance(stages, list):
        return
    for item in stages:
        if not isinstance(item, dict):
            continue
        mask_path = item.get("mask_path")
        if mask_path is None:
            continue
        if not isinstance(mask_path, str) or not mask_path.strip() or "\0" in mask_path:
            raise InvalidRequest('The request has an invalid stage "mask_path".')
        try:
            require_ordinary_source(mask_path)
        except ValueError as error:
            raise InvalidRequest(str(error)) from None


def _check_output_bit_depth(data: dict[str, Any]) -> None:
    if data.get("output_bit_depth", 8) == 16 and str(data.get("output_format", "")).lower() not in (
        "png",
        "tif",
        "tiff",
    ):
        raise InvalidRequest("16-bit output is only supported for PNG and TIFF images.")


def validate_image_job(data: Any) -> None:
    _validate_object(data, _IMAGE_JOB_SPEC, "image job request")
    _check_paths(data["image_path"], data["output_path"])
    _check_stage_masks(data)
    _check_output_bit_depth(data)


def validate_video_job(data: Any) -> None:
    _validate_object(data, _VIDEO_JOB_SPEC, "video job request")
    _check_paths(data["video_path"], data["output_video_path"])
    _check_stage_masks(data)


def validate_edit_job(data: Any) -> None:
    _validate_object(data, _EDIT_JOB_SPEC, "edit job request")
    _check_paths(data["image_path"], data["output_path"])
    vram_limit = data.get("edit_vram_limit_mb", 0)
    if vram_limit != 0 and vram_limit < 1024:
        raise InvalidRequest(
            'The edit job request has an invalid "edit_vram_limit_mb": '
            f"it must be 0 or at least 1024, not {vram_limit}."
        )


def validate_patch_request(data: Any) -> None:
    _validate_object(data, _PATCH_SPEC, "preview patch request")
    _validate_object(data["region"], _PATCH_REGION_SPEC, "preview patch region")
    try:
        require_ordinary_source(data["image_path"])
    except ValueError as error:
        raise InvalidRequest(str(error)) from None
    _check_stage_masks(data)


def validate_probe(data: Any) -> None:
    validate_fields(data, PROBE_FIELDS, "media probe request")
    try:
        require_ordinary_source(data["media_path"])
    except ValueError as error:
        raise InvalidRequest(str(error)) from None


def usable_job_id(data: Any) -> str | None:
    """The job id a terminal message can name, or None when the request has none."""
    job_id = data.get("job_id") if isinstance(data, dict) else None
    return job_id if isinstance(job_id, str) and job_id else None
