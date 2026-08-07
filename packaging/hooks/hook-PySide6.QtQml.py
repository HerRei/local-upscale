"""Collect only the QML modules used by LocalSR.

PyInstaller's default QtQml hook intentionally bundles every module shipped in
the PySide wheel, including WebEngine and Qt Quick 3D. LocalSR uses the Basic
Qt Quick Controls style, so carrying those unrelated frameworks adds hundreds
of megabytes without adding functionality.
"""

from pathlib import PurePosixPath

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()

_ALLOWED_MODULES = {
    "QtQml",
    "QtQml/Models",
    "QtQml/WorkerScript",
    "QtQuick",
    "QtQuick/Controls",
    "QtQuick/Controls/Basic",
    "QtQuick/Controls/impl",
    "QtQuick/Layouts",
    "QtQuick/Templates",
    "QtQuick/Window",
}


def _is_used_module(entry) -> bool:
    destination = PurePosixPath(str(entry[1]).replace("\\", "/"))
    parts = destination.parts
    try:
        qml_index = parts.index("qml")
    except ValueError:
        return False
    module = "/".join(parts[qml_index + 1 :])
    return module in _ALLOWED_MODULES


binaries += [entry for entry in qml_binaries if _is_used_module(entry)]
datas += [entry for entry in qml_datas if _is_used_module(entry)]
