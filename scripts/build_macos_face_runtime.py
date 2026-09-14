"""Build the OpenCV operations used by LocalSR without a second video stack.

Video decoding/encoding remains in PyAV. This private build supplies YuNet face
detection and SeedVR2's drawing operations from a pinned source archive. A small
packaging patch omits type aliases and SDK files for disabled modules. This recipe does not
install packages or modify a shared Python environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path

SOURCE_SHA256 = "f2017c6101d7c2ef8d7bc3b414c37ff7f54d64413a1847d89970b6b7069b4e1a"
SOURCE_DIRECTORY = "opencv-python-headless-4.10.0.84"
OPTIONS = [
    "-DBUILD_LIST=core,imgproc,dnn,objdetect,python3",
    "-DBUILD_SHARED_LIBS=OFF",
    "-DBUILD_TESTS=OFF",
    "-DBUILD_PERF_TESTS=OFF",
    "-DBUILD_EXAMPLES=OFF",
    "-DBUILD_opencv_apps=OFF",
    "-DBUILD_opencv_java=OFF",
    "-DBUILD_JAVA=OFF",
    "-DWITH_FFMPEG=OFF",
    "-DWITH_GSTREAMER=OFF",
    "-DWITH_AVFOUNDATION=OFF",
    "-DWITH_OBSENSOR=OFF",
    "-DWITH_V4L=OFF",
    "-DWITH_QT=OFF",
    "-DWITH_GTK=OFF",
    "-DWITH_COCOA=OFF",
    "-DWITH_OPENCL=OFF",
    "-DWITH_LAPACK=OFF",
    "-DWITH_OPENEXR=OFF",
    "-DBUILD_OPENEXR=OFF",
    "-DWITH_JASPER=OFF",
    "-DWITH_OPENJPEG=OFF",
    "-DWITH_JPEG=OFF",
    "-DWITH_PNG=OFF",
    "-DWITH_TIFF=OFF",
    "-DWITH_WEBP=OFF",
    "-DBUILD_ZLIB=ON",
    "-DBUILD_PROTOBUF=ON",
    "-DCMAKE_OSX_ARCHITECTURES=arm64",
    "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("This recipe targets native Apple Silicon only")
    with args.source.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SOURCE_SHA256:
            parser.error("The source archive does not match the reviewed upstream digest")
    root = args.work_dir.resolve()
    source = root / "source" / SOURCE_DIRECTORY
    if not source.exists():
        with tarfile.open(args.source) as archive:
            archive.extractall(root / "source", filter="data")
    patch = (
        Path(__file__).resolve().parents[1]
        / "packaging/patches/opencv-disabled-module-typing.patch"
    )
    patch_command = ["patch", "-p1", "--input", str(patch)]
    pristine = (
        subprocess.run(
            patch_command + ["--dry-run", "--forward"], cwd=source, capture_output=True
        ).returncode
        == 0
    )
    if pristine:
        subprocess.run(patch_command + ["--forward"], cwd=source, check=True)
    else:
        subprocess.run(patch_command + ["--dry-run", "--reverse"], cwd=source, check=True)
    # CMake can leave files from previously enabled modules in its install
    # directory. Retain compiled objects, but always stage the wheel afresh.
    for installed in (source / "_skbuild").glob("*/cmake-install"):
        shutil.rmtree(installed)
    wheels = root / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    python = args.python.absolute()
    env = dict(os.environ)
    env.update(
        PATH=str(python.parent) + os.pathsep + env.get("PATH", ""),
        CMAKE_ARGS=" ".join(OPTIONS),
        CMAKE_BUILD_PARALLEL_LEVEL="2",
        MACOSX_DEPLOYMENT_TARGET="14.0",
        ENABLE_HEADLESS="1",
        ENABLE_CONTRIB="0",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    command = [
        str(python),
        "-m",
        "pip",
        "--isolated",
        "wheel",
        "--no-deps",
        "--no-build-isolation",
        "--no-cache-dir",
        "--wheel-dir",
        str(wheels),
        str(source),
        "--verbose",
    ]
    report = {
        "source_sha256": SOURCE_SHA256,
        "source_url": "https://pypi.org/project/opencv-python-headless/4.10.0.84/#files",
        "source_modified": True,
        "patch": patch.name,
        "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "cmake_options": OPTIONS,
        "parallel_jobs": 2,
        "build_tools": {"cmake": "3.31.6", "ninja": "1.11.1.3", "scikit-build": "0.18.1"},
        "passed": False,
    }
    try:
        with (root / "build.log").open("w") as log:
            subprocess.run(
                command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800
            )
        files = sorted(wheels.glob("opencv_python_headless-4.10.0.84-*.whl"))
        if len(files) != 1:
            raise RuntimeError("Expected exactly one locally built OpenCV wheel")
        wheel = files[0]
        with wheel.open("rb") as stream:
            report.update(
                passed=True,
                wheel=wheel.name,
                size_bytes=wheel.stat().st_size,
                wheel_sha256=hashlib.file_digest(stream, "sha256").hexdigest(),
            )
    finally:
        (root / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
