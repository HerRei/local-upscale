# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir worker used by the additive Tauri desktop preview.

This intentionally excludes both Slint and PySide. The resulting engine is a
private subprocess resource, not a second user-facing application.
"""

import os
import sys
from pathlib import Path

from PyInstaller.config import CONF
from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH).parent
SOURCE = ROOT / "src"
target_arch = os.environ.get("LOCALSR_TARGET_ARCH") or CONF.get("target_arch")

spandrel_datas, spandrel_binaries, spandrel_hidden = collect_all("spandrel")
datas = spandrel_datas + [
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
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
            "rawpy",
            "tifffile",
            "torchvision",
        ]
    )
)

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
