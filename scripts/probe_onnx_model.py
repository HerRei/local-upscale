#!/usr/bin/env python3
"""Export a verified catalog model and compare ONNX inference with LocalSR.

This is an acceptance probe, not a selectable application backend. Export on a
development machine, then copy the resulting directory to the acceptance device
and run `evaluate`. Evaluation needs NumPy and ONNX Runtime, but no PyTorch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from collections import Counter
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export_probe(args: argparse.Namespace) -> None:
    import numpy as np
    import onnx
    import torch

    from localsr.core.model_adapter import ModelAdapter
    from localsr.core.model_catalog import CATALOG_BY_ID

    if not 16 <= args.height <= 256 or not 16 <= args.width <= 256:
        raise ValueError("Probe dimensions must be between 16 and 256 pixels")
    if args.output.exists():
        raise ValueError("Choose a new output directory; existing evidence is not overwritten")
    args.output.mkdir(parents=True)
    model = CATALOG_BY_ID[args.model_id]
    checkpoint = args.model_root / model.filename
    torch.set_num_threads(2)
    adapter = ModelAdapter(allow_unverified_checkpoints=False)
    info = adapter.inspect(str(checkpoint))
    descriptor, _ = adapter.load(str(checkpoint), torch.device("cpu"), torch.float32)

    class ExportImage(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.model = descriptor.model

        def forward(self, image):
            return descriptor(image)

    exported = ExportImage().eval()
    rng = np.random.default_rng(20260913)
    y, x = np.mgrid[: args.height, : args.width].astype(np.float32)
    ramp = np.stack([x / args.width, y / args.height, (x + y) / (args.width + args.height)])
    checks = ((x.astype(int) // 3 + y.astype(int) // 3) % 2).astype(np.float32)
    patterns = {
        "ramp": ramp[None],
        "edges": np.stack([checks, 1 - checks, checks * 0.5])[None],
        "noise": np.clip(
            ramp[None] + rng.normal(0, 0.04, (1, 3, args.height, args.width)), 0, 1
        ).astype(np.float32),
    }
    samples: dict[str, object] = {}
    for name, array in patterns.items():
        tensor = torch.from_numpy(array.copy())
        with torch.inference_mode():
            expected = descriptor(tensor).cpu().numpy()
        if not np.isfinite(expected).all():
            raise ValueError(f"Reference output is non-finite for {name}")
        samples[f"{name}_input"] = array
        samples[f"{name}_expected"] = expected
    np.savez_compressed(args.output / "samples.npz", **samples)

    started = time.monotonic()
    torch.onnx.export(
        exported,
        (torch.from_numpy(patterns["ramp"].copy()),),
        str(args.output / "model.onnx"),
        input_names=["image"],
        output_names=["restored"],
        opset_version=20,
        dynamo=False,
        external_data=False,
    )
    onnx.checker.check_model(str(args.output / "model.onnx"))
    graph = onnx.load(str(args.output / "model.onnx"), load_external_data=False)
    manifest = {
        "schema_version": 1,
        "model_id": args.model_id,
        "checkpoint_sha256": model.sha256,
        "architecture": info.architecture,
        "scale": info.scale,
        "input_shape": list(patterns["ramp"].shape),
        "opset": 20,
        "torch": torch.__version__,
        "onnx": onnx.__version__,
        "export_seconds": time.monotonic() - started,
        "operators": dict(Counter(node.op_type for node in graph.graph.node)),
        "files": {name: digest(args.output / name) for name in ("model.onnx", "samples.npz")},
        "cases": list(patterns),
        "limits": {"absolute": 0.0002, "relative": 0.001},
        "scope": "Fixed input shape and synthetic probes only; not full worker or installed acceptance.",
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    adapter.release()
    print(
        json.dumps(
            {"model": args.model_id, "export": "PASS", "seconds": manifest["export_seconds"]}
        ),
        flush=True,
    )


def evaluate_probe(args: argparse.Namespace) -> bool:
    import numpy as np
    import onnxruntime as ort

    manifest = json.loads((args.probe / "manifest.json").read_text())
    for name in ("model.onnx", "samples.npz"):
        if digest(args.probe / name) != manifest["files"][name]:
            raise ValueError(f"Probe integrity check failed: {name}")
    ort.disable_telemetry_events()
    if args.provider not in ort.get_available_providers():
        raise ValueError(f"Requested provider is unavailable: {args.provider}")
    options = ort.SessionOptions()
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.enable_mem_pattern = False
    options.intra_op_num_threads = 2
    options.enable_profiling = True
    options.profile_file_prefix = str(args.probe / f"profile-{args.provider}")
    providers = [args.provider]
    if args.provider != "CPUExecutionProvider":
        providers.append("CPUExecutionProvider")
    started = time.monotonic()
    session = ort.InferenceSession(
        str(args.probe / "model.onnx"), sess_options=options, providers=providers
    )
    session.disable_fallback()
    if args.provider not in session.get_providers():
        raise ValueError("Requested provider failed to initialize; refusing an all-CPU result")
    report = {
        "model_id": manifest["model_id"],
        "manifest_sha256": digest(args.probe / "manifest.json"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "onnxruntime": ort.__version__,
        "requested_provider": args.provider,
        "providers": session.get_providers(),
        "telemetry_disabled": True,
        "session_seconds": time.monotonic() - started,
        "cases": [],
    }
    passed = True
    with np.load(args.probe / "samples.npz", allow_pickle=False) as samples:
        for name in manifest["cases"]:
            source, expected = samples[f"{name}_input"], samples[f"{name}_expected"]
            started = time.monotonic()
            actual = session.run(["restored"], {"image": source})[0]
            elapsed = time.monotonic() - started
            same_shape = actual.shape == expected.shape
            finite = bool(np.isfinite(actual).all())
            correct = (
                same_shape
                and finite
                and bool(
                    np.allclose(
                        actual,
                        expected,
                        atol=manifest["limits"]["absolute"],
                        rtol=manifest["limits"]["relative"],
                    )
                )
            )
            error = np.abs(actual - expected) if same_shape and finite else None
            report["cases"].append(
                {
                    "case": name,
                    "shape": list(actual.shape),
                    "finite": finite,
                    "passed": correct,
                    "max_error": float(error.max()) if error is not None else None,
                    "mean_error": float(error.mean()) if error is not None else None,
                    "seconds": elapsed,
                }
            )
            passed = passed and correct
    profile = Path(session.end_profiling())
    events = json.loads(profile.read_text())
    executions = Counter(
        event.get("args", {}).get("provider")
        for event in events
        if event.get("args", {}).get("provider")
    )
    report["profile_node_executions"] = dict(executions)
    report["profile_sha256"] = digest(profile)
    report["profile_file"] = profile.name
    if args.provider != "CPUExecutionProvider" and not executions[args.provider]:
        passed = False
    report["passed"] = passed
    output = args.probe / f"result-{args.provider}.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    exporter = commands.add_parser("export")
    exporter.add_argument("--model-id", required=True)
    exporter.add_argument("--model-root", type=Path, required=True)
    exporter.add_argument("--output", type=Path, required=True)
    exporter.add_argument("--height", type=int, default=32)
    exporter.add_argument("--width", type=int, default=32)
    evaluator = commands.add_parser("evaluate")
    evaluator.add_argument("--probe", type=Path, required=True)
    evaluator.add_argument(
        "--provider", choices=["CPUExecutionProvider", "DmlExecutionProvider"], required=True
    )
    args = parser.parse_args()
    if args.command == "export":
        export_probe(args)
        return 0
    return 0 if evaluate_probe(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
