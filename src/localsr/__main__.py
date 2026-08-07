import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication


def _record_smoke_stage(stage: str) -> None:
    report_path = os.environ.get("LOCALSR_SMOKE_REPORT")
    if report_path:
        Path(report_path).write_text(stage, encoding="utf-8")


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
            # Frozen Qt Quick processes can deadlock during teardown on the
            # non-interactive Windows Server desktop used by GitHub Actions.
            # Reaching this point proves that the packaged QML/controller loaded.
            if sys.platform == "win32" and getattr(sys, "frozen", False):
                os._exit(0)
            _controller.shutdown()
            return
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
