import os
import shutil
from pathlib import Path

from PySide6.QtCore import Property, QObject, QSettings, QTimer, QUrl, Signal, Slot

from localsr.core.estimator import format_bytes
from localsr.core.image_formats import is_raw_input
from localsr.core.model_catalog import MODEL_CATALOG, ModelPurpose
from localsr.core.presets import (
    NoCompatibleModelError,
    PresetMode,
    rank_models_for_preset,
    resolve_settings_for_model,
)
from localsr.protocol.messages import CapabilitiesRequest, PreviewRequest

from .main_window import CUSTOM_MODEL_ID, MainWindow
from .preview_provider import PreviewImageProvider


class LocalSRController(QObject):
    stateChanged = Signal()
    previewChanged = Signal()

    def __init__(
        self,
        backend: MainWindow,
        preview_provider: PreviewImageProvider,
        settings: QSettings | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.backend = backend
        self.preview_provider = preview_provider
        self.settings = settings or backend.settings
        self._preview_revision = 0
        self._progressive_revision = 0
        self._pending_preset: PresetMode | None = None
        self._preset_message = "Choose Quick or Best Quality, or tune settings yourself."
        self._preview_error = ""
        self._active_tile = (0.0, 0.0, 0.0, 0.0)
        self._memory_snapshot = dict(backend.capability_report)
        self._quick_estimate = ""
        self._best_estimate = ""
        self._denoise_estimate = ""
        self._show_progressive = False
        self._shutdown = False

        worker = backend.worker
        worker.preview_ready.connect(self._on_preview_ready)
        worker.preview_failed.connect(self._on_preview_failed)
        worker.tile_updated.connect(self._on_tile_update)
        worker.progress_updated.connect(self._on_progress)
        worker.capabilities_received.connect(self._on_capabilities)
        worker.model_info_received.connect(self._on_model_info)
        worker.job_started.connect(self._on_job_started)
        worker.job_completed.connect(self._on_job_completed)
        worker.job_cancelled.connect(self._on_job_stopped)
        worker.job_failed.connect(self._on_job_failed)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(180)
        self._poll_timer.timeout.connect(self.stateChanged)
        self._poll_timer.start()

        self._hardware_timer = QTimer(self)
        self._hardware_timer.setInterval(5000)
        self._hardware_timer.timeout.connect(self._refresh_idle_snapshot)
        self._hardware_timer.start()

    def _bump_preview(self, progressive: bool = False):
        if progressive:
            self._show_progressive = True
            self._progressive_revision += 1
        else:
            self._preview_revision += 1
        self.previewChanged.emit()
        self.stateChanged.emit()

    def _installed_ids(self) -> frozenset[str]:
        return frozenset(
            model.model_id
            for model in MODEL_CATALOG
            if self.backend.model_store.is_installed(model)
        )

    @Property("QVariantList", notify=stateChanged)
    def modelRows(self):
        installed = self._installed_ids()
        rows = [
            {
                "id": model.model_id,
                "name": model.name,
                "description": model.description,
                "architecture": model.architecture,
                "size": f"{model.size_megabytes:.0f} MB",
                "license": model.license_name,
                "installed": model.model_id in installed,
                "status": "Installed" if model.model_id in installed else "Optional download",
            }
            for model in MODEL_CATALOG
        ]
        rows.append(
            {
                "id": CUSTOM_MODEL_ID,
                "name": "Use my own checkpoint…",
                "description": "Load a compatible local Spandrel checkpoint.",
                "architecture": "Automatic",
                "size": "Local file",
                "license": "User supplied",
                "installed": bool(self.backend.custom_model_path),
                "status": "Custom",
            }
        )
        return rows

    @Property(int, notify=stateChanged)
    def modelIndex(self):
        return max(0, self.backend.combo_model.currentIndex())

    @Property(str, notify=stateChanged)
    def modelStatus(self):
        return self.backend.model_status_label.text()

    @Property(bool, notify=stateChanged)
    def downloadVisible(self):
        return bool(self.backend.download_worker is not None)

    @Property(int, notify=stateChanged)
    def downloadProgress(self):
        return self.backend.model_download_progress.value()

    @Property(str, notify=stateChanged)
    def downloadActionText(self):
        return self.backend.btn_download_model.text()

    @Property(bool, notify=stateChanged)
    def imageReady(self):
        return bool(self.backend.image_path)

    @Property(str, notify=stateChanged)
    def imageTitle(self):
        return Path(self.backend.image_path).name if self.backend.image_path else "Drop an image"

    @Property(str, notify=stateChanged)
    def imageSubtitle(self):
        if not self.backend.image_path:
            return "JPEG, PNG, TIFF, WebP or camera DNG"
        raw = " · RAW developed in worker" if is_raw_input(self.backend.image_path) else ""
        return f"{self.backend.image_w} × {self.backend.image_h}{raw}"

    @Property(str, notify=previewChanged)
    def sourcePreviewSource(self):
        return f"image://localsr/source?r={self._preview_revision}" if self.imageReady else ""

    @Property(str, notify=previewChanged)
    def progressivePreviewSource(self):
        if not getattr(self, "_show_progressive", False):
            return self.sourcePreviewSource
        return f"image://localsr/progressive?r={self._progressive_revision}"

    @Property(str, notify=stateChanged)
    def previewError(self):
        return self._preview_error

    @Property(float, notify=stateChanged)
    def activeTileX(self):
        return self._active_tile[0]

    @Property(float, notify=stateChanged)
    def activeTileY(self):
        return self._active_tile[1]

    @Property(float, notify=stateChanged)
    def activeTileWidth(self):
        return self._active_tile[2]

    @Property(float, notify=stateChanged)
    def activeTileHeight(self):
        return self._active_tile[3]

    @Property(bool, notify=stateChanged)
    def tileActive(self):
        return self._active_tile[2] > 0 and self.backend.current_job_id is not None

    @Property("QVariantList", notify=stateChanged)
    def deviceRows(self):
        return [
            {
                "id": device.get("id", "cpu"),
                "name": device.get("name", device.get("id", "Device")),
                "detail": f"{format_bytes(device.get('free_memory'))} available",
            }
            for device in self.backend.capability_report.get("devices", [])
        ]

    @Property(int, notify=stateChanged)
    def deviceIndex(self):
        return max(0, self.backend.combo_device.currentIndex())

    @Property("QVariantList", notify=stateChanged)
    def scaleRows(self):
        return [
            {
                "label": self.backend.combo_output_scale.itemText(index),
                "value": self.backend.combo_output_scale.itemData(index),
            }
            for index in range(self.backend.combo_output_scale.count())
        ]

    @Property(int, notify=stateChanged)
    def scaleIndex(self):
        return max(0, self.backend.combo_output_scale.currentIndex())

    @Property("QVariantList", constant=True)
    def formatRows(self):
        return ["png", "jpg", "tif"]

    @Property(int, notify=stateChanged)
    def formatIndex(self):
        return max(0, self.backend.combo_format.currentIndex())

    @Property("QVariantList", notify=stateChanged)
    def tileRows(self):
        return [self.backend.combo_tile.itemText(i) for i in range(self.backend.combo_tile.count())]

    @Property(int, notify=stateChanged)
    def tileIndex(self):
        return max(0, self.backend.combo_tile.currentIndex())

    @Property("QVariantList", notify=stateChanged)
    def haloRows(self):
        return [self.backend.combo_halo.itemText(i) for i in range(self.backend.combo_halo.count())]

    @Property(int, notify=stateChanged)
    def haloIndex(self):
        return max(0, self.backend.combo_halo.currentIndex())

    @Property("QVariantList", notify=stateChanged)
    def precisionRows(self):
        return [
            self.backend.combo_precision.itemText(i)
            for i in range(self.backend.combo_precision.count())
        ]

    @Property(int, notify=stateChanged)
    def precisionIndex(self):
        return max(0, self.backend.combo_precision.currentIndex())

    @Property(bool, notify=stateChanged)
    def safeMemory(self):
        return self.backend.check_safe_mem.isChecked()

    @Property(bool, notify=stateChanged)
    def safeMemoryEditable(self):
        return self.backend.check_safe_mem.isEnabled()

    @Property(bool, notify=stateChanged)
    def preserveMetadata(self):
        return self.backend.check_meta.isChecked()

    @Property(int, notify=stateChanged)
    def jpegQuality(self):
        return self.backend.spin_jpg.value()

    @Property(str, notify=stateChanged)
    def outputDirectory(self):
        return self.backend.output_dir

    @Property(str, notify=stateChanged)
    def predictedSize(self):
        return self.backend.predicted_size_label.text().removeprefix("Predicted Output: ")

    @Property(str, notify=stateChanged)
    def estimateText(self):
        return self.backend.estimate_label.text()

    @Property(str, notify=stateChanged)
    def quickEstimate(self):
        return getattr(self, "_quick_estimate", "")

    @Property(str, notify=stateChanged)
    def bestEstimate(self):
        return getattr(self, "_best_estimate", "")

    @Property(str, notify=stateChanged)
    def denoiseEstimate(self):
        return getattr(self, "_denoise_estimate", "")

    @Property(str, notify=stateChanged)
    def hardwareText(self):
        return self.backend.hardware_label.text()

    @Property(str, notify=stateChanged)
    def memoryText(self):
        return self.backend.memory_label.text()

    @Property(str, notify=stateChanged)
    def warningText(self):
        return self.backend.preflight_warning.text()

    @Property(str, notify=stateChanged)
    def presetMessage(self):
        return self._preset_message

    @Property(float, notify=stateChanged)
    def progress(self):
        return max(0.0, min(1.0, self.backend.progress_bar.value() / 100.0))

    @Property(str, notify=stateChanged)
    def progressText(self):
        return self.backend.progress_label.text()

    @Property(str, notify=stateChanged)
    def liveResourceText(self):
        return self.backend.live_resource_label.text()

    @Property(bool, notify=stateChanged)
    def canStart(self):
        return self.backend.btn_upscale.isEnabled()

    @Property(bool, notify=stateChanged)
    def canCancel(self):
        return self.backend.btn_cancel.isEnabled()

    @Property(bool, notify=stateChanged)
    def canOpenResult(self):
        return self.backend.btn_open.isEnabled()

    @Property(float, notify=stateChanged)
    def pressurePercent(self):
        return float(self._memory_snapshot.get("system_memory_pressure_percent", 0.0))

    @Property(str, notify=stateChanged)
    def pressureLabel(self):
        level = str(self._memory_snapshot.get("system_memory_pressure_level", "unknown"))
        return {
            "low": "Low pressure",
            "moderate": "Moderate pressure",
            "high": "High pressure",
        }.get(level, "Pressure unavailable")

    @Property(str, notify=stateChanged)
    def pressureColor(self):
        level = str(self._memory_snapshot.get("system_memory_pressure_level", "unknown"))
        return {"low": "#48d597", "moderate": "#f5b942", "high": "#ff667a"}.get(level, "#7f8da3")

    @Property(str, notify=stateChanged)
    def mpsMemoryText(self):
        driver = int(self._memory_snapshot.get("mps_driver_allocated_memory", 0))
        tensors = int(self._memory_snapshot.get("mps_tensor_allocated_memory", 0))
        recommended = int(self._memory_snapshot.get("mps_recommended_max_memory", 0))
        compressed = int(self._memory_snapshot.get("system_compressed_memory", 0))
        swap = int(self._memory_snapshot.get("system_swap_used", 0))
        parts = []
        if recommended:
            parts.append(
                f"Metal driver {format_bytes(driver)} / {format_bytes(recommended)} · "
                f"tensors {format_bytes(tensors)}"
            )
        if compressed or swap:
            parts.append(f"Compressed {format_bytes(compressed)} · swap {format_bytes(swap)}")
        return "\n".join(parts)

    @Slot()
    def chooseImage(self):
        self.backend.choose_image()
        if self.backend.image_path:
            self._load_preview(self.backend.image_path)

    @Slot(str)
    def setImageFromUrl(self, value):
        path = QUrl(value).toLocalFile() if value.startswith("file:") else value
        if not path:
            return
        self.backend.set_image(path)
        if self.backend.image_path:
            self._load_preview(self.backend.image_path)

    def _load_preview(self, path: str):
        self._preview_error = ""
        self._show_progressive = False
        if is_raw_input(path):
            self.preview_provider.clear()
            self.backend.worker.send_request(PreviewRequest(image_path=path))
        elif not self.preview_provider.set_source_path(path):
            self._preview_error = (
                "The preview could not be decoded, but the worker may still load it."
            )
        self._bump_preview()
        self._update_preset_estimates()
        self.stateChanged.emit()

    def _update_preset_estimates(self):
        from localsr.core.presets import PresetMode, resolve_settings_for_model, select_model_for_preset
        from localsr.core.estimator import format_duration_range
        from localsr.core.model_catalog import MODEL_CATALOG, ModelPurpose
        
        for mode, attr in [(PresetMode.QUICK, "_quick_estimate"), (PresetMode.BEST, "_best_estimate"), (PresetMode.DENOISE, "_denoise_estimate")]:
            try:
                scale = 1 if mode == PresetMode.DENOISE else 4
                purpose = ModelPurpose.DENOISE if mode == PresetMode.DENOISE else ModelPurpose.PHOTO
                model = select_model_for_preset(MODEL_CATALOG, mode, output_scale=scale, purpose=purpose, installed_model_ids=self._installed_ids())
                if not model:
                    continue
                
                decision = resolve_settings_for_model(
                    model=model,
                    mode=mode,
                    devices=self.backend.capability_report.get("devices", []),
                    image_width=self.backend.image_w,
                    image_height=self.backend.image_h,
                    output_scale=model.native_scale,
                    available_system_memory=int(self.backend.capability_report.get("system_ram_available", 0)) or None,
                    available_disk=None,
                    model_half_supported=bool(self.backend.current_model_info.get("half_supported", False)),
                    parameter_count=int(self.backend.current_model_info.get("parameter_count", 0)),
                    model_file_size=int(self.backend.current_model_info.get("model_file_size", model.size_bytes)),
                    installed_model_ids=self._installed_ids(),
                )
                est_str = format_duration_range(decision.estimate.seconds_low, decision.estimate.seconds_high)
                setattr(self, attr, f"({est_str})")
            except Exception:
                setattr(self, attr, "")

    @Slot(int)
    def setModelIndex(self, index):
        if index < 0 or index >= self.backend.combo_model.count():
            return
        if self.backend.combo_model.itemData(index) == CUSTOM_MODEL_ID:
            self.backend.choose_model()
        else:
            self.backend.combo_model.setCurrentIndex(index)
        self._pending_preset = None
        self.stateChanged.emit()

    @Slot()
    def downloadModel(self):
        self.backend.start_model_download()
        self.stateChanged.emit()

    @Slot(int)
    def setDeviceIndex(self, index):
        if 0 <= index < self.backend.combo_device.count():
            self.backend.combo_device.setCurrentIndex(index)

    @Slot(int)
    def setScaleIndex(self, index):
        if 0 <= index < self.backend.combo_output_scale.count():
            self.backend.combo_output_scale.setCurrentIndex(index)

    @Slot(int)
    def setFormatIndex(self, index):
        if 0 <= index < self.backend.combo_format.count():
            self.backend.combo_format.setCurrentIndex(index)
            self.backend.update_estimate()

    @Slot(int)
    def setTileIndex(self, index):
        if 0 <= index < self.backend.combo_tile.count():
            self.backend.combo_tile.setCurrentIndex(index)

    @Slot(int)
    def setHaloIndex(self, index):
        if 0 <= index < self.backend.combo_halo.count():
            self.backend.combo_halo.setCurrentIndex(index)

    @Slot(int)
    def setPrecisionIndex(self, index):
        if 0 <= index < self.backend.combo_precision.count():
            self.backend.combo_precision.setCurrentIndex(index)
            self.backend.update_estimate()

    @Slot(bool)
    def setSafeMemory(self, enabled):
        if self.backend.check_safe_mem.isEnabled():
            self.backend.check_safe_mem.setChecked(enabled)
            self.backend.update_estimate()

    @Slot(bool)
    def setPreserveMetadata(self, enabled):
        self.backend.check_meta.setChecked(enabled)

    @Slot(int)
    def setJpegQuality(self, value):
        self.backend.spin_jpg.setValue(max(70, min(100, value)))

    @Slot()
    def chooseOutputDirectory(self):
        self.backend.choose_output_dir()

    @Slot(str)
    def applyPreset(self, name):
        if not self.backend.image_path:
            self._preset_message = "Choose an image before applying an automatic preset."
            self.stateChanged.emit()
            return
        try:
            mode = PresetMode(name)
            scale = 1 if mode == PresetMode.DENOISE else 4
            purpose = ModelPurpose.DENOISE if mode == PresetMode.DENOISE else ModelPurpose.PHOTO
            ranked = rank_models_for_preset(
                MODEL_CATALOG,
                mode,
                purpose=purpose,
                output_scale=scale,
                installed_model_ids=self._installed_ids(),
            )
            if not ranked:
                raise NoCompatibleModelError("No compatible catalog model is available.")
            model = ranked[0]
            index = self.backend.combo_model.findData(model.model_id)
            if index < 0:
                raise NoCompatibleModelError("The selected model is missing from the interface.")
            self._pending_preset = mode
            if self.backend.combo_model.currentIndex() == index:
                if self.backend.model_store.is_installed(model):
                    if not self.backend.current_model_info:
                        self.backend.inspect_selected_model()
                    else:
                        from PySide6.QtCore import QTimer
                        QTimer.singleShot(0, self._apply_pending_preset)
            else:
                self.backend.combo_model.setCurrentIndex(index)
            self._preset_message = f"{model.name} selected. " + (
                "Downloading and verifying it now…"
                if not self.backend.model_store.is_installed(model)
                else "Inspecting it and choosing hardware settings…"
            )
            if not self.backend.model_store.is_installed(model):
                self.backend.start_model_download()
        except (NoCompatibleModelError, ValueError) as error:
            self._pending_preset = None
            self._preset_message = str(error)
        self.stateChanged.emit()

    def _apply_pending_preset(self):
        if self._pending_preset is None or not self.backend.current_model_info:
            return
        model = self.backend.selected_catalog_model()
        if model is None:
            return
        output_parent = Path(self.backend.output_dir).expanduser()
        while not output_parent.exists() and output_parent != output_parent.parent:
            output_parent = output_parent.parent
        try:
            disk = shutil.disk_usage(output_parent).free
        except OSError:
            disk = None
        try:
            decision = resolve_settings_for_model(
                model=model,
                mode=self._pending_preset,
                devices=self.backend.capability_report.get("devices", []),
                image_width=self.backend.image_w,
                image_height=self.backend.image_h,
                output_scale=model.native_scale,
                available_system_memory=int(
                    self.backend.capability_report.get("system_ram_available", 0)
                )
                or None,
                available_disk=disk,
                model_half_supported=bool(
                    self.backend.current_model_info.get("half_supported", False)
                ),
                parameter_count=int(self.backend.current_model_info.get("parameter_count", 0)),
                model_file_size=int(
                    self.backend.current_model_info.get("model_file_size", model.size_bytes)
                ),
                installed_model_ids=self._installed_ids(),
            )
            device_index = self.backend.combo_device.findData(
                next(
                    (
                        item
                        for item in self.backend.capability_report.get("devices", [])
                        if item.get("id") == decision.device_id
                    ),
                    {},
                )
            )
            if device_index < 0:
                device_index = next(
                    (
                        index
                        for index in range(self.backend.combo_device.count())
                        if self.backend.combo_device.itemData(index).get("id") == decision.device_id
                    ),
                    0,
                )
            self.backend.combo_device.setCurrentIndex(device_index)
            tile_index = self.backend.combo_tile.findText(str(decision.tile_size))
            if tile_index >= 0:
                self.backend.combo_tile.setCurrentIndex(tile_index)
            halo_index = self.backend.combo_halo.findText(str(decision.halo))
            if halo_index >= 0:
                self.backend.combo_halo.setCurrentIndex(halo_index)
            precision_index = self.backend.combo_precision.findText(decision.precision)
            if precision_index >= 0:
                self.backend.combo_precision.setCurrentIndex(precision_index)
            if self.backend.check_safe_mem.isEnabled():
                self.backend.check_safe_mem.setChecked(decision.safe_memory)
            scale_index = self.backend.combo_output_scale.findData(model.native_scale)
            if scale_index >= 0:
                self.backend.combo_output_scale.setCurrentIndex(scale_index)
            self.backend.update_estimate()
            mode_name = "Quick" if self._pending_preset == PresetMode.QUICK else "Best Quality"
            self._preset_message = (
                f"{mode_name} prepared: {model.name} · {decision.device_id} · "
                f"{decision.precision.upper()} · {decision.tile_size}px tiles."
            )
            self.startUpscale()
            self._pending_preset = None
        except ValueError as error:
            self._preset_message = f"Automatic settings need attention: {error}"
        self.stateChanged.emit()

    @Slot()
    def startUpscale(self):
        self.backend.start_upscale()
        self.stateChanged.emit()

    @Slot()
    def cancelUpscale(self):
        self.backend.cancel_job()

    @Slot()
    def openResult(self):
        self.backend.open_result()

    @Slot()
    def revealResult(self):
        self.backend.reveal_result()

    @Slot()
    def refreshHardware(self):
        self.backend.refresh_hardware()

    def _refresh_idle_snapshot(self):
        if self.backend.current_job_id is None:
            self.backend.worker.send_request(CapabilitiesRequest())

    @Slot()
    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        self._poll_timer.stop()
        self._hardware_timer.stop()
        self.backend.close()

    def _on_preview_ready(self, data):
        if data.get("image_path") != self.backend.image_path:
            return
        if self.preview_provider.set_source_base64(data.get("jpeg_base64", "")):
            self._preview_error = ""
            self._bump_preview()

    def _on_preview_failed(self, data):
        if data.get("image_path") == self.backend.image_path:
            self._preview_error = f"RAW preview unavailable: {data.get('error_message', '')}"
            self.stateChanged.emit()

    def _on_tile_update(self, data):
        image_width = int(data.get("image_width", 0))
        image_height = int(data.get("image_height", 0))
        phase = data.get("phase")
        if phase == "reset":
            self.preview_provider.reset_progressive(image_width, image_height)
            self._active_tile = (0.0, 0.0, 0.0, 0.0)
            self._bump_preview(progressive=True)
            return
        if image_width <= 0 or image_height <= 0:
            return
        self._active_tile = (
            int(data.get("output_x", 0)) / image_width,
            int(data.get("output_y", 0)) / image_height,
            int(data.get("output_width", 0)) / image_width,
            int(data.get("output_height", 0)) / image_height,
        )
        if phase == "started" and not getattr(self, "_show_progressive", False):
            self.preview_provider.reset_progressive(image_width, image_height)
            self._bump_preview(progressive=True)
        elif phase == "completed" and self.preview_provider.apply_tile(
            jpeg_base64=data.get("jpeg_base64", ""),
            output_x=int(data.get("output_x", 0)),
            output_y=int(data.get("output_y", 0)),
            output_width=int(data.get("output_width", 0)),
            output_height=int(data.get("output_height", 0)),
            image_width=image_width,
            image_height=image_height,
        ):
            self._bump_preview(progressive=True)
        self.stateChanged.emit()

    def _on_progress(self, data):
        self._memory_snapshot.update(data)
        self.stateChanged.emit()

    def _on_capabilities(self, data):
        self._memory_snapshot.update(data)
        self.stateChanged.emit()

    def _on_model_info(self, _data):
        if self._pending_preset is not None:
            QTimer.singleShot(0, self._apply_pending_preset)
        self.stateChanged.emit()

    def _on_job_started(self, _job_id):
        self._show_progressive = False
        self._active_tile = (0.0, 0.0, 0.0, 0.0)
        self.stateChanged.emit()

    def _on_job_completed(self, _job_id, _result):
        self._active_tile = (0.0, 0.0, 0.0, 0.0)
        self.stateChanged.emit()

    def _on_job_stopped(self, _job_id):
        self._active_tile = (0.0, 0.0, 0.0, 0.0)
        self.stateChanged.emit()

    def _on_job_failed(self, _job_id, _message):
        self._on_job_stopped(_job_id)


def create_controller(start_worker: bool = True, settings: QSettings | None = None):
    backend = MainWindow(start_worker=start_worker, settings=settings)
    provider = PreviewImageProvider()
    controller = LocalSRController(backend, provider, settings=settings)
    return controller, provider
