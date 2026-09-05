"""Keep Microsoft's explicitly loaded DirectML DLL beside its Python package."""

from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = collect_dynamic_libs("torch_directml")
hiddenimports = ["torch_directml_native"]
