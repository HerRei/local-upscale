#!/usr/bin/env python3
"""Write the traceable metadata sidecar required for every release artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def mps_provenance(
    wheel_manifest: Path,
    normalization_report: Path | None = None,
) -> dict[str, object]:
    data = json.loads(wheel_manifest.read_text(encoding="utf-8"))
    for wheel in data.get("wheels", []):
        if wheel.get("distribution") != "torch":
            continue
        for filename, digest in zip(
            wheel.get("sources", []), wheel.get("source_sha256", []), strict=True
        ):
            if "arm64" in filename:
                result: dict[str, object] = {
                    "torch_version": wheel.get("version"),
                    "torch_arm64_wheel": filename,
                    "torch_arm64_sha256": digest,
                    "universal2_wheel": wheel.get("output"),
                    "static_evidence": [
                        "official arm64 macOS torch wheel",
                        "paired x86_64/arm64 wheel merged with delocate-merge",
                        "recursive bundled Mach-O ARM64-slice verification",
                    ],
                }
                if normalization_report:
                    result["openmp_normalization"] = json.loads(
                        normalization_report.read_text(encoding="utf-8")
                    )
                return result
    raise ValueError(f"No arm64 torch wheel provenance in {wheel_manifest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--backend-probe", type=Path)
    parser.add_argument("--smoke-report", type=Path)
    parser.add_argument("--wheel-manifest", type=Path)
    parser.add_argument("--normalization-report", type=Path)
    args = parser.parse_args()
    digest = sha256(args.artifact)
    metadata: dict[str, object] = {
        "schema_version": 1,
        "repository_commit": args.commit,
        "github_run_id": str(args.run_id),
        "run_attempt": str(args.attempt),
        "platform": args.platform,
        "architecture": args.architecture,
        "backend": args.backend,
        "timestamp": datetime.now(UTC).isoformat(),
        "artifact_filename": args.artifact.name,
        "artifact_size": args.artifact.stat().st_size,
        "sha256": digest,
    }
    if args.backend_probe:
        metadata["backend_probe"] = json.loads(args.backend_probe.read_text(encoding="utf-8"))
    if args.smoke_report:
        metadata["package_smoke"] = json.loads(args.smoke_report.read_text(encoding="utf-8"))
    if args.wheel_manifest:
        metadata["mps"] = mps_provenance(args.wheel_manifest, args.normalization_report)
    output = args.artifact.with_name(args.artifact.name + ".metadata.json")
    atomic_json(output, metadata)
    args.artifact.with_name(args.artifact.name + ".sha256").write_text(
        f"{digest}  {args.artifact.name}\n", encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()
