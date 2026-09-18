# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir worker used by the additive Tauri desktop preview.

This excludes the optional PySide client. The resulting engine is a
private subprocess resource, not a second user-facing application.
"""

import os
import sys
from importlib import metadata
from importlib.metadata import PackageNotFoundError
from importlib.util import find_spec
from pathlib import Path

from PyInstaller.building.datastruct import TOC
from PyInstaller.config import CONF
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata


ROOT = Path(SPECPATH).parent
SOURCE = ROOT / "src"
target_arch = os.environ.get("LOCALSR_TARGET_ARCH") or CONF.get("target_arch")

spandrel_datas, spandrel_binaries, spandrel_hidden = collect_all("spandrel")
datas = spandrel_datas + [
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (
        str(SOURCE / "localsr" / "core" / "benchmark_references.json"),
        "localsr/core",
    ),
    (
        str(SOURCE / "localsr" / "video_models" / "seedvr2"),
        "localsr/video_models/seedvr2",
    ),
]
# Diffusers checks installed distribution versions when SeedVR2 is imported.
# Bundling importable modules alone leaves the packaged temporal engine broken.
datas += copy_metadata("diffusers", recursive=True)
datas += copy_metadata("torch", recursive=True) + copy_metadata("torchvision", recursive=True)
# These native media modules are explicit imports below, but not dependencies
# of Diffusers. Keep their wheel licenses and version metadata in the bundle.
for distribution in (
    "av", "opencv-python-headless", "rawpy", "psutil", "tifffile",
    "spandrel", "einops", "rotary-embedding-torch", "omegaconf", "gguf",
):
    datas += copy_metadata(distribution, recursive=True)
onnx_hidden = []
if find_spec("onnxruntime") is not None:
    # Runtime graph conversion is part of the Windows DirectML worker. Provider
    # DLLs are collected by PyInstaller's onnxruntime hook.
    onnx_datas, onnx_binaries, onnx_hidden = collect_all("onnx")
    datas += onnx_datas + copy_metadata("onnx", recursive=True)
    runtime_notices = collect_data_files(
        "onnxruntime", includes=["LICENSE", "ThirdPartyNotices.txt"]
    )
    if len(runtime_notices) != 2:
        raise RuntimeError("The ONNX Runtime license and third-party notices must be bundled")
    datas += runtime_notices
    spandrel_binaries += onnx_binaries
    for distribution in ("onnxruntime-directml", "onnxruntime"):
        try:
            datas += copy_metadata(distribution)
        except PackageNotFoundError:
            continue
    onnx_hidden += ["onnxruntime", "torch.onnx"]
hiddenimports = sorted(
    set(
        spandrel_hidden
        + onnx_hidden
        + collect_submodules("spandrel")
        + [
            "PIL._tkinter_finder",
            "av",
            "cv2",
            "rawpy",
            "tifffile",
            "torchvision",
        ]
    )
)

# Intel runtime wheels put SYCL/UR/oneMKL libraries outside torch/lib. Several
# GPU adapters and kernels use dlopen rather than ELF DT_NEEDED dependencies.
if sys.platform == "linux" and "+xpu" in metadata.version("torch").lower():
    sys.path[:0] = [str(ROOT / "scripts"), str(SOURCE)]
    from collect_xpu_runtime import collect_runtime

    runtime = collect_runtime(
        ROOT / "staging",
        torch_version=metadata.version("torch"),
        prefix=Path(sys.prefix),
        distributions=metadata.distributions(),
    )
    spandrel_binaries += runtime.binaries
    datas += runtime.datas
    for distribution_name in runtime.metadata_names:
        datas += copy_metadata(distribution_name)

# Filter out deeply nested license and third-party vendor subtrees in wheel
# dist-info (notably kineto/dynolog/civetweb inside torch.dist-info) that exceed
# the Win32 MAX_PATH (260 characters) limit during PyInstaller assembly.
# Distribution metadata needed for runtime version checks (METADATA, entry_points.txt,
# top-level license files) is preserved.
filtered_datas = []
for src, dst in datas:
    normalized_dst = dst.replace("\\", "/")
    if "/licenses/third_party" in normalized_dst or normalized_dst.startswith("licenses/third_party"):
        continue
    if "licenses" in normalized_dst and normalized_dst.count("/") > 3:
        continue
    filtered_datas.append((src, dst))
datas = filtered_datas

analysis = Analysis(
    [str(ROOT / "packaging" / "worker_entrypoint.py")],
    pathex=[str(SOURCE)],
    binaries=spandrel_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "PySide6",
        "jupyter",
        "matplotlib",
        "notebook",
        "pytest",
        # GNU readline is GPL-3.0 and only serves interactive shells; the worker has
        # none, and bundling it would put a GPL library in the process with PyTorch.
        "readline",
        "tkinter",
        # Triton is only required for torch.compile/inductor. The packaged
        # worker does not enable that path, and linuxdeploy cannot patch
        # Triton's large native library while producing the CUDA AppImage.
        "triton",
    ],
    noarchive=False,
    optimize=1,
)

if sys.platform == "darwin" and target_arch in ("arm64", "x86_64"):
    sys.path.insert(0, str(ROOT / "scripts"))
    import filter_macho_architecture
    
    report_path = Path(os.environ.get(
        "LOCALSR_ARCHITECTURE_REPORT",
        str(ROOT / "staging" / "architecture-pruning-report.json"),
    ))
    analysis.binaries = TOC(
        filter_macho_architecture.filter_binaries(
            analysis.binaries, target_arch, report_path
        )
    )

# OpenCV's Windows wheel loads FFmpeg as a separate videoio plugin. LocalSR uses
# OpenCV only for face detection and drawing, so that plugin is never shipped.
analysis.binaries = TOC(
    entry
    for entry in analysis.binaries
    if not Path(entry[0]).name.lower().startswith("opencv_videoio_ffmpeg")
)
# PyTorch's Windows CUDA wheel also carries multi-GPU cuSOLVER and NVRTC's alternate
# build, which the CUDA Toolkit EULA's Attachment A does not list as distributable
# (packaging/nvidia/redistributables.json). PyTorch loads neither.
analysis.binaries = TOC(
    entry
    for entry in analysis.binaries
    if not (
        Path(entry[0]).name.lower().startswith(("cusolvermg", "libcusolvermg"))
        or Path(entry[0]).name.lower().endswith(".alt.dll")
    )
)

pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="localsr-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch=target_arch,
)
# Filter analysis.datas after Analysis and all built-in PyInstaller hooks have
# run to remove deeply nested license and vendor trees in wheel dist-info
# (notably kineto/dynolog/civetweb inside torch.dist-info) that exceed
# Win32 MAX_PATH (260 characters) during COLLECT assembly on Windows.
filtered_analysis_datas = []
for entry in analysis.datas:
    dest_name = entry[0].replace("\\", "/")
    if "/licenses/third_party" in dest_name or dest_name.startswith("licenses/third_party"):
        continue
    if "licenses" in dest_name and dest_name.count("/") > 3:
        continue
    if len(entry[0]) > 140 and "dist-info" in dest_name:
        continue
    filtered_analysis_datas.append(entry)
analysis.datas = TOC(filtered_analysis_datas)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="engine",
)

# Fail the build if any bundled media component falls outside the codec policy
# (GPL/patent-licensed codecs such as x264, x265, H.264, HEVC or AAC). Private,
# never-distributed preview builds may opt out explicitly.
import subprocess  # noqa: E402

private_preview = os.environ.get("LOCALSR_PRIVATE_PREVIEW_MEDIA") == "1"

policy_check = subprocess.run(
    [
        sys.executable,
        str(ROOT / "scripts" / "verify_codec_allowlist.py"),
        "--tree",
        str(Path(DISTPATH) / "engine"),
    ],
    capture_output=True,
    text=True,
)
nvidia_check = subprocess.run(
    [
        sys.executable,
        str(ROOT / "scripts" / "verify_nvidia_redistributables.py"),
        str(Path(DISTPATH) / "engine"),
    ],
    capture_output=True,
    text=True,
)
if nvidia_check.returncode != 0 and not private_preview:
    raise SystemExit(
        "The frozen worker contains NVIDIA libraries that may not be redistributed:\n"
        + nvidia_check.stdout
        + nvidia_check.stderr
    )
if policy_check.returncode != 0 and not private_preview:
    raise SystemExit(
        "The frozen worker violates packaging/ffmpeg/codec-policy.json:\n"
        + policy_check.stdout
        + policy_check.stderr
    )
