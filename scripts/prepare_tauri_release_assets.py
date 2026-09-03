#!/usr/bin/env python3
"""Validate and compact the three user-facing Tauri installers for publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePath

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_LIVE_MODELS = {
    "Quick": "span_photo_x4",
    "Best": "realplksr_nomoswebphoto_x4",
}
SIGNED_POLICY = "production-signed"
CROSS_ALPHA_POLICY = "v0.0.11-cross-alpha-exception"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_exact(root: Path, filename: str) -> Path:
    if PurePath(filename).name != filename:
        raise ValueError(f"unsafe release filename: {filename!r}")
    matches = [path for path in root.rglob(filename) if path.is_file()]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {filename}, found {len(matches)}")
    return matches[0]


def has_release_sidecars(path: Path) -> bool:
    """Return whether an installer has every file required for verification."""
    return all(
        path.with_name(path.name + suffix).is_file()
        for suffix in (".sha256", ".metadata.json", ".architecture.json")
    )


def locate_release_input(root: Path, filename: str) -> Path:
    """Locate an installer, combining successful jobs from workflow reruns.

    The artifact server stores uploads as ``RUN_ID/ATTEMPT/PLATFORM/FILE``.
    GitHub's "re-run failed jobs" creates a new attempt but does not rerun or
    copy successful platform jobs. Select the newest complete upload for each
    filename across the run while ignoring compact publish output, which has no
    per-installer sidecars. Plain staging trees retain the strict old behavior.
    """
    if PurePath(filename).name != filename:
        raise ValueError(f"unsafe release filename: {filename!r}")

    attempts = [path for path in root.iterdir() if path.is_dir() and path.name.isdigit()]
    if not attempts:
        return locate_exact(root, filename)

    by_attempt: dict[int, list[Path]] = {}
    for attempt in attempts:
        matches = [
            path
            for path in attempt.glob(f"*/{filename}")
            if path.is_file() and has_release_sidecars(path)
        ]
        if matches:
            by_attempt[int(attempt.name)] = matches

    if not by_attempt:
        raise ValueError(f"found no complete release input for {filename}")
    newest = max(by_attempt)
    matches = by_attempt[newest]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one complete {filename} in attempt {newest}, found {len(matches)}"
        )
    return matches[0]


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def release_policy(
    manifest: dict[str, object],
    readiness: dict[str, object] | None,
    version: str,
) -> str:
    policy = str(manifest.get("release_policy", SIGNED_POLICY))
    if policy == SIGNED_POLICY:
        return policy
    if policy != CROSS_ALPHA_POLICY:
        raise ValueError(f"unsupported release policy {policy!r}")
    if version != "0.0.11-alpha":
        raise ValueError("the unsigned cross-build exception is restricted to v0.0.11-alpha")
    if readiness is None or readiness.get("beta_ready") is not False:
        raise ValueError("the unsigned cross-build exception must be explicitly non-beta")
    return policy


def validate_signing_evidence(
    platform: str,
    signing: object,
    expected: object,
    policy: str,
) -> dict[str, object]:
    if not isinstance(signing, dict) or signing.get("status") != expected:
        raise ValueError(f"signing evidence does not match {expected!r}")
    if expected == "developer-id-notarized" and not all(
        signing.get(field)
        for field in ("developer_id", "team_id", "notarized", "stapled", "gatekeeper_accepted")
    ):
        raise ValueError("incomplete Developer ID/notarization evidence")
    if expected == "authenticode-valid" and not all(
        signing.get(field) for field in ("signer_subject", "signer_thumbprint", "timestamped")
    ):
        raise ValueError("incomplete Authenticode evidence")
    if expected in {"ad-hoc-alpha", "unsigned-alpha"}:
        if policy != CROSS_ALPHA_POLICY or platform not in {"macos", "windows"}:
            raise ValueError("unsigned evidence is allowed only by the v0.0.11 cross-alpha policy")
        if signing.get("production_signed") is not False or not signing.get("warning"):
            raise ValueError("unsigned alpha evidence must record its warning and unsigned state")
    return signing


def validate_smoke_evidence(
    platform: str,
    smoke: object,
    policy: str,
) -> dict[str, object]:
    if isinstance(smoke, dict) and smoke.get("passed") is True:
        return smoke
    if (
        policy == CROSS_ALPHA_POLICY
        and platform == "macos"
        and isinstance(smoke, dict)
        and smoke.get("mode") == "cross-build-static"
        and smoke.get("static_verified") is True
        and smoke.get("runtime_tested") is False
    ):
        return smoke
    raise ValueError("no acceptable installed-package smoke evidence")


def prepare(
    root: Path,
    manifest_path: Path,
    tag: str,
    output: Path,
    readiness_path: Path | None = None,
    *,
    expected_commit: str | None = None,
    verification_only: bool = False,
) -> Path:
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported Tauri release manifest schema")
    version = str(manifest.get("version", ""))
    if tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not match manifest version {version!r}")
    readiness = load_json(readiness_path) if readiness_path else None
    if readiness and readiness.get("release") != version:
        raise ValueError("beta-readiness version does not match the release manifest")
    policy = release_policy(manifest, readiness, version)
    entries = manifest.get("artifacts")
    if not isinstance(entries, list) or len(entries) != 3:
        raise ValueError("the compact release must contain exactly three installers")

    output.mkdir(parents=True, exist_ok=True)
    bundles: list[dict[str, object]] = []
    digests: set[str] = set()
    live_model_evidence: dict[str, object] | None = None
    release_commit = ""
    source_matrix: dict[str, dict[str, object]] = {}
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("release manifest artifact entries must be objects")
        entry = dict(raw_entry)
        filename = str(entry["filename"])
        source = locate_release_input(root, filename)
        digest = sha256(source)
        if not SHA256_RE.fullmatch(digest):
            raise ValueError(f"invalid digest for {filename}")
        if digest in digests:
            raise ValueError("release installers must have distinct SHA-256 digests")
        digests.add(digest)

        checksum_path = source.with_name(source.name + ".sha256")
        metadata_path = source.with_name(source.name + ".metadata.json")
        architecture_path = source.with_name(source.name + ".architecture.json")
        expected_line = f"{digest}  {filename}"
        if checksum_path.read_text(encoding="utf-8-sig").strip() != expected_line:
            raise ValueError(f"checksum sidecar mismatch for {filename}")
        metadata = load_json(metadata_path)
        architecture = load_json(architecture_path)
        expected = {
            "artifact_filename": filename,
            "sha256": digest,
            "platform": entry["platform"],
            "architecture": entry["architecture"],
            "backend": entry["backend"],
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise ValueError(f"{filename} metadata {key!r} does not match manifest")
        commit = str(metadata.get("repository_commit", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"{filename} has no valid repository commit evidence")
        if release_commit and commit != release_commit:
            raise ValueError(f"release matrix mixes commits: {release_commit} and {commit}")
        release_commit = commit
        attempt = str(metadata.get("run_attempt", ""))
        if not attempt.isdigit():
            raise ValueError(f"{filename} has no valid workflow-attempt evidence")
        run_id = str(metadata.get("github_run_id", ""))
        if not run_id.isdigit():
            raise ValueError(f"{filename} has no valid workflow-run evidence")
        if root.name.isdigit() and root.name != run_id:
            raise ValueError(f"{filename} run metadata {run_id} does not match storage root")
        if source.parent.parent.name.isdigit() and source.parent.parent.name != attempt:
            raise ValueError(f"{filename} attempt metadata {attempt} does not match storage path")
        if source.parent.parent.name.isdigit() and source.parent.name != entry["platform"]:
            raise ValueError(f"{filename} platform storage path does not match manifest")
        source_matrix[str(entry["platform"])] = {
            "run_id": int(run_id),
            "attempt": int(attempt),
            "commit": commit,
            "filename": filename,
            "sha256": digest,
        }
        try:
            smoke = validate_smoke_evidence(
                str(entry["platform"]), metadata.get("package_smoke"), policy
            )
        except ValueError as error:
            raise ValueError(f"{filename} has {error}") from error
        if architecture.get("result") != "PASS" or not architecture.get("native_binary_count"):
            raise ValueError(f"{filename} has no passing native architecture evidence")
        expected_signing = entry.get("signing")
        try:
            signing = validate_signing_evidence(
                str(entry["platform"]), metadata.get("signing"), expected_signing, policy
            )
        except ValueError as error:
            raise ValueError(f"{filename} has {error}") from error
        if entry["platform"] == "linux":
            live_models = metadata.get("live_models")
            if not isinstance(live_models, dict) or live_models.get("result") != "PASS":
                raise ValueError(f"{filename} has no passing real Quick/Best model evidence")
            models = live_models.get("models")
            if (
                not isinstance(models, list)
                or len(models) != len(EXPECTED_LIVE_MODELS)
                or {
                    model.get("preset"): model.get("model_id")
                    for model in models
                    if isinstance(model, dict)
                }
                != EXPECTED_LIVE_MODELS
            ):
                raise ValueError(f"{filename} has incomplete real Quick/Best model evidence")
            for model in models:
                output_shape = model.get("output_shape") if isinstance(model, dict) else None
                if not isinstance(model, dict) or not all(
                    (
                        model.get("model_id"),
                        SHA256_RE.fullmatch(str(model.get("sha256", ""))),
                        isinstance(model.get("download_bytes"), int)
                        and int(model["download_bytes"]) > 0,
                        isinstance(output_shape, list)
                        and len(output_shape) == 4
                        and all(isinstance(side, int) and side > 0 for side in output_shape),
                    )
                ):
                    raise ValueError(f"{filename} has malformed real model evidence")
            live_model_evidence = live_models

        destination = output / filename
        if destination.exists() and sha256(destination) != digest:
            raise ValueError(f"refusing to overwrite conflicting prepared asset {filename}")
        if not destination.exists():
            shutil.copy2(source, destination)
        bundles.append(
            entry
            | {
                "sha256": digest,
                "size": source.stat().st_size,
                "signing_evidence": signing,
                "architecture_evidence": architecture,
                "smoke_evidence": smoke,
            }
        )

    checksums = "".join(f"{item['sha256']}  {item['filename']}\n" for item in bundles)
    (output / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    index = {
        "schema_version": 1,
        "release_tag": tag,
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "channel": "alpha",
        "release_policy": policy,
        "repository_commit": release_commit,
        "source_matrix": source_matrix,
        "beta_ready": False,
        "live_model_evidence": live_model_evidence,
        "installers": bundles,
    }
    if live_model_evidence is None:
        raise ValueError("the release has no real Quick/Best empty-cache inference evidence")
    if expected_commit is not None:
        if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
            raise ValueError("expected release commit must be a lowercase 40-character SHA")
        if release_commit != expected_commit:
            raise ValueError(
                f"release matrix commit {release_commit} does not match {expected_commit}"
            )
    index["verification_only"] = verification_only
    if readiness:
        index["beta_readiness"] = readiness
    index_path = output / "release-index.json"
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "matrix-selection.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_tag": tag,
                "version": version,
                "repository_commit": release_commit,
                "platforms": source_matrix,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    files = [str(output / str(item["filename"])) for item in bundles]
    files.extend([str(output / "SHA256SUMS"), str(index_path)])
    (output / "release-files.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
    return index_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--commit")
    parser.add_argument("--verification-only", action="store_true")
    args = parser.parse_args()
    path = prepare(
        args.root.resolve(),
        args.manifest.resolve(),
        args.tag,
        args.output.resolve(),
        args.readiness.resolve(),
        expected_commit=args.commit,
        verification_only=args.verification_only,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
