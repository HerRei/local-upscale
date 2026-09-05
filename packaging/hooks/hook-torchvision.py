"""Collect torchvision's compiled operator extensions into the bundle.

PyInstaller's community hook (_pyinstaller_hooks_contrib/stdhooks/hook-torchvision.py)
only collects ``torchvision._C``. PyTorch stable-channel builds (torchvision
0.29+) instead ship the compiled ops as ``torchvision._C_stable`` and
``torchvision.image_stable``, and torchvision locates them on disk through
``importlib`` file-finding rather than an import statement, so PyInstaller
never sees them and silently drops the shared libraries. The worker then dies
at startup with ``RuntimeError: operator torchvision::nms does not exist``.

This hook collects whichever extension suffixes the installed torchvision
actually provides so both release channels keep working.
"""

import os

from PyInstaller.utils.hooks import collect_dynamic_libs, get_package_paths

_hidden = ["torchvision"]
for _name in sorted(os.listdir(get_package_paths("torchvision")[1])):
    if _name.endswith((".so", ".dylib", ".dll", ".pyd")) and not _name.startswith("."):
        _root, _ = os.path.splitext(_name)
        if _root != "__init__":
            _hidden.append(f"torchvision.{_root}")

hiddenimports = sorted(set(_hidden))
binaries = collect_dynamic_libs("torchvision")
