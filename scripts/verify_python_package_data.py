#!/usr/bin/env python3
"""Verify that wheels and sdists retain SeedVR2 runtime/license data."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

REQUIRED_SUFFIXES = (
    "THIRD_PARTY_NOTICES.md",
    "localsr/video_models/seedvr2/configs_3b/main.yaml",
    "localsr/video_models/seedvr2/configs_7b/main.yaml",
    "localsr/video_models/seedvr2/neg_emb.safetensors",
    "localsr/video_models/seedvr2/pos_emb.safetensors",
    "localsr/video_models/seedvr2/NOTICE.md",
    "localsr/video_models/seedvr2/src/models/video_vae_v3/s8_c16_t4_inflation_sd3.yaml",
    "localsr/video_models/seedvr2/vendor/LICENSE",
    "localsr/video_models/seedvr2/vendor/models/video_vae_v3/s8_c16_t4_inflation_sd3.yaml",
)
FORBIDDEN_SUFFIXES = (
    "localsr/video_models/seedvr2/neg_emb.pt",
    "localsr/video_models/seedvr2/pos_emb.pt",
    "localsr/ui/slint_app.py",
    "localsr/ui/slint_worker.py",
    "localsr/ui/slint_preview.py",
    "localsr/ui/slint_check.py",
    "localsr/ui/slint/main.slint",
    "localsr/ui/slint/components.slint",
    "localsr/ui/native_dialog.py",
    "localsr/platform/macos_menu.py",
    "localsr/ui/main_window.py",
    "localsr/protocol/client.py",
)


def member_names(path: Path) -> set[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return {member.name for member in archive.getmembers() if member.isfile()}
    raise ValueError(f"Unsupported Python distribution: {path}")


def verify(path: Path) -> None:
    names = member_names(path)
    missing = [
        suffix for suffix in REQUIRED_SUFFIXES if not any(name.endswith(suffix) for name in names)
    ]
    if missing:
        raise ValueError(f"{path.name} omits required package data: {', '.join(missing)}")
    forbidden = [
        suffix for suffix in FORBIDDEN_SUFFIXES if any(name.endswith(suffix) for name in names)
    ]
    if forbidden:
        raise ValueError(f"{path.name} contains retired package data: {', '.join(forbidden)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distributions", nargs="+", type=Path)
    args = parser.parse_args()
    for distribution in args.distributions:
        verify(distribution)
        print(f"Python package data verified: {distribution.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
