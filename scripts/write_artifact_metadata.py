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
    parser.add_argument("--signing-report", type=Path)
    parser.add_argument("--live-model-report", type=Path)
    parser.add_argument("--engine-payload-manifest", type=Path)
    parser.add_argument("--dependency-report", type=Path)
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
        "signing": {
            "status": "unsigned",
            "developer_id": False,
            "notarized": False,
            "gatekeeper_accepted": False,
        },
    }
    if args.backend_probe:
        metadata["backend_probe"] = json.loads(args.backend_probe.read_text(encoding="utf-8"))
    if args.smoke_report:
        metadata["package_smoke"] = json.loads(args.smoke_report.read_text(encoding="utf-8"))
        if "backend_probe" in metadata["package_smoke"]:
            metadata["backend_probe"] = metadata["package_smoke"]["backend_probe"]
    if args.engine_payload_manifest:
        metadata["engine_payload"] = json.loads(args.engine_payload_manifest.read_text())
        metadata["engine_payload_sha256"] = sha256(args.engine_payload_manifest)
    if args.dependency_report:
        metadata["dependency_wheelhouse"] = json.loads(args.dependency_report.read_text())
    if args.signing_report:
        metadata["signing"] = json.loads(args.signing_report.read_text(encoding="utf-8"))
    if args.live_model_report:
        metadata["live_models"] = json.loads(args.live_model_report.read_text(encoding="utf-8"))
    output = args.artifact.with_name(args.artifact.name + ".metadata.json")
    atomic_json(output, metadata)
    args.artifact.with_name(args.artifact.name + ".sha256").write_text(
        f"{digest}  {args.artifact.name}\n", encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()
