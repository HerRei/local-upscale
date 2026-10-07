#!/usr/bin/env python3
"""Build the pinned native Qwen runtime; never download weights or run inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REVISION = "3f8527a46c54ecf4cb4ed6003da8e8982283c73c"
ROOT = Path(__file__).resolve().parents[1]
TAG = "master-929-3f8527a"
RELEASE_BASE = f"https://github.com/leejet/stable-diffusion.cpp/releases/download/{TAG}/"
PREBUILT = {
    ("win32", "CPU"): (
        "sd-master-3f8527a-bin-win-cpu-x64.zip",
        "5e7caca2080321b25a12c1fa4175cb7d953f2b182309f8f73bfc9c725231d26c",
    ),
    ("win32", "CUDA"): (
        "sd-master-3f8527a-bin-win-cuda12-x64.zip",
        "217d6dead9abd3f827fc338268555cc179234e7e6330ef21ecb1c985e19d2dc7",
    ),
    ("win32", "ROCM"): (
        "sd-master-3f8527a-bin-win-rocm-7.14.0-x64.zip",
        "13fd3a7f159a73b6ff6b046665db4009fc4f253eef8c14d93f0e9612818606e3",
    ),
    ("linux", "CUDA"): (
        "sd-master-3f8527a-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip",
        "e35cc73cf5ba9637d1dc1d717760e7b8428376a4905d57e72ec8c871864f62c7",
    ),
    ("linux", "ROCM"): (
        "sd-master-3f8527a-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip",
        "e35cc73cf5ba9637d1dc1d717760e7b8428376a4905d57e72ec8c871864f62c7",
    ),
}
WINDOWS_CUDA_RUNTIME = (
    "cudart-sd-bin-win-cu12-x64.zip",
    "fe20366827d357c00797eebb58244dddab7fd9a348d70090c3871004c320f38d",
)


def unpack_verified_release(asset, destination: Path):
    name, expected = asset
    cache = ROOT / ".cache" / name
    cache.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    if cache.is_file():
        with cache.open("rb") as stream:
            while chunk := stream.read(1024**2):
                digest.update(chunk)
    if digest.hexdigest() != expected:
        digest = hashlib.sha256()
        with (
            urllib.request.urlopen(RELEASE_BASE + name, timeout=60) as source,
            cache.open("wb") as target,
        ):
            while chunk := source.read(1024**2):
                digest.update(chunk)
                target.write(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(f"Native runtime archive checksum mismatch: {name}")
    extracted = set()
    with zipfile.ZipFile(cache) as archive:
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
                raise ValueError("Unsafe native runtime archive")
            if entry.is_dir():
                continue
            filename = path.name
            wanted = (
                filename in {"sd-cli", "sd-cli.exe"}
                or filename.endswith((".dll", ".dylib", ".so"))
                or ".so." in filename
            )
            if wanted:
                if filename in extracted:
                    raise ValueError("Duplicate native runtime archive filename")
                extracted.add(filename)
                with archive.open(entry) as source, (destination / filename).open("wb") as target:
                    shutil.copyfileobj(source, target, 1024**2)
                if filename == "sd-cli":
                    (destination / filename).chmod(0o755)
    return extracted


def run(args):
    subprocess.run([str(arg) for arg in args], check=True)


def build(backend: str, destination: Path, source: Path | None = None, arch: str | None = None):
    if backend not in {"MPS", "CUDA", "ROCM", "CPU", "XPU"}:
        raise ValueError("Unsupported editing backend")
    source = source or ROOT / ".cache/sd-src"
    if not source.exists():
        source.mkdir(parents=True)
        run(["git", "-C", source, "init"])
        run(
            [
                "git",
                "-C",
                source,
                "remote",
                "add",
                "origin",
                "https://github.com/leejet/stable-diffusion.cpp.git",
            ]
        )
        run(["git", "-C", source, "fetch", "--depth", "1", "origin", REVISION])
        run(["git", "-C", source, "checkout", "--detach", REVISION])
        run(["git", "-C", source, "submodule", "update", "--init", "--recursive"])
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != REVISION:
        raise ValueError(f"Native runtime source must be pinned to {REVISION}")
    metadata = destination / "runtime.json"
    actual_backend = (
        "VULKAN" if sys.platform == "linux" and backend in {"CUDA", "ROCM"} else backend
    )
    if metadata.is_file() and json.loads(metadata.read_text()).get("backend") != actual_backend:
        raise ValueError("Use a fresh editing runtime destination when changing native backends")
    destination.mkdir(parents=True, exist_ok=True)
    asset = PREBUILT.get((sys.platform, backend))
    if asset:
        # Linux CUDA/ROCm editions use the native Vulkan backend. This avoids
        # adding a GPU compiler toolchain to the existing hosted release builds.
        # The worker still measures the selected GPU through PyTorch.
        files = unpack_verified_release(asset, destination)
        if ("sd-cli.exe" if sys.platform == "win32" else "sd-cli") not in files:
            raise RuntimeError("The verified native archive contains no editing executable")
        if sys.platform == "win32" and backend == "CUDA":
            unpack_verified_release(WINDOWS_CUDA_RUNTIME, destination)
        write_notices(source, destination, revision, actual_backend)
        print(f"Prepared pinned editing runtime ({actual_backend}); no models were loaded.")
        return
    build_dir = ROOT / ".cache" / f"sd-build-{backend.lower()}"
    options = [
        "-DCMAKE_BUILD_TYPE=Release",
        "-DSD_BUILD_SHARED_LIBS=OFF",
        "-DSD_BUILD_SHARED_GGML_LIB=OFF",
        "-DGGML_NATIVE=OFF",
        "-DSD_WEBP=OFF",
        "-DSD_WEBM=OFF",
        "-DGGML_METAL_EMBED_LIBRARY=ON",
    ]
    flag = {"MPS": "SD_METAL", "CUDA": "SD_CUDA", "ROCM": "SD_HIPBLAS", "XPU": "SD_SYCL"}.get(
        backend
    )
    if flag:
        options += [f"-D{flag}=ON"]
    if arch and sys.platform == "darwin":
        options += [f"-DCMAKE_OSX_ARCHITECTURES={arch}"]
    if sys.platform == "darwin":
        options += ["-DCMAKE_OSX_DEPLOYMENT_TARGET=12.0"]
        # Link against the SDK of the active toolchain (the release script selects
        # the Command Line Tools). A build directory configured earlier against
        # a newer Xcode SDK fails at link time ("tapi error: unknown
        # architecture"), so a changed SDK starts a fresh build directory.
        sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
        cache = build_dir / "CMakeCache.txt"
        if cache.is_file() and f"CMAKE_OSX_SYSROOT:STRING={sdk}\n" not in cache.read_text():
            shutil.rmtree(build_dir)
        options += [f"-DCMAKE_OSX_SYSROOT={sdk}"]
    run(["cmake", "-S", source, "-B", build_dir, *options])
    # Bound compilation concurrency on both developer machines and release runners.
    run(
        [
            "cmake",
            "--build",
            build_dir,
            "--config",
            "Release",
            "--target",
            "sd-cli",
            "--parallel",
            "2",
        ]
    )
    name = "sd-cli.exe" if os.name == "nt" else "sd-cli"
    executable = next(
        (p for p in (build_dir / "bin" / name, build_dir / "bin/Release" / name) if p.is_file()),
        None,
    )
    if executable is None:
        raise RuntimeError("The native editing executable was not built")
    shutil.copy2(executable, destination / name)
    # Copy backend runtime libraries emitted beside the binary, when applicable.
    for file in executable.parent.iterdir():
        if file.suffix in {".dll", ".dylib", ".so", ".metallib"}:
            shutil.copy2(file, destination / file.name)
    write_notices(source, destination, revision, backend)
    print(f"Built editing runtime ({backend}); no models were loaded.")


def write_notices(source, destination, revision, backend):
    for relative in (
        "LICENSE",
        "ggml/LICENSE",
        "thirdparty/oniguruma/COPYING",
        "thirdparty/utf8proc/LICENSE.md",
        "thirdparty/LICENSE.darts_clone.txt",
    ):
        shutil.copy2(source / relative, destination / relative.replace("/", "-"))
    notice = (
        f"stable-diffusion.cpp {revision}, https://github.com/leejet/stable-diffusion.cpp\n"
        "Includes GGML, Oniguruma, utf8proc, miniz/zip, stb, nlohmann/json and darts-clone.\n"
        "Their retained license notices are in the corresponding source files.\n"
        f"Source and submodule revisions: https://github.com/leejet/stable-diffusion.cpp/tree/{revision}\n"
    )
    (destination / "SOURCE-NOTICES.txt").write_text(notice, encoding="utf-8")
    # Preserve embedded third-party license texts in their original source files.
    headers = destination / "third-party-notices"
    headers.mkdir(exist_ok=True)
    for filename in ("miniz.h", "zip.h", "zip.c", "stb_image.h", "stb_image_write.h", "json.hpp"):
        shutil.copy2(source / "thirdparty" / filename, headers / filename)
    (destination / "runtime.json").write_text(
        json.dumps({"revision": revision, "backend": backend}) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["MPS", "CUDA", "ROCM", "CPU", "XPU"], required=True)
    parser.add_argument("--destination", type=Path, default=ROOT / ".cache/edit-runtime")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--arch")
    args = parser.parse_args()
    build(args.backend, args.destination, args.source, args.arch)


if __name__ == "__main__":
    main()
