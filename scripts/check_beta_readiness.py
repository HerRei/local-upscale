#!/usr/bin/env python3
"""Validate and summarize the explicit public-beta gate register."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_STATUSES = {
    "automated-pass",
    "manual-pass",
    "waiting-credentials",
    "decision-required",
    "manual-required",
    "external-clarification",
    "labs",
}
UNRESOLVED_STATUSES = ALLOWED_STATUSES - {"automated-pass", "manual-pass"}


def blocks_release(gate: dict) -> bool:
    return gate["blocking"] and gate["status"] in UNRESOLVED_STATUSES


def validate(path: Path, root: Path = ROOT) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    with (root / "pyproject.toml").open("rb") as stream:
        version = str(tomllib.load(stream)["project"]["version"])
    if data.get("schema_version") != 2:
        raise ValueError("beta-readiness schema_version must be 2")
    if data.get("release") != version:
        raise ValueError("beta-readiness release does not match pyproject.toml")
    gates = data.get("gates")
    if not isinstance(gates, list) or not gates:
        raise ValueError("beta-readiness gates must be a non-empty list")
    ids: set[str] = set()
    unresolved = 0
    for gate in gates:
        if not isinstance(gate, dict):
            raise ValueError("each beta-readiness gate must be an object")
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or not gate_id or gate_id in ids:
            raise ValueError(f"invalid or duplicate beta-readiness gate id: {gate_id!r}")
        ids.add(gate_id)
        status = gate.get("status")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"{gate_id}: unsupported status {status!r}")
        if not isinstance(gate.get("blocking"), bool):
            raise ValueError(f"{gate_id}: blocking must be a boolean")
        if not isinstance(gate.get("scope"), str) or not gate["scope"].strip():
            raise ValueError(f"{gate_id}: scope must be non-empty")
        if status == "manual-pass" and (
            not isinstance(gate.get("evidence"), str) or not gate["evidence"].strip()
        ):
            raise ValueError(f"{gate_id}: manual-pass requires an evidence reference")
        if blocks_release(gate):
            unresolved += 1
        for field in ("summary", "next_action"):
            if not isinstance(gate.get(field), str) or not gate[field].strip():
                raise ValueError(f"{gate_id}: {field} must be non-empty")
    declared_ready = data.get("beta_ready")
    if not isinstance(declared_ready, bool):
        raise ValueError("beta_ready must be a boolean")
    if declared_ready != (unresolved == 0):
        raise ValueError("beta_ready disagrees with unresolved gate statuses")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--require-beta-ready", action="store_true")
    args = parser.parse_args()
    path = args.path
    if path is None:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        register = "public-beta-readiness.json" if "-beta" in version else "beta-readiness.json"
        path = ROOT / "ci" / register
    data = validate(path)
    gates = data["gates"]
    unresolved = [gate for gate in gates if blocks_release(gate)]
    optional = [
        gate for gate in gates if not gate["blocking"] and gate["status"] in UNRESOLVED_STATUSES
    ]
    print(
        f"Beta readiness register valid for v{data['release']}: "
        f"{len(unresolved)} blocking, {len(optional)} optional experimental/pending"
    )
    for gate in unresolved:
        print(f"- {gate['id']}: {gate['status']} — {gate['next_action']}")
    for gate in optional:
        print(f"- Optional {gate['id']}: {gate['status']} — {gate['summary']}")
    return 1 if args.require_beta_ready and unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
