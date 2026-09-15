"""Build the OpenCV operations used by LocalSR without a second video stack.

Video decoding/encoding remains in PyAV. This private build supplies YuNet face
detection and SeedVR2's drawing operations from a pinned source archive. A small
packaging patch omits type aliases and SDK files for disabled modules. This recipe does not
install packages or modify a shared Python environment.

Despite the historical file name, the recipe runs on macOS (arm64/x86_64), Linux and
Windows: only the Apple-specific CMake options are conditional. Pass a Python that already
has the build requirements (numpy, scikit-build, cmake, ninja, setuptools, wheel, pip).
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
    "-DWITH_MSMF=OFF",
    "-DWITH_DSHOW=OFF",
    "-DWITH_1394=OFF",
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
]
MACOS_DEPLOYMENT_TARGET = "14.0"
BUILD_TOOLS = ("cmake", "ninja", "scikit-build", "numpy", "setuptools")


def platform_options() -> list[str]:
    """Return the CMake options that only apply to the host platform."""
    if platform.system() == "Darwin":
        return [
            f"-DCMAKE_OSX_ARCHITECTURES={platform.machine()}",
            f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MACOS_DEPLOYMENT_TARGET}",
        ]
    return []


def tool_versions(python: Path) -> dict[str, str | None]:
    """Record the build tool versions actually present in the build interpreter."""
    probe = (
        "import importlib.metadata as m, json\n"
        "out = {}\n"
        f"for name in {list(BUILD_TOOLS)!r}:\n"
        "    try:\n"
        "        out[name] = m.version(name)\n"
        "    except m.PackageNotFoundError:\n"
        "        out[name] = None\n"
        "print(json.dumps(out))\n"
    )
    result = subprocess.run([str(python), "-c", probe], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2, help="Parallel compile jobs (default 2)")
    parser.add_argument(
        "--timeout", type=int, default=1800, help="Wheel build timeout in seconds (default 1800)"
    )
    args = parser.parse_args()
    if platform.system() not in {"Darwin", "Linux", "Windows"}:
        parser.error(f"Unsupported build platform: {platform.system()}")
    if shutil.which("patch") is None:
        parser.error("The 'patch' tool is required (on Windows use the one shipped with Git)")
    options = OPTIONS + platform_options()
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
        CMAKE_ARGS=" ".join(options),
        CMAKE_BUILD_PARALLEL_LEVEL=str(args.jobs),
        ENABLE_HEADLESS="1",
        ENABLE_CONTRIB="0",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    if platform.system() == "Darwin":
        env["MACOSX_DEPLOYMENT_TARGET"] = MACOS_DEPLOYMENT_TARGET
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
        "platform": f"{platform.system()}-{platform.machine()}",
        "cmake_options": options,
        "parallel_jobs": args.jobs,
        "build_tools": tool_versions(python),
        "passed": False,
    }
    try:
        with (root / "build.log").open("w") as log:
            subprocess.run(
                command,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=args.timeout,
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
