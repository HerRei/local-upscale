# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir worker used by the additive Tauri desktop preview.

This intentionally excludes both Slint and PySide. The resulting engine is a
private subprocess resource, not a second user-facing application.
"""

import os
import sys
from importlib import metadata
from pathlib import Path

from PyInstaller.config import CONF
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


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
hiddenimports = sorted(
    set(
        spandrel_hidden
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
        "slint",
        "tkinter",
    ],
    noarchive=False,
    optimize=1,
)

if sys.platform == "darwin" and target_arch in ("arm64", "x86_64"):
    sys.path.insert(0, str(ROOT / "scripts"))
    import filter_macho_architecture
    from PyInstaller.building.datastruct import TOC
    
    report_path = ROOT / "staging" / "architecture-pruning-report.json"
    analysis.binaries = TOC(
        filter_macho_architecture.filter_binaries(
            analysis.binaries, target_arch, report_path
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
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="engine",
)
