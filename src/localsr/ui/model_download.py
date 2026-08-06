import threading

from PySide6.QtCore import QObject, Signal, Slot

from localsr.core.model_catalog import CatalogModel, download_model


class ModelDownloadWorker(QObject):
    progress = Signal(int, int)
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, model: CatalogModel, destination: str):
        super().__init__()
        self.model = model
        self.destination = destination
        self.cancel_event = threading.Event()

    @Slot()
    def run(self):
        try:
            path = download_model(
                self.model,
                self.destination,
                progress_callback=self.progress.emit,
                cancel_event=self.cancel_event,
            )
        except Exception as error:  # noqa: BLE001 - report failures across the thread boundary
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(error))
            return
        self.completed.emit(str(path))

    def cancel(self):
        self.cancel_event.set()
