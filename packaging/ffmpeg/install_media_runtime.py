#!/usr/bin/env python3
"""Build and install LocalSR's licensing-clean media runtime into one Python.

This replaces PyPI's PyAV (which bundles GPL x264/x265 and patent-licensed
codecs) and OpenCV (which bundles FFmpeg) with the allowlisted builds:

1. ``build_lgpl_media.py`` builds FFmpeg and a PyAV wheel from pinned sources;
2. ``scripts/build_macos_face_runtime.py`` builds OpenCV without FFmpeg;
3. both wheels are installed with ``--no-deps --force-reinstall``;
4. ``scripts/verify_codec_allowlist.py`` must pass for that interpreter.

Build directories are reused, so repeated runs are quick. Windows is not yet
supported by the media build and fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_macos_face_runtime as opencv  # noqa: E402

OPENCV_SDIST = "opencv-python-headless-4.10.0.84.tar.gz"
OPENCV_BUILD_REQUIREMENTS = [
    "numpy<2.3",
    "scikit-build==0.18.1",
    "cmake==3.31.6",
    "ninja==1.11.1.3",
    "setuptools",
    "wheel",
    "pip",
]


def run(command: list[str], **kwargs) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch_opencv_source(cache: Path) -> Path:
    archive = cache / OPENCV_SDIST
    if archive.is_file() and sha256(archive) == opencv.SOURCE_SHA256:
        return archive
    cache.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(
        "https://pypi.org/pypi/opencv-python-headless/4.10.0.84/json", timeout=60
    ) as response:
        files = json.load(response)["urls"]
    url = next(item["url"] for item in files if item["filename"] == OPENCV_SDIST)
    partial = archive.with_suffix(".partial")
    urllib.request.urlretrieve(url, partial)
    if sha256(partial) != opencv.SOURCE_SHA256:
        partial.unlink()
        raise SystemExit("downloaded OpenCV source does not match the reviewed digest")
    partial.replace(archive)
    return archive


def newest(pattern: str, directory: Path) -> Path:
    matches = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not matches:
        raise SystemExit(f"no {pattern} produced in {directory}")
    return matches[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build" / "lgpl-media")
    parser.add_argument("--source-cache", type=Path, help="defaults to <work-dir>/sources")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--skip-opencv", action="store_true")
    arguments = parser.parse_args(argv)
    work = arguments.work_dir.resolve()
    sources = (arguments.source_cache or work / "sources").resolve()
    python = str(arguments.python.absolute())

    run(
        [
            sys.executable,
            str(ROOT / "packaging" / "ffmpeg" / "build_lgpl_media.py"),
            "--work-dir",
            str(work / "work"),
            "--output-dir",
            str(work / "dist"),
            "--source-cache",
            str(sources),
            "--jobs",
            str(arguments.jobs),
        ]
    )
    wheels = [newest("av-*.whl", work / "dist")]

    if not arguments.skip_opencv:
        build_env = work / "opencv" / "buildenv"
        build_python = build_env / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        if not build_python.exists():
            run([python, "-m", "venv", str(build_env)])
        run([str(build_python), "-m", "pip", "install", "--quiet", *OPENCV_BUILD_REQUIREMENTS])
        run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_macos_face_runtime.py"),
                "--source",
                str(fetch_opencv_source(sources)),
                "--work-dir",
                str(work / "opencv"),
                "--python",
                str(build_python),
                "--jobs",
                str(arguments.jobs),
            ]
        )
        wheels.append(newest("opencv_python_headless-*.whl", work / "opencv" / "wheels"))
        corresponding = work / "opencv" / "corresponding-source"
        corresponding.mkdir(parents=True, exist_ok=True)
        for item in (
            fetch_opencv_source(sources),
            ROOT / "packaging" / "patches" / "opencv-disabled-module-typing.patch",
            ROOT / "scripts" / "build_macos_face_runtime.py",
            work / "opencv" / "build-report.json",
        ):
            if item.is_file():
                shutil.copy2(item, corresponding / item.name)

    run([python, "-m", "pip", "install", "--no-deps", "--force-reinstall", *map(str, wheels)])
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verify_codec_allowlist.py"),
            "--python-env",
            python,
        ]
    )
    print("Installed the licensing-clean media runtime:", ", ".join(w.name for w in wheels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
