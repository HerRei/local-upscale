#!/usr/bin/env python3
"""Export the Python step registry for the non-Python desktop control plane."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from localsr.core.steps import LIMITS, PHASES, STEPS, StepParameter  # noqa: E402

DEFAULT_OUTPUT = ROOT / "desktop" / "src-tauri" / "resources" / "steps.json"


def _encode_parameter(param: StepParameter) -> dict[str, Any]:
    return {
        "name": param.name,
        "kind": param.kind,
        "default": param.default,
        "minimum": param.minimum,
        "maximum": param.maximum,
        "choices": list(param.choices),
        "int_choices": list(param.int_choices),
        "ui_minimum": param.ui_minimum,
        "ui_maximum": param.ui_maximum,
        "ui_default": param.ui_default,
        "nullable": param.nullable,
        "required": param.required,
    }


def build_manifest() -> dict[str, Any]:
    steps = []
    for step in STEPS:
        steps.append(
            {
                "id": step.id,
                "media": list(step.media),
                "phase": step.phase,
                "order": step.order,
                "needs_model": step.needs_model,
                "supports_mask": step.supports_mask,
                "requires_mask": step.requires_mask,
                "stage_kind": step.stage_kind,
                "role": step.role,
                "fix_kind": step.fix_kind,
                "is_fix_stage": step.is_fix_stage,
                "fix_chip_order": step.fix_chip_order,
                "in_image_pipeline": step.in_image_pipeline,
                "in_recipe_stages": step.in_recipe_stages,
                "label": step.label,
                "row_label": step.row_label,
                "short_name": step.short_name,
                "stage_label": step.stage_label,
                "required_feature": step.required_feature,
                "dev_only": step.dev_only,
                "model_task": step.model_task,
                "output_alpha": step.output_alpha,
                "parameters": [_encode_parameter(param) for param in step.parameters],
            }
        )
    return {
        "schema_version": 1,
        "phases": list(PHASES),
        "limits": {
            "max_fix_stages": LIMITS.max_fix_stages,
            "max_fix_chips": LIMITS.max_fix_chips,
            "max_face_stages": LIMITS.max_face_stages,
            "max_recipe_stages": LIMITS.max_recipe_stages,
            "max_image_pipeline_stages": LIMITS.max_image_pipeline_stages,
            "max_upscale_prefix_stages": LIMITS.max_upscale_prefix_stages,
            "max_denoise_prefix_stages": LIMITS.max_denoise_prefix_stages,
            "max_video_upscale_prefix_stages": LIMITS.max_video_upscale_prefix_stages,
            "max_step_stages": LIMITS.max_step_stages,
            "multi_fix_feature": LIMITS.multi_fix_feature,
            "stage_params_feature": LIMITS.stage_params_feature,
        },
        "steps": steps,
    }


def encoded_manifest() -> str:
    return json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = encoded_manifest()

    if args.check:
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError:
            print(f"missing generated desktop steps manifest: {args.output}", file=sys.stderr)
            return 1
        if current != content:
            print(
                "desktop steps manifest is stale; run python scripts/export_desktop_steps.py",
                file=sys.stderr,
            )
            return 1
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
