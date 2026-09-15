#!/usr/bin/env python3
"""Validate and compact the complete backend matrix and payloads for publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path, PurePath

from release_targets import ROOT, validate_manifest
from tauri_public_assets import prepare_engine_parts, prepare_installer

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_LIVE_MODELS = {
    "Quick": "span_photo_x4",
    "Best": "realplksr_nomoswebphoto_x4",
}
SIGNED_POLICY = "production-signed"
SIGNING_REQUIREMENTS = {"developer-id-notarized", "authenticode-valid", "sha256"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lock_sha256_candidates(lock: Path) -> set[str]:
    content = lock.read_bytes()
    normalized = content.replace(b"\r\n", b"\n")
    crlf = normalized.replace(b"\n", b"\r\n")
    return {
        sha256(lock),
        hashlib.sha256(normalized).hexdigest(),
        hashlib.sha256(crlf).hexdigest(),
    }


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


def release_policy(manifest: dict[str, object]) -> str:
    policy = str(manifest.get("release_policy", SIGNED_POLICY))
    if policy != SIGNED_POLICY:
        raise ValueError(f"unsupported release policy {policy!r}")
    return policy


def validate_signing_evidence(signing: object, expected: object) -> dict[str, object]:
    if expected not in SIGNING_REQUIREMENTS:
        raise ValueError(f"unsupported signing requirement {expected!r}")
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
    return signing


def validate_smoke_evidence(smoke: object, version: str) -> dict[str, object]:
    if (
        isinstance(smoke, dict)
        and smoke.get("passed") is True
        and smoke.get("mode") == "headless-installed-host"
        and smoke.get("worker") == "ready"
        and isinstance(smoke.get("worker_path"), str)
        and bool(str(smoke["worker_path"]).strip())
        and smoke.get("version") == version
    ):
        return smoke
    raise ValueError("no acceptable installed-package smoke evidence")


def validate_architecture_evidence(
    filename: str,
    architecture: object,
    expected_architecture: str,
) -> dict[str, object]:
    if not isinstance(architecture, dict):
        raise ValueError("architecture evidence must be a JSON object")
    if architecture.get("schema_version") != 1:
        raise ValueError("architecture evidence has an unsupported schema")
    if architecture.get("artifact") != filename:
        raise ValueError("architecture evidence names a different artifact")
    if architecture.get("required_architectures") != [expected_architecture]:
        raise ValueError("architecture evidence does not require the manifest architecture")
    if architecture.get("mismatches") != []:
        raise ValueError("architecture evidence contains native binary mismatches")
    if architecture.get("result") != "PASS":
        raise ValueError("architecture evidence did not pass")
    count = architecture.get("native_binary_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("architecture evidence has no native binaries")
    return architecture


def parse_evidence_timestamp(value: object, filename: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{filename} has no artifact evidence timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{filename} has an invalid artifact evidence timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{filename} artifact evidence timestamp has no timezone")
    return parsed.astimezone(UTC)


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
    policy = release_policy(manifest)
    entries = manifest.get("artifacts")
    validate_manifest(manifest)

    output.mkdir(parents=True, exist_ok=True)
    bundles: list[dict[str, object]] = []
    public_assets: list[dict] = []
    digests: set[str] = set()
    live_model_evidence: dict[str, object] | None = None
    release_commit = ""
    source_matrix: dict[str, dict[str, object]] = {}
    evidence_timestamps: list[datetime] = []
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
        if metadata.get("schema_version") != 1:
            raise ValueError(f"{filename} metadata has an unsupported schema")
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
        if metadata.get("artifact_size") != source.stat().st_size:
            raise ValueError(f"{filename} metadata artifact size does not match the installer")
        evidence_timestamps.append(parse_evidence_timestamp(metadata.get("timestamp"), filename))
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
        source_matrix[str(entry["id"])] = {
            "run_id": int(run_id),
            "attempt": int(attempt),
            "commit": commit,
            "filename": filename,
            "sha256": digest,
        }
        try:
            smoke = validate_smoke_evidence(metadata.get("package_smoke"), version)
        except ValueError as error:
            raise ValueError(f"{filename} has {error}") from error
        try:
            architecture = validate_architecture_evidence(
                filename, architecture, str(entry["architecture"])
            )
        except ValueError as error:
            raise ValueError(f"{filename} has {error}") from error
        expected_signing = entry.get("signing")
        try:
            signing = validate_signing_evidence(metadata.get("signing"), expected_signing)
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

        probe = metadata.get("backend_probe")
        if (
            not isinstance(probe, dict)
            or probe.get("backend") != entry["backend"]
            or probe.get("runtime_verified") is not True
            or probe.get("cpu_inference_verified") is not True
            or probe != smoke.get("backend_probe")
        ):
            raise ValueError(f"{filename} has no verified installed backend identity")
        if entry["backend"] == "Intel-XPU" and (
            probe.get("xpu_runtime_files_verified") is not True
            or not isinstance(probe.get("xpu_runtime_library_count"), int)
            or probe["xpu_runtime_library_count"] < 5
        ):
            raise ValueError(f"{filename} has no verified bundled Intel runtime files")
        if entry["platform"] != "macos":
            dependencies = metadata.get("dependency_wheelhouse")
            lock = ROOT / "requirements/locks" / f"{entry['id']}.txt"
            if (
                not isinstance(dependencies, dict)
                or dependencies.get("schema_version") != 1
                or dependencies.get("target") != entry["id"]
                or dependencies.get("source_lock_sha256") not in lock_sha256_candidates(lock)
                or not dependencies.get("wheels")
            ):
                raise ValueError(f"{filename} has no matching locked wheelhouse provenance")
        downloads = prepare_installer(source, output)
        if entry.get("external_engine"):
            downloads.extend(prepare_engine_parts(metadata, smoke, source, output))
        public_assets.extend(downloads)
        bundles.append(
            entry
            | {
                "sha256": digest,
                "size": source.stat().st_size,
                "download_files": downloads,
                "backend_evidence": metadata.get("backend_probe"),
                "dependency_evidence": metadata.get("dependency_wheelhouse"),
                "signing_evidence": signing,
                "architecture_evidence": architecture,
                "smoke_evidence": smoke,
            }
        )

    if len({item["filename"] for item in public_assets}) != len(public_assets):
        raise ValueError("release contains duplicate public asset filenames")
    checksums = "".join(f"{item['sha256']}  {item['filename']}\n" for item in public_assets)
    (output / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    index = {
        "schema_version": 1,
        "release_tag": tag,
        "version": version,
        # Derive this from immutable uploaded evidence so a failed publication
        # job can recreate byte-identical public metadata on a safe rerun.
        "generated_at": max(evidence_timestamps).isoformat(),
        "channel": "alpha",
        "release_policy": policy,
        "repository_commit": release_commit,
        "source_matrix": source_matrix,
        "beta_ready": False,
        "live_model_evidence": live_model_evidence,
        "installers": bundles,
        "public_assets": public_assets,
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
                "targets": source_matrix,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    files = [str(output / str(item["filename"])) for item in public_assets]
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
