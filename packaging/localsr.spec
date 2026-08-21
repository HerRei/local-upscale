# -*- mode: python ; coding: utf-8 -*-

import subprocess
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH).parent
SOURCE = ROOT / "src"
ICON_DIR = ROOT / "packaging" / "icons"

with (ROOT / "pyproject.toml").open("rb") as stream:
    APP_VERSION = tomllib.load(stream)["project"]["version"]

macos_dialog_helper = None
native_helpers = []
if sys.platform == "darwin":
    native_build_dir = ROOT / "build" / "native"
    native_build_dir.mkdir(parents=True, exist_ok=True)
    macos_dialog_helper = native_build_dir / "LocalSRDialog"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-O",
            str(ROOT / "packaging" / "macos" / "LocalSRDialog.swift"),
            "-o",
            str(macos_dialog_helper),
        ],
        check=True,
    )
    native_helpers.append(("LocalSRDialog", str(macos_dialog_helper), "BINARY"))

spandrel_datas, spandrel_binaries, spandrel_hidden = collect_all("spandrel")
slint_datas, slint_binaries, slint_hidden = collect_all("slint")
datas = spandrel_datas + slint_datas + [
    (str(SOURCE / "localsr" / "ui" / "slint"), "localsr/ui/slint"),
    # SeedVR2 vendored configs, text embeddings, and license travel as data
    # so the temporal engine finds them next to its modules.
    (
        str(SOURCE / "localsr" / "video_models" / "seedvr2"),
        "localsr/video_models/seedvr2",
    ),
]
hiddenimports = sorted(
    set(
        spandrel_hidden
        + slint_hidden
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
        binaries=spandrel_binaries + slint_binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[str(ROOT / "packaging" / "hooks")],
        hooksconfig={},
        runtime_hooks=[],
        # Analysis mutates this collection; each executable needs its own copy.
        excludes=list(excludes),
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
    native_helpers,
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
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSMinimumSystemVersion": "12.0",
            # The bundle contains a console worker as a sibling executable.
            # Override PyInstaller's collection-level inference so the GUI is
            # still a normal foreground macOS application.
            "LSBackgroundOnly": False,
            "NSHighResolutionCapable": True,
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Images and Videos",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                    "LSItemContentTypes": [
                        "public.image",
                        "public.jpeg",
                        "public.png",
                        "public.tiff",
                        "public.movie",
                        "public.video",
                        "com.compuserve.gif",
                        "org.webmproject.webp",
                        "com.adobe.raw-image",
                        "public.content",
                        "public.item",
                        "public.data",
                    ],
                    "CFBundleTypeExtensions": [
                        "png",
                        "jpg",
                        "jpeg",
                        "webp",
                        "dng",
                        "tif",
                        "tiff",
                        "mp4",
                        "mov",
                        "m4v",
                        "avi",
                        "mkv",
                        "webm",
                    ],
                }
            ],
        },
    )

