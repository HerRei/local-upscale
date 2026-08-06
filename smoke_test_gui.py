"""Small cross-platform GUI/worker startup and shutdown smoke test."""

import sys

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QApplication

from localsr.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    result = {"ready": False}

    def on_ready():
        result["ready"] = True
        window.close()
        QTimer.singleShot(0, app.quit)

    window.worker.worker_ready.connect(on_ready)
    QTimer.singleShot(10_000, app.quit)
    app.exec()

    if window.worker.process.state() != QProcess.NotRunning:
        window.close()
    clean_exit = window.worker.process.state() == QProcess.NotRunning
    if result["ready"] and clean_exit:
        print("SUCCESS: worker became ready and exited cleanly")
        return 0
    print("FAILED: GUI worker did not complete its startup/shutdown cycle")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
