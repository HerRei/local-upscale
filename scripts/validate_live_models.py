#!/usr/bin/env python3
"""Download Quick and Best into an empty cache and run real CPU inference."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def validate(model_root: Path | None = None) -> list[dict[str, object]]:
    import torch

    from localsr.core.model_adapter import ModelAdapter
    from localsr.core.model_catalog import MODEL_CATALOG, ModelPurpose, download_model
    from localsr.core.presets import PresetMode, select_model_for_preset

    temporary = None
    if model_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="localsr-empty-model-cache-")
        model_root = Path(temporary.name)
    model_root.mkdir(parents=True, exist_ok=True)
    if any(model_root.iterdir()):
        raise ValueError(f"Live-model cache must be empty: {model_root}")

    torch.set_num_threads(min(2, torch.get_num_threads()))
    results: list[dict[str, object]] = []
    try:
        for label, mode in (
            ("Quick", PresetMode.QUICK_UPSCALE),
            ("Best", PresetMode.BEST_UPSCALE),
        ):
            model = select_model_for_preset(
                MODEL_CATALOG,
                mode,
                purpose=ModelPurpose.PHOTO,
                output_scale=4,
            )
            path = download_model(model, model_root / model.filename)
            adapter = ModelAdapter()
            info = adapter.inspect(str(path))
            network, _descriptor = adapter.load(str(path), torch.device("cpu"), torch.float32)
            side = max(16, info.size_requirements_min, info.size_requirements_mult)
            if info.size_requirements_mult > 1:
                side = (
                    (side + info.size_requirements_mult - 1) // info.size_requirements_mult
                ) * info.size_requirements_mult
            with torch.inference_mode():
                output = network(torch.rand(1, info.in_channels, side, side))
            expected = side * info.scale
            if tuple(output.shape) != (1, info.out_channels, expected, expected):
                raise RuntimeError(
                    f"{label} output shape {tuple(output.shape)}; expected "
                    f"(1, {info.out_channels}, {expected}, {expected})"
                )
            if not bool(torch.isfinite(output).all()):
                raise RuntimeError(f"{label} inference produced non-finite output")
            results.append(
                {
                    "preset": label,
                    "model_id": model.model_id,
                    "architecture": info.architecture,
                    "scale": info.scale,
                    "download_bytes": path.stat().st_size,
                    "sha256": model.sha256,
                    "output_shape": list(output.shape),
                }
            )
            adapter.release()
    finally:
        if temporary is not None:
            temporary.cleanup()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        help="use this already-empty directory instead of a temporary cache",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    results = validate(args.cache)
    rendered = json.dumps({"result": "PASS", "models": results}, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
