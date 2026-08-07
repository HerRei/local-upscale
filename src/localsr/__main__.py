import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication


def _record_smoke_stage(stage: str) -> None:
    report_path = os.environ.get("LOCALSR_SMOKE_REPORT")
    if report_path:
        Path(report_path).write_text(stage, encoding="utf-8")


def _verify_frozen_windows_package() -> None:
    """Validate the frozen UI dependency graph without a headless QML render loop."""
    from localsr.ui import qml_app

    qml_path = Path(qml_app.__file__).with_name("qml") / "Main.qml"
    if not qml_path.is_file():
        raise RuntimeError(f"Packaged QML entry point is missing: {qml_path}")
    _record_smoke_stage("package-ready")


def main():
    if "--worker" in sys.argv:
        from localsr.worker.server import main as worker_main

        worker_main()
        return

    app = QApplication(sys.argv)
    app.setApplicationName("LocalSR")
    app.setOrganizationName("LocalSR")
    app.setApplicationDisplayName("LocalSR")
    smoke_test = "--smoke-test" in sys.argv
    if smoke_test:
        _record_smoke_stage("qt-ready")
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            _verify_frozen_windows_package()
            os._exit(0)
    if "--legacy" in sys.argv:
        from localsr.ui.main_window import MainWindow

        window = MainWindow()
        window.show()
        if smoke_test:
            window.close()
            return
    else:
        from localsr.ui.qml_app import create_qml_application

        engine, _controller = create_qml_application(start_worker=not smoke_test)
        if smoke_test:
            _record_smoke_stage("qml-ready")
            _controller.shutdown()
            return
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
