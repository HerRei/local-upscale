import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def main():
    if "--worker" in sys.argv:
        from localsr.worker.server import main as worker_main

        worker_main()
        return

    app = QApplication(sys.argv)
    app.setApplicationName("LocalSR")
    app.setOrganizationName("LocalSR")
    app.setApplicationDisplayName("LocalSR")
    if "--legacy" in sys.argv:
        from localsr.ui.main_window import MainWindow

        window = MainWindow()
        window.show()
    else:
        from localsr.ui.qml_app import create_qml_application

        engine, _controller = create_qml_application()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(1500, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
