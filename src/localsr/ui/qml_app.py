from pathlib import Path

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from .controller import create_controller


def create_qml_application(start_worker: bool = True, settings=None):
    QQuickStyle.setStyle("Basic")
    engine = QQmlApplicationEngine()
    controller, provider = create_controller(start_worker=start_worker, settings=settings)
    engine.addImageProvider("localsr", provider)
    qml_path = Path(__file__).with_name("qml") / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        controller.shutdown()
        raise RuntimeError(f"Could not load the LocalSR interface from {qml_path}")
    engine.rootObjects()[0].setProperty("localSR", controller)
    app = QCoreApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(controller.shutdown)
    engine._localsr_controller = controller
    engine._localsr_provider = provider
    return engine, controller
