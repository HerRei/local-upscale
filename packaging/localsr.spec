# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH).parent
SOURCE = ROOT / "src"
ICON_DIR = ROOT / "packaging" / "icons"

spandrel_datas, spandrel_binaries, spandrel_hidden = collect_all("spandrel")
datas = spandrel_datas + [
    (str(SOURCE / "localsr" / "ui" / "qml"), "localsr/ui/qml"),
]
hiddenimports = sorted(
    set(
        spandrel_hidden
        + collect_submodules("spandrel")
        + [
            "PIL._tkinter_finder",
            "rawpy",
            "tifffile",
            "torchvision",
        ]
    )
)
excludes = [
    "IPython",
    "jupyter",
    "matplotlib",
    "notebook",
    "pytest",
    "tkinter",
]


def analysis(script):
    return Analysis(
        [str(script)],
        pathex=[str(SOURCE)],
        binaries=spandrel_binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[str(ROOT / "packaging" / "hooks")],
        hooksconfig={},
        runtime_hooks=[],
        excludes=excludes,
        noarchive=False,
        optimize=1,
    )


gui_analysis = analysis(ROOT / "packaging" / "entrypoint.py")
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="LocalSR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON_DIR / ("LocalSR.icns" if sys.platform == "darwin" else "LocalSR.ico"))
    if sys.platform in {"darwin", "win32"}
    else None,
)

worker_analysis = analysis(ROOT / "packaging" / "worker_entrypoint.py")
worker_pyz = PYZ(worker_analysis.pure)
worker_exe = EXE(
    worker_pyz,
    worker_analysis.scripts,
    [],
    exclude_binaries=True,
    name="LocalSRWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

collection = COLLECT(
    gui_exe,
    worker_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    worker_analysis.binaries,
    worker_analysis.datas,
    strip=False,
    upx=False,
    name="LocalSR",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="LocalSR.app",
        icon=str(ICON_DIR / "LocalSR.icns"),
        bundle_identifier="com.localsr.desktop",
        info_plist={
            "CFBundleDisplayName": "LocalSR",
            "CFBundleShortVersionString": "0.3.0",
            "CFBundleVersion": "0.3.0",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        },
    )
