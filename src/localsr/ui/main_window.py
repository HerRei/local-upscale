import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from localsr.core.estimator import (
    estimate_resources,
    format_bytes,
    format_duration,
    format_duration_range,
)
from localsr.core.model_catalog import CATALOG_BY_ID, MODEL_CATALOG, ModelStore
from localsr.protocol.client import WorkerClient
from localsr.protocol.messages import (
    CancelRequest,
    CapabilitiesRequest,
    InspectRequest,
    JobRequest,
)
from localsr.ui.model_download import ModelDownloadWorker

CUSTOM_MODEL_ID = "__custom__"


class DropLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setText("Drop Image Here\nor Click 'Choose Image'")
        self.setStyleSheet("border: 2px dashed #aaa; padding: 20px; font-size: 16px;")
        self.setAcceptDrops(True)
        self.main_window = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and self.main_window:
            self.main_window.set_image(urls[0].toLocalFile())


class MainWindow(QMainWindow):
    def __init__(self, start_worker=True, settings=None):
        super().__init__()
        self.setWindowTitle("LocalSR")
        self.resize(720, 920)
        self.settings = settings if settings is not None else QSettings("LocalSR", "LocalSR")

        self.image_path = None
        self.image_w = 0
        self.image_h = 0
        self.model_path = None
        self.custom_model_path = None
        self.model_scale = 1
        self.current_model_info = {}
        self.output_dir = os.path.expanduser("~")
        self.current_job_id = None
        self.current_estimate = None
        self.job_started_at = None
        self.job_effective_megapixels = 0.0
        self.runtime_warning = ""

        self.model_store = ModelStore()
        self.download_thread = None
        self.download_worker = None
        self.pending_device_setting = "cpu"
        self.capability_report = self._fallback_capabilities()

        self.init_ui()

        self.worker = WorkerClient(self)
        self.worker.worker_ready.connect(self.on_worker_ready)
        self.worker.model_info_received.connect(self.on_model_info)
        self.worker.capabilities_received.connect(self.on_capabilities)
        self.worker.job_started.connect(self.on_job_started)
        self.worker.progress_updated.connect(self.on_progress)
        self.worker.job_completed.connect(self.on_job_completed)
        self.worker.job_cancelled.connect(self.on_job_cancelled)
        self.worker.job_failed.connect(self.on_job_failed)
        self.worker.warning.connect(self.on_warning)
        self.worker.worker_error.connect(self.on_worker_error)
        self.worker.log_received.connect(self.on_log)

        self.load_settings()
        if start_worker:
            self.worker.start()

    @staticmethod
    def _fallback_capabilities():
        return {
            "system_ram_total": 0,
            "system_ram_available": 0,
            "devices": [
                {
                    "id": "cpu",
                    "type": "cpu",
                    "name": "CPU (hardware check pending)",
                    "total_memory": 0,
                    "free_memory": 0,
                    "supports_fp16": False,
                    "recommended_tile_sizes": [64, 128, 192, 256],
                }
            ],
        }

    def init_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)
        central = QWidget()
        scroll.setWidget(central)
        layout = QVBoxLayout(central)

        self.drop_label = DropLabel()
        self.drop_label.main_window = self
        layout.addWidget(self.drop_label)

        self.img_info_label = QLabel("No image selected")
        layout.addWidget(self.img_info_label)

        self.predicted_size_label = QLabel("Predicted Output: -")
        self.predicted_size_label.setStyleSheet("color: gray;")
        layout.addWidget(self.predicted_size_label)

        self.btn_img = QPushButton("Choose Image")
        self.btn_img.clicked.connect(self.choose_image)
        layout.addWidget(self.btn_img)

        model_group = QGroupBox("Upscale Model")
        model_layout = QVBoxLayout(model_group)
        self.combo_model = QComboBox()
        for model in MODEL_CATALOG:
            self.combo_model.addItem(model.name, model.model_id)
        self.combo_model.addItem("Use my own checkpoint…", CUSTOM_MODEL_ID)
        model_layout.addWidget(self.combo_model)

        self.model_description = QLabel()
        self.model_description.setWordWrap(True)
        self.model_description.setOpenExternalLinks(True)
        model_layout.addWidget(self.model_description)

        model_actions = QHBoxLayout()
        self.btn_download_model = QPushButton("Download Model")
        self.btn_download_model.clicked.connect(self.start_model_download)
        self.btn_model = QPushButton("Choose Model File…")
        self.btn_model.clicked.connect(self.choose_model)
        model_actions.addWidget(self.btn_download_model)
        model_actions.addWidget(self.btn_model)
        model_layout.addLayout(model_actions)

        self.model_download_progress = QProgressBar()
        self.model_download_progress.setRange(0, 100)
        self.model_download_progress.setVisible(False)
        model_layout.addWidget(self.model_download_progress)
        self.model_status_label = QLabel()
        self.model_status_label.setWordWrap(True)
        model_layout.addWidget(self.model_status_label)
        layout.addWidget(model_group)

        self.model_info_group = QGroupBox("Model Information")
        mi_layout = QFormLayout(self.model_info_group)
        self.mi_name = QLabel("-")
        self.mi_arch = QLabel("-")
        self.mi_scale = QLabel("-")
        self.mi_params = QLabel("-")
        self.mi_warn = QLabel("")
        self.mi_warn.setWordWrap(True)
        self.mi_warn.setStyleSheet("color: #b26a00;")
        mi_layout.addRow("Filename:", self.mi_name)
        mi_layout.addRow("Architecture:", self.mi_arch)
        mi_layout.addRow("Scale:", self.mi_scale)
        mi_layout.addRow("Parameters:", self.mi_params)
        mi_layout.addRow("", self.mi_warn)
        layout.addWidget(self.model_info_group)

        out_group = QGroupBox("Output")
        out_layout = QFormLayout(out_group)
        dir_layout = QHBoxLayout()
        self.out_dir_label = QLabel(self.output_dir)
        self.out_dir_label.setWordWrap(True)
        self.btn_out_dir = QPushButton("Browse")
        self.btn_out_dir.clicked.connect(self.choose_output_dir)
        dir_layout.addWidget(self.out_dir_label, 1)
        dir_layout.addWidget(self.btn_out_dir)
        out_layout.addRow("Folder:", dir_layout)
        self.combo_format = QComboBox()
        self.combo_format.addItems(["png", "jpg", "tif"])
        out_layout.addRow("Format:", self.combo_format)
        layout.addWidget(out_group)

        self.btn_adv = QToolButton()
        self.btn_adv.setText("Show Advanced Settings")
        self.btn_adv.setCheckable(True)
        self.btn_adv.clicked.connect(self.toggle_advanced)
        layout.addWidget(self.btn_adv)

        self.settings_group = QGroupBox("Advanced Settings")
        settings_layout = QFormLayout(self.settings_group)
        device_row = QHBoxLayout()
        self.combo_device = QComboBox()
        fallback = self.capability_report["devices"][0]
        self.combo_device.addItem("cpu", fallback)
        self.btn_refresh_hardware = QPushButton("Refresh")
        self.btn_refresh_hardware.clicked.connect(self.refresh_hardware)
        device_row.addWidget(self.combo_device, 1)
        device_row.addWidget(self.btn_refresh_hardware)
        self.hardware_label = QLabel("Hardware detection runs in the isolated worker.")
        self.hardware_label.setWordWrap(True)

        self.combo_tile = QComboBox()
        self.combo_tile.addItems(["64", "128", "192", "256"])
        self.combo_halo = QComboBox()
        self.combo_halo.addItems(["8", "16", "32", "64"])
        self.combo_precision = QComboBox()
        self.combo_precision.addItem("fp32")
        self.spin_jpg = QSpinBox()
        self.spin_jpg.setRange(1, 100)
        self.spin_jpg.setValue(98)
        self.check_meta = QCheckBox("Preserve Metadata")
        self.check_meta.setChecked(True)
        self.check_safe_mem = QCheckBox("Safe Memory Mode")
        self.check_safe_mem.setChecked(True)

        settings_layout.addRow("Device:", device_row)
        settings_layout.addRow("Detected:", self.hardware_label)
        settings_layout.addRow("Tile Size:", self.combo_tile)
        settings_layout.addRow("Halo:", self.combo_halo)
        settings_layout.addRow("Precision:", self.combo_precision)
        settings_layout.addRow("JPEG Quality:", self.spin_jpg)
        settings_layout.addRow("", self.check_meta)
        settings_layout.addRow("", self.check_safe_mem)
        self.settings_group.setVisible(False)
        layout.addWidget(self.settings_group)

        estimate_group = QGroupBox("Resource Estimate")
        estimate_group.setMinimumHeight(190)
        estimate_layout = QVBoxLayout(estimate_group)
        self.estimate_label = QLabel("Choose an image and a model to calculate an estimate.")
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.estimate_label.setMinimumHeight(48)
        self.memory_label = QLabel("Available RAM and device memory will appear here.")
        self.memory_label.setWordWrap(True)
        self.memory_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.memory_label.setMinimumHeight(54)
        self.live_resource_label = QLabel("Live memory will appear here while a job is running.")
        self.live_resource_label.setWordWrap(True)
        self.live_resource_label.setStyleSheet("color: #2457a6;")
        self.preflight_warning = QLabel()
        self.preflight_warning.setWordWrap(True)
        self.preflight_warning.setStyleSheet("color: #b3261e; font-weight: 600;")
        estimate_layout.addWidget(self.estimate_label)
        estimate_layout.addWidget(self.memory_label)
        estimate_layout.addWidget(self.live_resource_label)
        estimate_layout.addWidget(self.preflight_warning)
        layout.addWidget(estimate_group)

        self.btn_upscale = QPushButton("Upscale")
        self.btn_upscale.setStyleSheet("font-size: 20px; padding: 15px; font-weight: bold;")
        self.btn_upscale.clicked.connect(self.start_upscale)
        self.btn_upscale.setEnabled(False)
        layout.addWidget(self.btn_upscale)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.cancel_job)
        self.btn_cancel.setEnabled(False)
        layout.addWidget(self.btn_cancel)

        actions_layout = QHBoxLayout()
        self.btn_open = QPushButton("Open Result")
        self.btn_open.clicked.connect(self.open_result)
        self.btn_reveal = QPushButton("Reveal in File Manager")
        self.btn_reveal.clicked.connect(self.reveal_result)
        self.btn_open.setEnabled(False)
        self.btn_reveal.setEnabled(False)
        actions_layout.addWidget(self.btn_open)
        actions_layout.addWidget(self.btn_reveal)
        layout.addLayout(actions_layout)

        self.combo_model.currentIndexChanged.connect(self.on_model_selection_changed)
        self.combo_device.currentIndexChanged.connect(self.on_device_changed)
        self.combo_tile.currentTextChanged.connect(self.on_tile_changed)
        self.combo_halo.currentTextChanged.connect(self.update_estimate)
        self.combo_precision.currentTextChanged.connect(self.update_estimate)
        self.combo_format.currentTextChanged.connect(self.update_estimate)
        self.check_safe_mem.toggled.connect(self.update_estimate)
        self.on_model_selection_changed()
        self.on_tile_changed()

    def toggle_advanced(self):
        visible = self.btn_adv.isChecked()
        self.settings_group.setVisible(visible)
        self.btn_adv.setText("Hide Advanced Settings" if visible else "Show Advanced Settings")

    def load_settings(self):
        self.pending_device_setting = str(self.settings.value("device", "mps"))
        self.combo_tile.setCurrentText(str(self.settings.value("tile", "256")))
        self.combo_halo.setCurrentText(str(self.settings.value("halo", "32")))
        self.combo_precision.setCurrentText(str(self.settings.value("precision", "fp32")))
        self.spin_jpg.setValue(int(self.settings.value("jpeg_quality", 98)))
        self.combo_format.setCurrentText(str(self.settings.value("format", "png")))
        self.output_dir = str(self.settings.value("output_dir", self.output_dir))
        self.out_dir_label.setText(self.output_dir)

        custom_path = str(self.settings.value("custom_model_path", ""))
        if custom_path:
            self.custom_model_path = custom_path
        selected = str(self.settings.value("selected_model_id", MODEL_CATALOG[0].model_id))
        index = self.combo_model.findData(selected)
        self.combo_model.setCurrentIndex(max(index, 0))
        self.on_model_selection_changed()

    def save_settings(self):
        self.settings.setValue("device", self.current_device_id())
        self.settings.setValue("tile", self.combo_tile.currentText())
        self.settings.setValue("halo", self.combo_halo.currentText())
        self.settings.setValue("precision", self.combo_precision.currentText())
        self.settings.setValue("jpeg_quality", self.spin_jpg.value())
        self.settings.setValue("format", self.combo_format.currentText())
        self.settings.setValue("output_dir", self.output_dir)
        self.settings.setValue("selected_model_id", self.combo_model.currentData())
        if self.combo_model.currentData() == CUSTOM_MODEL_ID and self.model_path:
            self.settings.setValue("custom_model_path", self.custom_model_path or self.model_path)

    def selected_catalog_model(self):
        return CATALOG_BY_ID.get(self.combo_model.currentData())

    def on_model_selection_changed(self):
        model = self.selected_catalog_model()
        is_custom = model is None
        self.btn_model.setVisible(is_custom)
        self.btn_download_model.setVisible(not is_custom)
        self.current_model_info = {}
        self.model_scale = 1
        self._clear_model_info()

        if is_custom:
            self.model_path = self.custom_model_path
            self.model_description.setText(
                "Use a local Spandrel-compatible .pth, .pt, or .safetensors checkpoint. "
                "Only open pickle-based model files from sources you trust."
            )
            if self.model_path and Path(self.model_path).is_file():
                self.model_status_label.setText(f"Custom model: {self.model_path}")
                self.inspect_selected_model()
            else:
                self.model_path = None
                self.model_status_label.setText("No custom checkpoint selected.")
        else:
            self.model_description.setText(
                f"{model.description}<br>"
                f"<a href='{model.source_url}'>Official HAT project</a> · "
                f"{model.license_name} · {model.size_megabytes:.0f} MB download"
            )
            installed_path = self.model_store.path_for(model)
            if self.model_store.is_installed(model):
                self.model_path = str(installed_path)
                self.btn_download_model.setText("Use Installed Model")
                self.model_status_label.setText(
                    f"Installed on demand at {installed_path}. It is not bundled with LocalSR."
                )
                self.inspect_selected_model()
            else:
                self.model_path = None
                self.btn_download_model.setText(f"Download {model.size_megabytes:.0f} MB")
                self.model_status_label.setText("Not installed. Downloaded only when requested.")

        self.save_settings()
        self.update_predict()

    def _clear_model_info(self):
        self.mi_name.setText("-")
        self.mi_arch.setText("-")
        self.mi_scale.setText("-")
        self.mi_params.setText("-")
        self.mi_warn.clear()

    def inspect_selected_model(self):
        if self.model_path and hasattr(self, "worker"):
            self.worker.send_request(InspectRequest(model_path=self.model_path))

    def start_model_download(self):
        if self.download_worker is not None:
            self.download_worker.cancel()
            self.model_status_label.setText("Cancelling model download…")
            self.btn_download_model.setEnabled(False)
            return

        model = self.selected_catalog_model()
        if model is None:
            self.choose_model()
            return
        if self.model_store.is_installed(model):
            self.model_path = str(self.model_store.path_for(model))
            self.inspect_selected_model()
            return

        destination = self.model_store.path_for(model)
        thread = QThread(self)
        worker = ModelDownloadWorker(model, str(destination))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.on_model_download_progress)
        worker.completed.connect(self.on_model_download_completed)
        worker.failed.connect(self.on_model_download_failed)
        worker.cancelled.connect(self.on_model_download_cancelled)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self.on_model_download_finished)
        thread.finished.connect(thread.deleteLater)
        self.download_thread = thread
        self.download_worker = worker

        self.combo_model.setEnabled(False)
        self.btn_model.setEnabled(False)
        self.btn_download_model.setText("Cancel Download")
        self.model_download_progress.setValue(0)
        self.model_download_progress.setVisible(True)
        self.model_status_label.setText(
            f"Downloading {model.name}; the SHA-256 checksum will be verified…"
        )
        self._refresh_upscale_enabled()
        thread.start()

    def on_model_download_progress(self, downloaded, total):
        percentage = int(downloaded * 100 / max(1, total))
        self.model_download_progress.setValue(percentage)
        self.model_status_label.setText(
            f"Downloaded {format_bytes(downloaded)} of {format_bytes(total)} ({percentage}%)."
        )

    def on_model_download_completed(self, path):
        self.model_path = path
        self.model_download_progress.setValue(100)
        self.model_status_label.setText("Download complete and SHA-256 verified. Inspecting model…")
        self.inspect_selected_model()

    def on_model_download_failed(self, message):
        self.model_status_label.setText(f"Download failed: {message}")
        if self.isVisible():
            QMessageBox.critical(self, "Model Download Failed", message)

    def on_model_download_cancelled(self):
        self.model_status_label.setText("Model download cancelled; partial data was removed.")

    def on_model_download_finished(self):
        self.download_worker = None
        self.download_thread = None
        self.combo_model.setEnabled(True)
        self.btn_model.setEnabled(True)
        model = self.selected_catalog_model()
        if model is not None:
            installed = self.model_store.is_installed(model)
            self.btn_download_model.setText(
                "Use Installed Model" if installed else f"Download {model.size_megabytes:.0f} MB"
            )
            self.btn_download_model.setEnabled(True)
        self.model_download_progress.setVisible(False)
        self.model_download_progress.setValue(0)
        self._refresh_upscale_enabled()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.webp)",
        )
        if path:
            self.set_image(path)

    def set_image(self, path):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            if self.isVisible():
                QMessageBox.warning(self, "Unsupported Image", "LocalSR could not read this image.")
            return
        self.image_path = path
        self.image_w = pixmap.width()
        self.image_h = pixmap.height()
        self.img_info_label.setText(f"{os.path.basename(path)} | {self.image_w}x{self.image_h}")
        self.update_predict()

    def choose_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Model", "", "Models (*.pth *.pt *.safetensors)"
        )
        if not path:
            return
        custom_index = self.combo_model.findData(CUSTOM_MODEL_ID)
        self.combo_model.setCurrentIndex(custom_index)
        self.custom_model_path = path
        self.model_path = path
        self.settings.setValue("custom_model_path", path)
        self.model_status_label.setText(f"Custom model: {path}")
        self.inspect_selected_model()
        self.update_predict()

    def choose_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.output_dir)
        if path:
            self.output_dir = path
            self.out_dir_label.setText(path)
            self.update_estimate()

    def on_model_info(self, info):
        self.current_model_info = dict(info)
        self.mi_name.setText(info["filename"])
        self.mi_arch.setText(info["architecture"])
        self.model_scale = int(info["scale"])
        self.mi_scale.setText(f"{self.model_scale}×")
        parameter_count = int(info.get("parameter_count", 0))
        self.mi_params.setText(
            f"{parameter_count / 1_000_000:.1f} million" if parameter_count else "unknown"
        )
        self.mi_warn.setText("\n".join(info.get("warnings", [])))
        self.apply_hardware_constraints()
        self.update_predict()

    def refresh_hardware(self):
        self.btn_refresh_hardware.setEnabled(False)
        self.hardware_label.setText("Detecting hardware in the worker…")
        self.worker.send_request(CapabilitiesRequest())

    def on_capabilities(self, report):
        devices = report.get("devices", [])
        if not devices:
            return
        self.capability_report = report
        current_id = self.pending_device_setting or self.current_device_id()
        self.combo_device.blockSignals(True)
        self.combo_device.clear()
        for device in devices:
            self.combo_device.addItem(device["id"], device)
            self.combo_device.setItemData(
                self.combo_device.count() - 1,
                f"{device.get('name', device['id'])} — "
                f"{format_bytes(device.get('free_memory'))} available",
                Qt.ToolTipRole,
            )
        index = next(
            (i for i, device in enumerate(devices) if device.get("id") == current_id),
            0,
        )
        self.combo_device.setCurrentIndex(index)
        self.combo_device.blockSignals(False)
        self.pending_device_setting = ""
        self.btn_refresh_hardware.setEnabled(True)
        self.apply_hardware_constraints()

    def current_device(self):
        data = self.combo_device.currentData()
        return data if isinstance(data, dict) else self.capability_report["devices"][0]

    def current_device_id(self):
        return str(self.current_device().get("id", "cpu"))

    def on_device_changed(self):
        self.apply_hardware_constraints()

    def apply_hardware_constraints(self):
        device = self.current_device()
        free_memory = int(device.get("free_memory", 0))
        total_memory = int(device.get("total_memory", 0))
        self.hardware_label.setText(
            f"{device.get('name', device.get('id', 'Device'))}: "
            f"{format_bytes(free_memory)} free of {format_bytes(total_memory)}. "
            f"System RAM available: "
            f"{format_bytes(self.capability_report.get('system_ram_available'))}."
        )

        previous_tile = int(self.combo_tile.currentText() or 128)
        recommended = sorted(
            {int(value) for value in device.get("recommended_tile_sizes", [64, 128])}
        )
        if not recommended:
            recommended = [64]
        chosen_tile = min(recommended, key=lambda value: abs(value - previous_tile))
        self.combo_tile.blockSignals(True)
        self.combo_tile.clear()
        self.combo_tile.addItems([str(value) for value in recommended])
        self.combo_tile.setCurrentText(str(chosen_tile))
        self.combo_tile.blockSignals(False)

        previous_precision = self.combo_precision.currentText()
        allow_fp16 = bool(device.get("supports_fp16", False)) and bool(
            self.current_model_info.get("half_supported", False)
        )
        precisions = ["fp32", "fp16"] if allow_fp16 else ["fp32"]
        self.combo_precision.blockSignals(True)
        self.combo_precision.clear()
        self.combo_precision.addItems(precisions)
        if previous_precision in precisions:
            self.combo_precision.setCurrentText(previous_precision)
        self.combo_precision.blockSignals(False)
        self.combo_precision.setToolTip(
            "FP16 is shown only when both the selected device and model support it."
        )

        low_memory = 0 < free_memory < 3 * 1024**3
        if low_memory:
            self.check_safe_mem.setChecked(True)
            self.check_safe_mem.setEnabled(False)
            self.check_safe_mem.setToolTip(
                "Required because this device currently has less than 3 GB available."
            )
        else:
            self.check_safe_mem.setEnabled(True)
            self.check_safe_mem.setToolTip("")

        self.on_tile_changed()

    def on_tile_changed(self):
        tile = int(self.combo_tile.currentText() or 64)
        previous = int(self.combo_halo.currentText() or 16)
        valid_halos = [value for value in (8, 16, 32, 64) if value <= tile // 2]
        if not valid_halos:
            valid_halos = [max(1, tile // 4)]
        chosen = min(valid_halos, key=lambda value: abs(value - previous))
        self.combo_halo.blockSignals(True)
        self.combo_halo.clear()
        self.combo_halo.addItems([str(value) for value in valid_halos])
        self.combo_halo.setCurrentText(str(chosen))
        self.combo_halo.blockSignals(False)
        self.update_estimate()

    def update_predict(self):
        if self.image_w > 0 and self.model_scale > 1:
            self.predicted_size_label.setText(
                f"Predicted Output: {self.image_w * self.model_scale}x"
                f"{self.image_h * self.model_scale}"
            )
        else:
            self.predicted_size_label.setText("Predicted Output: -")
        self.update_estimate()

    def _throughput_key(self, device_type):
        model_key = self.combo_model.currentData() or self.current_model_info.get(
            "architecture", "custom"
        )
        safe_model_key = "".join(c for c in str(model_key) if c.isalnum() or c in "-_")
        return f"throughput_v2/{safe_model_key}/{device_type}"

    def update_estimate(self, *_args):
        if self.image_w <= 0 or self.image_h <= 0 or self.model_scale <= 1:
            self.current_estimate = None
            self.estimate_label.setText(
                "Choose an image and a valid model to calculate an estimate."
            )
            self.memory_label.setText(
                "Available RAM and device memory will appear after hardware detection."
            )
            self.preflight_warning.clear()
            self._refresh_upscale_enabled()
            return

        device = self.current_device()
        model = self.selected_catalog_model()
        output_parent = Path(self.output_dir).expanduser()
        while not output_parent.exists() and output_parent != output_parent.parent:
            output_parent = output_parent.parent
        try:
            available_disk = shutil.disk_usage(output_parent).free
        except OSError:
            available_disk = None

        throughput = self.settings.value(self._throughput_key(device.get("type", "cpu")))
        try:
            measured_throughput = float(throughput) if throughput is not None else None
        except (TypeError, ValueError):
            measured_throughput = None

        model_file_size = int(self.current_model_info.get("model_file_size", 0))
        if not model_file_size and model is not None:
            model_file_size = model.size_bytes
        self.current_estimate = estimate_resources(
            image_width=self.image_w,
            image_height=self.image_h,
            scale=self.model_scale,
            tile_size=int(self.combo_tile.currentText()),
            halo=int(self.combo_halo.currentText()),
            precision=self.combo_precision.currentText(),
            device_type=str(device.get("type", "cpu")),
            available_device_memory=int(device.get("free_memory", 0)) or None,
            available_system_memory=int(self.capability_report.get("system_ram_available", 0))
            or None,
            available_disk=available_disk,
            model_file_size=model_file_size,
            parameter_count=int(self.current_model_info.get("parameter_count", 0)),
            memory_factor=model.memory_factor if model else 1.0,
            time_factor=model.time_factor if model else 1.0,
            measured_seconds_per_megapixel=measured_throughput,
        )
        estimate = self.current_estimate
        confidence = (
            "calibrated from a completed run on this device"
            if estimate.calibrated
            else "uncalibrated first-run range; live ETA replaces it after the first tiles"
        )
        self.estimate_label.setText(
            f"Estimated time: {format_duration_range(estimate.seconds_low, estimate.seconds_high)} "
            f"({confidence}).\n"
            f"Work: {estimate.tile_count} tiles · output about "
            f"{format_bytes(estimate.output_bytes)} · temporary disk about "
            f"{format_bytes(estimate.working_disk_bytes)}."
        )
        device_type = str(device.get("type", "cpu"))
        device_available = int(device.get("free_memory", 0)) or None
        ram_available = int(self.capability_report.get("system_ram_available", 0)) or None
        if device_type == "cuda":
            memory_text = (
                f"VRAM now: {format_bytes(device_available)} available → about "
                f"{format_bytes(estimate.device_memory_remaining)} left. "
                f"RAM now: {format_bytes(ram_available)} available → about "
                f"{format_bytes(estimate.system_memory_remaining)} left."
            )
        elif device_type == "mps":
            available_values = [
                value for value in (device_available, ram_available) if value is not None
            ]
            projected_values = [
                value
                for value in (
                    estimate.device_memory_remaining,
                    estimate.system_memory_remaining,
                )
                if value is not None
            ]
            unified_available = min(available_values) if available_values else None
            projected = min(projected_values) if projected_values else None
            memory_text = (
                f"Unified memory now: {format_bytes(unified_available)} available → about "
                f"{format_bytes(projected)} left during the run."
            )
        else:
            memory_text = (
                f"RAM now: {format_bytes(ram_available)} available → about "
                f"{format_bytes(estimate.system_memory_remaining)} left during the run."
            )
        self.memory_label.setText(
            f"{memory_text}\nDisk now: {format_bytes(available_disk)} free → about "
            f"{format_bytes(estimate.disk_remaining)} left."
        )
        warnings = ([self.runtime_warning] if self.runtime_warning else []) + list(
            estimate.warnings
        )
        self.preflight_warning.setText("\n".join(warnings))
        self._refresh_upscale_enabled()

    def _refresh_upscale_enabled(self):
        blocking = bool(self.current_estimate and self.current_estimate.blocking)
        ready = bool(
            self.image_path
            and self.model_path
            and self.model_scale > 1
            and self.current_job_id is None
            and self.download_worker is None
            and not blocking
        )
        self.btn_upscale.setEnabled(ready)
        if blocking:
            self.btn_upscale.setToolTip("Resolve the resource warning before starting.")
        else:
            self.btn_upscale.setToolTip("")

    def on_log(self, data):
        print(f"[{data.get('level', 'info').upper()}] {data.get('message', '')}")

    def get_output_path(self):
        base = os.path.splitext(os.path.basename(self.image_path))[0]
        ext = self.combo_format.currentText()
        return os.path.join(self.output_dir, f"{base}_upscaled.{ext}")

    def start_upscale(self):
        if not self.btn_upscale.isEnabled():
            return
        self.update_estimate()
        if self.current_estimate and self.current_estimate.blocking:
            QMessageBox.warning(self, "Unsafe Resource Settings", self.preflight_warning.text())
            return

        self.save_settings()
        out_path = self.get_output_path()
        if os.path.exists(out_path):
            result = QMessageBox.question(
                self,
                "Overwrite?",
                f"{os.path.basename(out_path)} exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if result != QMessageBox.Yes:
                return

        self.current_job_id = str(uuid.uuid4())
        self.runtime_warning = ""
        self.job_started_at = time.monotonic()
        self.job_effective_megapixels = (
            self.current_estimate.effective_megapixels
            if self.current_estimate is not None
            else self.image_w * self.image_h / 1_000_000
        )
        request = JobRequest(
            job_id=self.current_job_id,
            image_path=self.image_path,
            model_path=self.model_path,
            output_path=out_path,
            output_format=self.combo_format.currentText(),
            device=self.current_device_id(),
            tile_size=int(self.combo_tile.currentText()),
            halo=int(self.combo_halo.currentText()),
            precision=self.combo_precision.currentText(),
            jpeg_quality=self.spin_jpg.value(),
            preserve_metadata=self.check_meta.isChecked(),
            safe_memory=self.check_safe_mem.isChecked(),
        )
        self.btn_upscale.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting…")
        self.live_resource_label.setText("Loading the model; live memory starts with inference…")
        self.btn_open.setEnabled(False)
        self.btn_reveal.setEnabled(False)
        self.worker.send_request(request)

    def cancel_job(self):
        if self.current_job_id:
            self.worker.send_request(CancelRequest(job_id=self.current_job_id))
            self.progress_label.setText("Cancelling…")
            self.btn_cancel.setEnabled(False)

    def on_progress(self, data):
        self.progress_bar.setValue(int(data["percentage"]))
        self.progress_label.setText(
            f"Tile {data['completed_tiles']}/{data['total_tiles']} "
            f"(size {data.get('active_tile_size')}) · {data['percentage']:.1f}% · "
            f"ETA {format_duration(data['estimated_remaining_seconds'])}"
        )
        device_free = int(data.get("device_free_memory", 0)) or None
        ram_available = int(data.get("system_ram_available", 0)) or None
        device_type = self.current_device().get("type", "cpu")
        if device_type == "cuda":
            live_memory = (
                f"Live: {format_bytes(device_free)} VRAM left · "
                f"{format_bytes(ram_available)} RAM left"
            )
        elif device_type == "mps":
            available_values = [
                value for value in (device_free, ram_available) if value is not None
            ]
            live_memory = (
                f"Live: {format_bytes(min(available_values) if available_values else None)} "
                "unified memory left"
            )
        else:
            live_memory = f"Live: {format_bytes(ram_available)} RAM left"
        self.live_resource_label.setText(live_memory)

    def on_worker_ready(self):
        if not self.current_job_id:
            self.progress_label.setText("Worker ready.")
        self.refresh_hardware()
        self.inspect_selected_model()

    def on_job_started(self, job_id):
        self.btn_upscale.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_label.setText("Processing job…")
        self.live_resource_label.setText("Loading the model; live memory starts with inference…")

    def on_job_completed(self, job_id, result):
        if self.job_started_at and self.job_effective_megapixels > 0:
            elapsed = time.monotonic() - self.job_started_at
            seconds_per_megapixel = elapsed / self.job_effective_megapixels
            if elapsed >= 3.0:
                device_type = self.current_device().get("type", "cpu")
                key = self._throughput_key(device_type)
                previous = self.settings.value(key)
                try:
                    previous_value = float(previous) if previous is not None else None
                except (TypeError, ValueError):
                    previous_value = None
                calibrated_value = (
                    previous_value * 0.7 + seconds_per_megapixel * 0.3
                    if previous_value and previous_value > 0
                    else seconds_per_megapixel
                )
                self.settings.setValue(key, calibrated_value)
        self.current_job_id = None
        self.job_started_at = None
        self.runtime_warning = ""
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Completed successfully!")
        self.live_resource_label.setText("Run complete; refreshing available memory…")
        self.btn_open.setEnabled(True)
        self.btn_reveal.setEnabled(True)
        self.refresh_hardware()
        self.update_estimate()

    def on_job_cancelled(self, job_id):
        self.current_job_id = None
        self.job_started_at = None
        self.runtime_warning = ""
        self.btn_cancel.setEnabled(False)
        self.progress_label.setText("Cancelled by user.")
        self.live_resource_label.setText("Cancelled; refreshing available memory…")
        output_exists = os.path.exists(self.get_output_path()) if self.image_path else False
        self.btn_open.setEnabled(output_exists)
        self.btn_reveal.setEnabled(output_exists)
        self.refresh_hardware()
        self.update_estimate()

    def on_job_failed(self, job_id, error_message):
        self.current_job_id = None
        self.job_started_at = None
        self.btn_cancel.setEnabled(False)
        friendly = self._friendly_error(error_message)
        self.runtime_warning = friendly
        self.progress_label.setText(f"Failed: {friendly}")
        self.live_resource_label.setText("Run stopped; refreshing available memory…")
        self.refresh_hardware()
        self.update_estimate()
        if self.isVisible():
            QMessageBox.critical(self, "Upscale Failed", friendly)

    @staticmethod
    def _friendly_error(error_message):
        message = error_message or "Unknown worker error."
        lower = message.lower()
        if any(term in lower for term in ("out of memory", "cannot allocate", "allocation")):
            return (
                "The selected device ran out of memory. Choose a smaller tile, enable Safe "
                "Memory Mode, close memory-heavy applications, or switch to CPU. "
                f"Technical detail: {message}"
            )
        if any(term in lower for term in ("no space", "disk full")):
            return (
                "The output drive ran out of free space. Choose another output folder or free "
                f"disk space. Technical detail: {message}"
            )
        if "cuda" in lower and ("driver" in lower or "available" in lower):
            return f"The CUDA device is unavailable. Refresh hardware or choose CPU. {message}"
        return message

    def on_warning(self, message):
        self.runtime_warning = message
        self.update_estimate()
        self.progress_label.setText(f"Warning: {message}")
        if self.isVisible():
            QMessageBox.warning(self, "LocalSR Warning", message)

    def on_job_result(self, data):
        """Compatibility handler for older workers that only emit job_result."""
        if data.get("type") == "job_completed" or data.get("success") is True:
            self.on_job_completed(data.get("job_id", ""), data)
        elif data.get("type") == "job_cancelled":
            self.on_job_cancelled(data.get("job_id", ""))
        else:
            self.on_job_failed(data.get("job_id", ""), data.get("error_message", "Unknown error"))

    def on_worker_error(self, error):
        if getattr(self.worker, "_is_shutting_down", False):
            return
        self.progress_label.setText(error)
        self.current_job_id = None
        self.btn_cancel.setEnabled(False)
        self._refresh_upscale_enabled()
        if "exited" in error and "code 0" not in error:
            self.worker.start()

    def open_result(self):
        path = self.get_output_path()
        if not os.path.exists(path):
            return
        if sys.platform == "darwin":
            subprocess.call(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.call(["xdg-open", path])

    def reveal_result(self):
        path = self.get_output_path()
        if not os.path.exists(path):
            return
        if sys.platform == "darwin":
            subprocess.call(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.call(["explorer", "/select,", path])
        else:
            subprocess.call(["xdg-open", os.path.dirname(path)])

    def closeEvent(self, event):
        self.save_settings()
        if self.download_worker is not None:
            self.download_worker.cancel()
        if self.download_thread is not None and self.download_thread.isRunning():
            self.download_thread.quit()
            self.download_thread.wait(16_000)
        self.worker.stop()
        event.accept()
