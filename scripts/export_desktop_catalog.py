#!/usr/bin/env python3
"""Export the Python model catalog for the non-Python desktop control plane.

The Slint application remains supported and therefore keeps its established
Python catalog as the source of truth during the Tauri migration.  This tool
creates a deterministic, data-only manifest consumed by the Rust host and can
also verify that the checked-in manifest is current.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from localsr.core.model_catalog import (  # noqa: E402
    CATALOG_REVISION,
    MODEL_CATALOG,
    VIDEO_MODEL_CATALOG,
)

DEFAULT_OUTPUT = ROOT / "desktop" / "src-tauri" / "resources" / "model-catalog.json"

LICENSE_URLS = {
    "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "BSD-3-Clause": "https://opensource.org/license/bsd-3-clause",
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC BY-NC-SA 4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "MIT": "https://opensource.org/license/mit",
}


def _license_policy(name: str, commercial_status: str) -> dict:
    normalized = name.strip()
    unclear = "clarify" in normalized.lower() or commercial_status == "unclear"
    noncommercial = commercial_status == "not-allowed" or "NC" in normalized.upper()
    return {
        "license_url": LICENSE_URLS.get(normalized, ""),
        # The app downloads from the publisher's pinned HTTPS URL; it does not
        # mirror a checkpoint merely because an upstream URL exists.
        "automated_download_allowed": not unclear,
        "redistribution_allowed": False,
        "commercial_use_allowed": False if noncommercial else None if unclear else True,
        "attribution_required": normalized not in {"MIT"},
        "terms_acceptance_required": noncommercial or unclear,
    }


def build_manifest() -> dict:
    models = []
    for model in MODEL_CATALOG:
        entry = asdict(model)
        entry["purposes"] = [purpose.value for purpose in model.purposes]
        entry["quality_tier"] = int(model.quality_tier)
        entry["speed_tier"] = int(model.speed_tier)
        entry.update(_license_policy(model.license_name, model.commercial_use_status))
        # Explicit opt-in downloads from the publisher for these two public
        # checkpoints. Their ambiguous license and commercial status stay
        # unresolved; the UI requires two acknowledgements before downloading.
        if model.model_id in {"realplksr_hfa2k_anime_x4", "realplksr_nomoswebphoto_x4"}:
            entry["automated_download_allowed"] = True
            tag = model.download_url.split("/download/", 1)[1].split("/", 1)[0]
            entry["license_url"] = f"https://github.com/Phhofm/models/releases/tag/{tag}"
        entry["engine_id"] = "localsr.pytorch-spandrel"
        entry["support_tier"] = "labs" if model.commercial_use_status != "allowed" else "supported"
        models.append(entry)

    video_models = []
    for model in VIDEO_MODEL_CATALOG:
        entry = asdict(model)
        entry["files"] = [asdict(file) for file in model.files]
        entry.update(_license_policy(model.license_name, "allowed"))
        entry["engine_id"] = "localsr.video.seedvr2"
        entry["support_tier"] = "labs"
        video_models.append(entry)

    return {
        "schema_version": 1,
        "catalog_revision": CATALOG_REVISION,
        "models": models,
        "video_models": video_models,
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
            print(f"missing generated desktop catalog: {args.output}", file=sys.stderr)
            return 1
        if current != content:
            print(
                "desktop model catalog is stale; run python scripts/export_desktop_catalog.py",
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
