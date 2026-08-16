import importlib
import os
import sys
from pathlib import Path


def _record_smoke_stage(stage: str) -> None:
    report_path = os.environ.get("LOCALSR_SMOKE_REPORT")
    if report_path:
        Path(report_path).write_text(stage, encoding="utf-8")


def main():
    os.environ.setdefault("SLINT_SCALE_FACTOR", "1.5")
    if "--worker" in sys.argv:
        from localsr.worker.server import main as worker_main

        worker_main()
        return

    smoke_test = "--smoke-test" in sys.argv
    if "--legacy" in sys.argv:
        try:
            QApplication = importlib.import_module("PySide6.QtWidgets").QApplication
            MainWindow = importlib.import_module("localsr.ui.main_window").MainWindow
        except ImportError as error:
            raise SystemExit(
                'The legacy UI is optional. Install it with: pip install -e ".[legacy]"'
            ) from error

        app = QApplication(sys.argv)
        app.setApplicationName("LocalSR")
        app.setOrganizationName("LocalSR")
        app.setApplicationDisplayName("LocalSR")
        window = MainWindow()
        window.show()
        if smoke_test:
            window.close()
            return
        raise SystemExit(app.exec())

    from localsr.ui.slint_app import create_slint_application

    application = create_slint_application(start_worker=not smoke_test)
    if smoke_test:
        _record_smoke_stage("package-ready")
        application.shutdown()
        return
    application.run()


if __name__ == "__main__":
    main()
