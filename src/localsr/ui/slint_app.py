"""Slint desktop frontend for LocalSR.

The frontend keeps all Torch/Spandrel work in the existing worker process.  It
owns presentation state only: queue management, model downloads, preflight
estimates, settings, and the JSON-line protocol bridge.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import slint

from localsr.core.estimator import (
    ResourceEstimate,
    estimate_resources,
    format_bytes,
    format_duration,
    format_duration_range,
)
from localsr.core.image_formats import SUPPORTED_INPUT_EXTENSIONS, probe_image_size
from localsr.core.model_catalog import (
    MODEL_CATALOG,
    CatalogModel,
    ModelDownloadCancelled,
    ModelPurpose,
    ModelStore,
    default_model_directory,
    download_model,
)
from localsr.core.presets import PresetMode, rank_models_for_preset, resolve_settings_for_model
from localsr.protocol.messages import (
    CancelRequest,
    CapabilitiesRequest,
    InspectRequest,
    JobRequest,
    PreviewRequest,
)
from localsr.ui.native_dialog import request_dialog
from localsr.ui.slint_preview import SlintPreviewBuffer
from localsr.ui.slint_worker import SlintWorkerClient

CUSTOM_MODEL_ID = "__custom__"
FORMAT_VALUES = ("png", "jpg", "tif")
TASK_LABELS = ("Upscale", "Denoise", "Upscale Video")
VIDEO_TASK_INDEX = 2
VIDEO_ENABLED = False


@dataclass(frozen=True)
class ImageItem:
    path: str
    width: int
    height: int
    thumbnail_path: str = ""


class SettingsStore:
    """Small atomic JSON settings store independent of any GUI framework."""

    def __init__(self, path: str | Path | None = None):
        self.path = (
            Path(path) if path is not None else default_model_directory().parent / "settings.json"
        )
        self.values: dict[str, object] = {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.values = loaded
        except (OSError, json.JSONDecodeError):
            pass

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value) -> None:
        self.values[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.values, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class SlintApplication:
    def __init__(
        self,
        *,
        start_worker: bool = True,
        settings_path: str | Path | None = None,
        model_root: str | Path | None = None,
    ):
        ui_path = Path(__file__).with_name("slint") / "main.slint"
        self.module = slint.load_file(str(ui_path), style="fluent-dark")
        self.ui = self.module.MainWindow()
        self._list_models: dict[str, slint.ListModel] = {}
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.worker = SlintWorkerClient(self.events)
        self.preview = SlintPreviewBuffer()
        self.settings = SettingsStore(settings_path)
        self.model_store = ModelStore(model_root)

        self.images: list[ImageItem] = []
        self.selected_image_index = -1
        self.filtered_models: list[CatalogModel | None] = []
        # Task selection is intentionally explicit on every launch. This keeps
        # Quick/Best contextual and prevents an old preset being started by mistake.
        self.task_selected = False
        self.task_index = -1
        self.profile_label = "—"
        self.model_id = str(self.settings.get("selected_model_id", ""))
        self.custom_model_path = str(self.settings.get("custom_model_path", ""))
        self.model_path = ""
        self.current_model_info: dict = {}
        self.model_scale = 1
        self.scale_values: list[int] = [1]
        self.output_scale = int(self.settings.get("output_scale", 4))

        desktop = Path.home() / "Desktop"
        default_output = desktop if desktop.is_dir() else Path.home()
        self.output_directory = str(self.settings.get("output_directory", str(default_output)))
        self.format_index = max(0, min(2, int(self.settings.get("format_index", 0))))
        self.preserve_metadata = bool(self.settings.get("preserve_metadata", True))
        self.jpeg_quality = max(70, min(100, int(self.settings.get("jpeg_quality", 98))))

        self.capability_report = self._fallback_capabilities()
        self.devices: list[dict] = list(self.capability_report["devices"])
        self.device_id = str(self.settings.get("device_id", "cpu"))
        self.tile_values = [64, 128, 192, 256]
        self.tile_size = int(self.settings.get("tile_size", 256))
        self.halo_values = [8, 16, 32, 64]
        self.halo = int(self.settings.get("halo", 32))
        self.precision_values = ["fp32"]
        self.precision = str(self.settings.get("precision", "fp32"))
        self.safe_memory = bool(self.settings.get("safe_memory", True))
        self.safe_memory_enabled = True
        self.current_estimate: ResourceEstimate | None = None
        self.runtime_warning = ""
        self.memory_snapshot: dict = {}

        self.current_job_id = ""
        self.last_output_path = ""
        self.job_started_at: float | None = None
        self.job_effective_megapixels = 0.0
        self.batch_paths: list[str] = []
        self.batch_total = 0
        self.batch_current = 0
        self.cancel_batch = False
        self.detected_face_boxes: list[dict] = []
        self.deflicker_enabled = False

        self.download_thread: threading.Thread | None = None
        self.download_cancel: threading.Event | None = None
        self.pending_preset: PresetMode | None = None
        self.pending_preset_label = ""
        self.pending_auto_start = False
        self._next_hardware_refresh = time.monotonic() + 5.0
        self._shutdown = False

        self._bind_callbacks()
        self._initialize_ui()
        if start_worker:
            self.worker.start()

    @staticmethod
    def _fallback_capabilities() -> dict:
        return {
            "system_ram_total": 0,
            "system_ram_available": 0,
            "system_memory_pressure_percent": 0.0,
            "system_memory_pressure_level": "unknown",
            "system_compressed_memory": 0,
            "system_swap_used": 0,
            "devices": [
                {
                    "id": "cpu",
                    "type": "cpu",
                    "name": "CPU (detecting hardware)",
                    "total_memory": 0,
                    "free_memory": 0,
                    "supports_fp16": False,
                    "is_integrated": False,
                    "recommended_tile_sizes": [64, 128, 192, 256],
                }
            ],
        }

    def _bind_callbacks(self) -> None:
        self.ui.poll_backend = self.poll_events
        self.ui.choose_images = self.choose_images
        self.ui.choose_folder = self.choose_folder
        self.ui.clear_queue = self.clear_queue
        self.ui.remove_queue_item = self.remove_queue_item
        self.ui.select_queue_item = self.select_queue_item
        self.ui.task_changed = self.set_task
        self.ui.model_changed = self.set_model_index
        self.ui.quick_setup = lambda: self.apply_automatic_setup(best=False)
        self.ui.best_setup = lambda: self.apply_automatic_setup(best=True)
        self.ui.model_action = self.handle_model_action
        self.ui.scale_changed = self.set_scale_index
        self.ui.format_changed = self.set_format_index
        self.ui.choose_output_directory = self.choose_output_directory
        self.ui.preserve_metadata_changed = self.set_preserve_metadata
        self.ui.device_changed = self.set_device_index
        self.ui.refresh_hardware = self.refresh_hardware
        self.ui.tile_changed = self.set_tile_index
        self.ui.halo_changed = self.set_halo_index
        self.ui.precision_changed = self.set_precision_index
        self.ui.safe_memory_changed = self.set_safe_memory
        self.ui.jpeg_quality_changed = self.set_jpeg_quality
        self.ui.start_job = self.start_jobs
        self.ui.cancel_job = self.cancel_job
        self.ui.open_result = self.open_result
        self.ui.reveal_result = self.reveal_result
        self.ui.detect_faces = self.detect_faces
        self.ui.clear_faces = self.clear_faces

    def _initialize_ui(self) -> None:
        self.ui.batch_mode = bool(self.settings.get("batch_mode", False))
        self.ui.video_enabled = VIDEO_ENABLED
        self.ui.task_selected = False
        self.ui.task_index = self.task_index
        self._set_list_model("format_options", ["PNG", "JPEG", "TIFF"])
        self.ui.format_index = self.format_index
        self.ui.output_directory = self.output_directory
        self.ui.preserve_metadata = self.preserve_metadata
        self.ui.jpeg_quality = self.jpeg_quality
        self._set_list_model("tile_options", [str(value) for value in self.tile_values])
        self._set_list_model("halo_options", [str(value) for value in self.halo_values])
        self._set_list_model("precision_options", self.precision_values)
        self.ui.status_title = "Starting"
        self.ui.status_detail = "Launching the isolated inference worker."
        self.ui.source_label = "Original"
        self.ui.result_label = "Preview"
        self.ui.live_result_ready = False
        self.ui.result_ready = False
        self.ui.preview_aspect = 1.0
        self.ui.preview_zoom = 1.0
        self.ui.preview_pan_x = 0.0
        self.ui.preview_pan_y = 0.0
        self._rebuild_model_options(preferred_id=self.model_id)
        self._sync_queue()
        self._apply_hardware_constraints()
        self._update_estimate()

    def _set_list_model(self, property_name: str, values: Iterable) -> None:
        model = slint.ListModel(list(values))
        self._list_models[property_name] = model
        setattr(self.ui, property_name, model)

    def _queue_entry(self, item: ImageItem, index: int):
        megapixels = item.width * item.height / 1_000_000
        thumbnail = (
            slint.Image.load_from_path(item.thumbnail_path)
            if item.thumbnail_path and Path(item.thumbnail_path).is_file()
            else slint.Image()
        )
        return self.module.QueueEntry(
            name=Path(item.path).name,
            detail=f"{item.width} × {item.height} · {megapixels:.1f} MP",
            selected=index == self.selected_image_index,
            thumbnail=thumbnail,
            has_thumbnail=bool(item.thumbnail_path),
        )

    def _sync_queue(self) -> None:
        self._set_list_model(
            "queue_items",
            [self._queue_entry(item, index) for index, item in enumerate(self.images)],
        )
        self._update_action_state()

    def choose_images(self) -> None:
        if self.current_job_id:
            return
        initial = (
            str(Path(self.images[self.selected_image_index].path).parent) if self.images else ""
        )
        paths = request_dialog("images", initial)
        if paths:
            self.add_images(paths, replace=not self.ui.batch_mode)

    def choose_folder(self) -> None:
        if self.current_job_id:
            return
        selected = request_dialog("folder", str(Path.home()))
        if not selected:
            return
        folder = Path(selected[0])
        paths = sorted(
            str(path)
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS
        )
        if not paths:
            self._show_status("No supported images", f"{folder} contains no supported image files.")
            return
        self.add_images(paths, replace=False)

    def add_images(self, paths: Iterable[str], *, replace: bool = False) -> None:
        if self.current_job_id:
            return
        accepted: list[ImageItem] = []
        rejected: list[str] = []
        existing = {item.path for item in self.images}
        for raw_path in paths:
            path = str(Path(raw_path).expanduser().resolve())
            if path in existing:
                continue
            try:
                width, height = probe_image_size(path)
            except (OSError, RuntimeError, ValueError):
                rejected.append(Path(path).name)
                continue
            thumbnail_path = self.preview.media_thumbnail(path)
            accepted.append(
                ImageItem(
                    path=path,
                    width=width,
                    height=height,
                    thumbnail_path=str(thumbnail_path) if thumbnail_path is not None else "",
                )
            )
            existing.add(path)
            if replace:
                break

        if replace:
            self.images = accepted[:1]
            self.selected_image_index = 0 if self.images else -1
        elif accepted:
            first_new = len(self.images)
            self.images.extend(accepted)
            if self.selected_image_index < 0:
                self.selected_image_index = first_new

        if accepted:
            self._load_selected_preview()
            # On compact windows, importing from the Media page should reveal
            # the selected document instead of leaving the user in the queue.
            self.ui.compact_page = 1
            count = len(accepted)
            self._show_status("Image ready", f"Added {count} image{'s' if count != 1 else ''}.")
        if rejected:
            self.runtime_warning = "Unreadable files were skipped: " + ", ".join(rejected[:4])
        self._sync_queue()
        self._update_estimate()

    def clear_queue(self) -> None:
        if self.current_job_id:
            return
        self.images.clear()
        self.selected_image_index = -1
        self.ui.image_ready = False
        self.ui.live_result_ready = False
        self.ui.result_ready = False
        self.ui.source_image = slint.Image()
        self.ui.result_image = slint.Image()
        self.ui.preview_aspect = 1.0
        self.ui.preview_zoom = 1.0
        self.ui.preview_pan_x = 0.0
        self.ui.preview_pan_y = 0.0
        self._sync_queue()
        self._update_estimate()
        self._show_status("Ready", "Add an image and configure the settings.")

    def remove_queue_item(self, index: int) -> None:
        index = int(index)
        if self.current_job_id or index < 0 or index >= len(self.images):
            return
        del self.images[index]
        if not self.images:
            self.clear_queue()
            return
        self.selected_image_index = min(self.selected_image_index, len(self.images) - 1)
        if index <= self.selected_image_index:
            self.selected_image_index = max(0, self.selected_image_index - 1)
        self._sync_queue()
        self._load_selected_preview()
        self._update_estimate()

    def select_queue_item(self, index: int) -> None:
        index = int(index)
        if self.current_job_id or index < 0 or index >= len(self.images):
            return
        self.selected_image_index = index
        self._sync_queue()
        self._load_selected_preview()
        self._update_estimate()

    def _selected_image(self) -> ImageItem | None:
        if 0 <= self.selected_image_index < len(self.images):
            return self.images[self.selected_image_index]
        return None

    def _load_selected_preview(self) -> None:
        image = self._selected_image()
        if image is None:
            return
        self.ui.image_ready = True
        self.ui.live_result_ready = False
        self.ui.result_ready = False
        self.ui.tile_active = False
        self.ui.preview_zoom = 1.0
        self.ui.preview_pan_x = 0.0
        self.ui.preview_pan_y = 0.0
        self.ui.compare_position = 0.5
        self.ui.result_image = slint.Image()
        self.ui.source_label = "Original"
        self.worker.send_request(PreviewRequest(image_path=image.path, max_dimension=1600))
        self._sync_inspector()

    def set_task(self, index: int) -> None:
        index = int(index)
        if self.current_job_id or index not in {0, 1, 2}:
            return
        self.task_selected = True
        self.task_index = index
        self.ui.task_selected = True
        self.ui.task_index = index
        self.pending_preset = None
        self.pending_auto_start = False
        self._rebuild_model_options()
        self.profile_label = "Choose Quick, Best, or manual"
        self._update_estimate()

    def _model_is_compatible(self, model: CatalogModel) -> bool:
        if self.task_index == 0:
            return model.native_scale > 1 and any(
                purpose in model.purposes for purpose in (ModelPurpose.PHOTO, ModelPurpose.GENERAL)
            )
        if self.task_index == 1:
            return model.native_scale == 1 and ModelPurpose.DENOISE in model.purposes
        return model.native_scale > 1 and any(
            purpose in model.purposes for purpose in (ModelPurpose.PHOTO, ModelPurpose.GENERAL)
        )

    def _rebuild_model_options(self, preferred_id: str = "") -> None:
        if not self.task_selected:
            self.filtered_models = []
            self._set_list_model("model_options", ["Choose a task first"])
            self.ui.model_index = 0
            self.ui.model_description = "Choose Upscale, Denoise, or both to see compatible models."
            self.ui.model_status = "No task selected."
            self.ui.model_action_visible = False
            self.model_path = ""
            self.current_model_info = {}
            self._sync_inspector()
            return
        compatible = [model for model in MODEL_CATALOG if self._model_is_compatible(model)]
        self.filtered_models = [*compatible, None]
        self._refresh_model_option_labels()
        desired = preferred_id or self.model_id
        if desired == CUSTOM_MODEL_ID and not (
            self.custom_model_path and Path(self.custom_model_path).is_file()
        ):
            # A missing custom checkpoint must not leave Upscale presenting a
            # meaningless 1× scale. Fall back to the first compatible model.
            desired = ""
        index = next(
            (
                candidate
                for candidate, model in enumerate(self.filtered_models)
                if (model.model_id if model is not None else CUSTOM_MODEL_ID) == desired
            ),
            0,
        )
        self.set_model_index(index)

    def _refresh_model_option_labels(self) -> None:
        labels = []
        for model in self.filtered_models:
            if model is None:
                labels.append("Use My Own Checkpoint…")
            elif self.model_store.is_installed(model):
                labels.append(f"{model.name} · Installed")
            else:
                labels.append(f"{model.name} · Download {model.size_megabytes:.0f} MB")
        self._set_list_model("model_options", labels)

    def _selected_catalog_model(self) -> CatalogModel | None:
        index = int(self.ui.model_index)
        if 0 <= index < len(self.filtered_models):
            return self.filtered_models[index]
        return None

    def set_model_index(self, index: int) -> None:
        index = int(index)
        if self.current_job_id or index < 0 or index >= len(self.filtered_models):
            return
        self.ui.model_index = index
        model = self.filtered_models[index]
        if self.pending_preset is None:
            self.profile_label = "Manual"
        self.model_id = model.model_id if model is not None else CUSTOM_MODEL_ID
        self.model_path = ""
        self.current_model_info = {}
        self.model_scale = model.native_scale if model is not None else 1
        self._configure_scales()

        if model is None:
            self.ui.model_description = (
                "Load a local Spandrel-compatible checkpoint. Only use trusted .pth or .pt files."
            )
            if self.custom_model_path and Path(self.custom_model_path).is_file():
                self.model_path = self.custom_model_path
                self.ui.model_status = f"Custom checkpoint: {Path(self.model_path).name}"
                self.ui.model_action_text = "Choose Another Checkpoint…"
                self._inspect_model()
            else:
                self.ui.model_status = "No custom checkpoint selected."
                self.ui.model_action_text = "Choose Checkpoint…"
            self.ui.model_action_visible = True
            self.ui.model_action_enabled = True
        else:
            self.ui.model_description = model.description
            path = self.model_store.path_for(model)
            if self.model_store.is_installed(model):
                self.model_path = str(path)
                self.ui.model_status = (
                    f"Installed · {model.architecture} · {model.license_name} · "
                    f"{model.size_megabytes:.0f} MB"
                )
                self.ui.model_action_text = "Model Installed"
                self.ui.model_action_visible = True
                self.ui.model_action_enabled = False
                self._inspect_model()
            else:
                self.ui.model_status = (
                    f"Not installed · {model.license_name} · optional verified download"
                )
                self.ui.model_action_text = f"Download {model.size_megabytes:.0f} MB"
                self.ui.model_action_visible = True
                self.ui.model_action_enabled = True
        self._sync_inspector()
        self._update_estimate()

    def _inspect_model(self) -> None:
        if self.model_path:
            self.ui.model_status = f"Inspecting {Path(self.model_path).name}…"
            self.worker.send_request(InspectRequest(model_path=self.model_path))

    def handle_model_action(self) -> None:
        if self.current_job_id:
            return
        if self.download_thread is not None and self.download_thread.is_alive():
            if self.download_cancel is not None:
                self.download_cancel.set()
                self.ui.model_status = "Cancelling download…"
                self.ui.model_action_enabled = False
            return
        model = self._selected_catalog_model()
        if model is None:
            selected = request_dialog("model", self.custom_model_path)
            if selected:
                candidate = Path(selected[0]).expanduser().resolve()
                if not candidate.is_file() or candidate.suffix.lower() not in {
                    ".pth",
                    ".pt",
                    ".safetensors",
                }:
                    self.runtime_warning = "Choose a .pth, .pt, or .safetensors checkpoint."
                    self._show_status("Unsupported checkpoint", self.runtime_warning)
                    return
                self.custom_model_path = str(candidate)
                self.model_path = str(candidate)
                self.ui.model_status = f"Custom checkpoint: {Path(self.model_path).name}"
                self.ui.model_action_text = "Choose Another Checkpoint…"
                self._inspect_model()
                self._update_estimate()
            return
        if not self.model_store.is_installed(model):
            self._start_model_download(model)

    def _start_model_download(self, model: CatalogModel) -> None:
        if self.download_thread is not None and self.download_thread.is_alive():
            return
        cancel_event = threading.Event()
        self.download_cancel = cancel_event
        self.ui.download_visible = True
        self.ui.download_progress = 0.0
        self.ui.model_action_text = "Cancel Download"
        self.ui.model_action_enabled = True
        self.ui.model_status = f"Downloading {model.name}; SHA-256 verification follows…"

        def progress(downloaded: int, total: int) -> None:
            self.events.put(
                (
                    "download_progress",
                    {"downloaded": downloaded, "total": total, "model_id": model.model_id},
                )
            )

        def run() -> None:
            try:
                path = download_model(
                    model,
                    self.model_store.path_for(model),
                    progress_callback=progress,
                    cancel_event=cancel_event,
                )
            except ModelDownloadCancelled:
                self.events.put(("download_cancelled", {"model_id": model.model_id}))
            except Exception as error:  # noqa: BLE001 - cross-thread user-facing error
                self.events.put(
                    ("download_failed", {"model_id": model.model_id, "message": str(error)})
                )
            else:
                self.events.put(
                    ("download_completed", {"model_id": model.model_id, "path": str(path)})
                )

        self.download_thread = threading.Thread(
            target=run,
            daemon=True,
            name="localsr-model-download",
        )
        self.download_thread.start()
        self._update_action_state()

    def apply_automatic_setup(self, *, best: bool) -> None:
        if not self.task_selected:
            self._show_status("Choose a task", "Select Upscale, Denoise, or Upscale Video first.")
            return
        if self.task_index == VIDEO_TASK_INDEX and not VIDEO_ENABLED:
            self._show_status(
                "Video coming soon",
                "Video upscaling is being prepared. Use Upscale on each frame for now.",
            )
            return
        image = self._selected_image()
        if image is None:
            self._show_status(
                "Image required", "Choose an image before applying automatic settings."
            )
            return
        purpose = ModelPurpose.PHOTO if self.task_index == 0 else ModelPurpose.DENOISE
        output_scale = 1 if self.task_index == 1 else 4
        if self.task_index == 1:
            mode = PresetMode.BEST_DENOISE if best else PresetMode.QUICK_DENOISE
        else:
            mode = PresetMode.BEST_UPSCALE if best else PresetMode.QUICK_UPSCALE
        ranked = rank_models_for_preset(
            MODEL_CATALOG,
            mode,
            purpose=purpose,
            output_scale=output_scale,
            installed_model_ids=self._installed_model_ids(),
        )
        compatible = [model for model in ranked if self._model_is_compatible(model)]
        if not compatible:
            self._show_status("No compatible model", "No catalog model can run this setup.")
            return
        chosen = compatible[0]
        index = next(
            (
                index
                for index, model in enumerate(self.filtered_models)
                if model is not None and model.model_id == chosen.model_id
            ),
            -1,
        )
        if index < 0:
            return
        self.pending_preset = mode
        self.pending_preset_label = "Best quality" if best else "Quick setup"
        self.pending_auto_start = True
        self.profile_label = "Best" if best else "Quick"
        self.set_model_index(index)
        if not self.model_store.is_installed(chosen):
            self._show_status(
                f"Preparing {self.profile_label}",
                f"Downloading {chosen.name}; processing will start automatically.",
            )
            self._start_model_download(chosen)
        elif self.current_model_info:
            self._apply_pending_preset()

    def _apply_pending_preset(self) -> None:
        if self.pending_preset is None or not self.current_model_info:
            return
        model = self._selected_catalog_model()
        image = self._selected_image()
        if model is None or image is None:
            return
        output_parent = self._existing_output_parent()
        try:
            disk = shutil.disk_usage(output_parent).free
        except OSError:
            disk = None
        try:
            decision = resolve_settings_for_model(
                model=model,
                mode=self.pending_preset,
                devices=self.devices,
                image_width=image.width,
                image_height=image.height,
                output_scale=1 if self.task_index == 1 else model.native_scale,
                available_system_memory=int(self.capability_report.get("system_ram_available", 0))
                or None,
                available_disk=disk,
                model_half_supported=bool(self.current_model_info.get("half_supported", False)),
                parameter_count=int(self.current_model_info.get("parameter_count", 0)),
                model_file_size=int(
                    self.current_model_info.get("model_file_size", model.size_bytes)
                ),
                installed_model_ids=self._installed_model_ids(),
            )
        except ValueError as error:
            self._show_status("Automatic setup needs attention", str(error))
            self.pending_preset = None
            self.pending_auto_start = False
            return
        self.device_id = decision.device_id
        self.tile_size = decision.tile_size
        self.halo = decision.halo
        self.precision = decision.precision
        self.safe_memory = decision.safe_memory
        self.output_scale = 1 if self.task_index == 1 else model.native_scale
        self._apply_hardware_constraints()
        self._configure_scales()
        preset_label = self.pending_preset_label
        auto_start = self.pending_auto_start
        self._show_status(
            f"{preset_label} prepared",
            f"{model.name} · {decision.device_id} · {decision.precision.upper()} · "
            f"{decision.tile_size}px tiles.",
        )
        self.pending_preset = None
        self.pending_auto_start = False
        self._update_estimate()
        if auto_start:
            if self.ui.can_start:
                self.start_jobs()
            else:
                self._show_status(
                    f"{preset_label} could not start",
                    "Review the warning in the inspector, then adjust the manual configuration.",
                )

    def _installed_model_ids(self) -> frozenset[str]:
        return frozenset(
            model.model_id for model in MODEL_CATALOG if self.model_store.is_installed(model)
        )

    def _configure_scales(self) -> None:
        if self.model_scale <= 1:
            self.scale_values = [1]
        else:
            self.scale_values = list(range(2, self.model_scale + 1))
        if self.output_scale not in self.scale_values:
            self.output_scale = self.scale_values[-1]
        self._set_list_model(
            "scale_options",
            [
                f"{value}×" + (" Native" if value == self.model_scale and value > 1 else "")
                for value in self.scale_values
            ],
        )
        self.ui.scale_index = self.scale_values.index(self.output_scale)

    def set_scale_index(self, index: int) -> None:
        index = int(index)
        if not self.current_job_id and 0 <= index < len(self.scale_values):
            self.output_scale = self.scale_values[index]
            self.ui.scale_index = index
            self.profile_label = "Manual"
            self._update_estimate()

    def set_format_index(self, index: int) -> None:
        index = int(index)
        if not self.current_job_id and 0 <= index < len(FORMAT_VALUES):
            self.format_index = index
            self.ui.format_index = index
            self._update_estimate()

    def choose_output_directory(self) -> None:
        if self.current_job_id:
            return
        selected = request_dialog("output", self.output_directory)
        if selected:
            self.output_directory = selected[0]
            self.ui.output_directory = selected[0]
            self._update_estimate()

    def set_preserve_metadata(self, enabled: bool) -> None:
        if self.current_job_id:
            return
        self.preserve_metadata = bool(enabled)
        self.ui.preserve_metadata = self.preserve_metadata

    def refresh_hardware(self) -> None:
        self.ui.pressure_text = "Refreshing…"
        if self.worker.send_request(CapabilitiesRequest()):
            self._next_hardware_refresh = time.monotonic() + 5.0

    def set_device_index(self, index: int) -> None:
        index = int(index)
        if not self.current_job_id and 0 <= index < len(self.devices):
            self.device_id = str(self.devices[index].get("id", "cpu"))
            self.ui.device_index = index
            self.profile_label = "Manual"
            self._apply_hardware_constraints()

    def _current_device(self) -> dict:
        return next(
            (device for device in self.devices if str(device.get("id")) == self.device_id),
            self.devices[0],
        )

    def _apply_hardware_constraints(self) -> None:
        device = self._current_device()
        self.device_id = str(device.get("id", "cpu"))
        self._set_list_model(
            "device_options",
            [str(item.get("name", item.get("id", "Device"))) for item in self.devices],
        )
        self.ui.device_index = self.devices.index(device)

        recommended = sorted(
            {max(4, int(value)) for value in device.get("recommended_tile_sizes", [64, 128])}
        )
        if not recommended:
            recommended = [64]
        self.tile_size = min(recommended, key=lambda value: abs(value - self.tile_size))
        self.tile_values = recommended
        self._set_list_model("tile_options", [str(value) for value in recommended])
        self.ui.tile_index = recommended.index(self.tile_size)

        valid_halos = [value for value in (8, 16, 32, 64) if value <= self.tile_size // 2]
        if not valid_halos:
            valid_halos = [max(1, self.tile_size // 4)]
        self.halo = min(valid_halos, key=lambda value: abs(value - self.halo))
        self.halo_values = valid_halos
        self._set_list_model("halo_options", [str(value) for value in valid_halos])
        self.ui.halo_index = valid_halos.index(self.halo)

        allow_fp16 = (
            bool(device.get("supports_fp16", False))
            and bool(self.current_model_info.get("half_supported", False))
            and str(device.get("type", "cpu")) != "cpu"
        )
        self.precision_values = ["fp32", "fp16"] if allow_fp16 else ["fp32"]
        if self.precision not in self.precision_values:
            self.precision = "fp32"
        self._set_list_model("precision_options", self.precision_values)
        self.ui.precision_index = self.precision_values.index(self.precision)

        free_memory = int(device.get("free_memory", 0))
        low_memory = 0 < free_memory < 3 * 1024**3
        if low_memory:
            self.safe_memory = True
        self.safe_memory_enabled = not low_memory
        self.ui.safe_memory = self.safe_memory
        self.ui.safe_memory_enabled = self.safe_memory_enabled
        self._update_hardware_display()
        self._update_estimate()

    def set_tile_index(self, index: int) -> None:
        index = int(index)
        if not self.current_job_id and 0 <= index < len(self.tile_values):
            self.tile_size = self.tile_values[index]
            self.profile_label = "Manual"
            self._apply_hardware_constraints()

    def set_halo_index(self, index: int) -> None:
        index = int(index)
        if not self.current_job_id and 0 <= index < len(self.halo_values):
            self.halo = self.halo_values[index]
            self.ui.halo_index = index
            self.profile_label = "Manual"
            self._update_estimate()

    def set_precision_index(self, index: int) -> None:
        index = int(index)
        if not self.current_job_id and 0 <= index < len(self.precision_values):
            self.precision = self.precision_values[index]
            self.ui.precision_index = index
            self.profile_label = "Manual"
            self._update_estimate()

    def set_safe_memory(self, enabled: bool) -> None:
        if not self.current_job_id and self.safe_memory_enabled:
            self.safe_memory = bool(enabled)
            self.ui.safe_memory = self.safe_memory
            self.profile_label = "Manual"
            self._update_hardware_display()
            self._update_estimate()

    def set_jpeg_quality(self, value: int) -> None:
        if self.current_job_id:
            return
        self.jpeg_quality = max(70, min(100, int(value)))
        self.ui.jpeg_quality = self.jpeg_quality

    def _existing_output_parent(self) -> Path:
        parent = Path(self.output_directory).expanduser()
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        return parent

    def _throughput_key(self, device_type: str) -> str:
        key = self.model_id or self.current_model_info.get("architecture", "custom")
        safe = "".join(
            character for character in str(key) if character.isalnum() or character in "-_"
        )
        return f"throughput/{safe}/{device_type}"

    def _update_estimate(self) -> None:
        image = self._selected_image()
        model = self._selected_catalog_model()
        if image is None or not self.model_path or not self.current_model_info:
            self.current_estimate = None
            self.ui.estimate_time = "—"
            self.ui.estimate_output = "—"
            self.ui.estimate_detail = "Choose an image and an installed model."
            self.ui.estimate_warning = self.runtime_warning
            self._update_hardware_display()
            self._update_action_state()
            return

        device = self._current_device()
        try:
            available_disk = shutil.disk_usage(self._existing_output_parent()).free
        except OSError:
            available_disk = None
        throughput = self.settings.get(self._throughput_key(str(device.get("type", "cpu"))))
        try:
            measured = float(throughput) if throughput is not None else None
        except (TypeError, ValueError):
            measured = None
        model_file_size = int(self.current_model_info.get("model_file_size", 0))
        if not model_file_size and model is not None:
            model_file_size = model.size_bytes

        self.current_estimate = estimate_resources(
            image_width=image.width,
            image_height=image.height,
            scale=self.output_scale,
            model_scale=self.model_scale,
            tile_size=self.tile_size,
            halo=self.halo,
            precision=self.precision,
            device_type=str(device.get("type", "cpu")),
            available_device_memory=int(device.get("free_memory", 0)) or None,
            available_system_memory=int(self.capability_report.get("system_ram_available", 0))
            or None,
            available_disk=available_disk,
            model_file_size=model_file_size,
            parameter_count=int(self.current_model_info.get("parameter_count", 0)),
            memory_factor=model.memory_factor if model is not None else 1.0,
            time_factor=model.time_factor if model is not None else 1.0,
            measured_seconds_per_megapixel=measured,
            device_memory_shared=bool(device.get("is_integrated", False)),
        )
        estimate = self.current_estimate
        self.ui.estimate_time = format_duration_range(
            estimate.seconds_low,
            estimate.seconds_high,
        )
        self.ui.estimate_output = format_bytes(estimate.output_bytes)
        calibration = "locally calibrated" if estimate.calibrated else "broad first-run range"
        self.ui.estimate_detail = (
            f"{estimate.tile_count} tiles · {calibration} · "
            f"{format_bytes(estimate.working_disk_bytes)} temporary disk"
        )
        warnings = ([self.runtime_warning] if self.runtime_warning else []) + list(
            estimate.warnings
        )
        self.ui.estimate_warning = "\n".join(warnings)
        self._update_hardware_display()
        self._update_action_state()

    def _update_hardware_display(self) -> None:
        device = self._current_device()
        device_type = str(device.get("type", "cpu"))
        device_name = str(device.get("name", device.get("id", "Device")))
        device_total = int(device.get("total_memory", 0))
        device_free = int(device.get("free_memory", 0))
        ram_total = int(self.capability_report.get("system_ram_total", 0))
        ram_available = int(self.capability_report.get("system_ram_available", 0))

        if device_type == "mps":
            totals = [value for value in (device_total, ram_total) if value > 0]
            frees = [value for value in (device_free, ram_available) if value > 0]
            total = min(totals) if totals else 0
            free = min(frees) if frees else 0
            memory_name = "unified memory"
            cap = 52 if self.safe_memory else 62
            limit = f"Hard Metal allocator cap: {cap}% of the recommended maximum."
        elif device_type in {"cuda", "rocm", "xpu"}:
            total = device_total
            free = device_free
            memory_name = "shared GPU memory" if device.get("is_integrated") else "VRAM"
            cap = 80 if self.safe_memory else 90
            limit = f"Hard GPU allocator cap: {cap}% of memory free at job start."
        else:
            total = ram_total
            free = ram_available
            memory_name = "RAM"
            limit = "CPU processing uses system memory and disk-backed output."

        current_used = max(0, total - free) if total else 0
        projected = current_used
        if self.current_estimate is not None:
            estimate_use = (
                self.current_estimate.device_memory_bytes
                if device_type in {"cuda", "rocm", "xpu"}
                else self.current_estimate.system_memory_bytes
            )
            projected = min(total, current_used + estimate_use) if total else estimate_use
        self.ui.memory_usage_ratio = projected / total if total else 0.0
        prefix = "≈ " if self.current_estimate is not None else ""
        self.ui.memory_usage_text = (
            f"{prefix}{format_bytes(projected)} / {format_bytes(total)}" if total else "Unavailable"
        )
        self.ui.hardware_summary = (
            f"{device_name} · {format_bytes(free)} {memory_name} currently available.\n{limit}"
        )
        self.ui.device_summary = device_name

        snapshot = {**self.capability_report, **self.memory_snapshot}
        level = str(snapshot.get("system_memory_pressure_level", "unknown"))
        percent = float(snapshot.get("system_memory_pressure_percent", 0.0))
        self.ui.pressure_text = (
            f"{level.title()} pressure · {percent:.0f}%"
            if level != "unknown"
            else "Pressure unavailable"
        )
        pressure_colors = {
            "low": "#48d597",
            "moderate": "#f5b942",
            "high": "#ff667a",
        }
        self.ui.pressure_color = slint.Color(pressure_colors.get(level, "#788391"))
        self._sync_inspector()

    def _sync_inspector(self) -> None:
        image = self._selected_image()
        if image is None:
            self.ui.selected_name = ""
            self.ui.selected_detail = "No image selected"
            self.ui.output_dimensions = "—"
            self.ui.input_summary = "No image selected"
        else:
            megapixels = image.width * image.height / 1_000_000
            self.ui.selected_name = Path(image.path).name
            format_name = Path(image.path).suffix.removeprefix(".").upper() or "IMAGE"
            self.ui.selected_detail = f"{image.width} × {image.height} · {format_name}"
            self.ui.input_summary = (
                f"{Path(image.path).name}\n{image.width} × {image.height} · {megapixels:.1f} MP"
            )
            if not self.task_selected:
                self.ui.output_dimensions = "Choose a task"
            elif self.task_index == 1:
                self.ui.output_dimensions = f"{image.width} × {image.height} · original size"
            else:
                output_width = image.width * self.output_scale
                output_height = image.height * self.output_scale
                self.ui.output_dimensions = (
                    f"{output_width} × {output_height} · {self.output_scale}×"
                )

        self.ui.task_summary = (
            TASK_LABELS[self.task_index]
            if self.task_selected and 0 <= self.task_index < len(TASK_LABELS)
            else "Choose a task"
        )
        self.ui.profile_summary = self.profile_label if self.task_selected else "—"

        model = self._selected_catalog_model()
        if model is not None:
            availability = (
                "Installed" if self.model_store.is_installed(model) else "Download required"
            )
            self.ui.model_summary = f"{model.name}\n{availability}"
        elif self.task_selected and self.model_id == CUSTOM_MODEL_ID:
            self.ui.model_summary = (
                Path(self.custom_model_path).name if self.custom_model_path else "Custom checkpoint"
            )
        else:
            self.ui.model_summary = "—"

        if not self.task_selected:
            self.ui.scale_summary = "—"
        elif self.task_index == 1:
            self.ui.scale_summary = "Original dimensions"
        else:
            self.ui.scale_summary = f"{self.output_scale}×"

        format_name = ("PNG", "JPEG", "TIFF")[self.format_index]
        folder = Path(self.output_directory).expanduser()
        folder_label = folder.name or str(folder)
        self.ui.output_summary = f"{format_name} · {folder_label}"

    def _update_action_state(self) -> None:
        estimate_blocking = bool(self.current_estimate and self.current_estimate.blocking)
        downloading = self.download_thread is not None and self.download_thread.is_alive()
        video_task_disabled = self.task_index == VIDEO_TASK_INDEX and not VIDEO_ENABLED
        can_start = bool(
            self.task_selected
            and not video_task_disabled
            and self.images
            and self.model_path
            and self.current_model_info
            and not self.current_job_id
            and not downloading
            and not estimate_blocking
        )
        self.ui.can_start = can_start
        self.ui.can_cancel = bool(self.current_job_id)
        self.ui.controls_enabled = not bool(self.current_job_id)
        self.ui.model_selection_enabled = not bool(self.current_job_id) and not downloading
        self.ui.can_open_result = bool(
            self.last_output_path and Path(self.last_output_path).is_file()
        )
        count = len(self.images) if self.ui.batch_mode else (1 if self._selected_image() else 0)
        verb = (
            "Denoise"
            if self.task_index == 1
            else "Upscale Video"
            if self.task_index == VIDEO_TASK_INDEX
            else "Upscale"
            if self.task_index == 0
            else "Start"
        )
        self.ui.action_text = f"{verb} {count} Images" if count > 1 else verb
        self._sync_inspector()

    def _output_path_for(self, image_path: str) -> str:
        base = Path(image_path).stem
        extension = FORMAT_VALUES[self.format_index]
        if self.task_index == 1:
            suffix = "_denoised"
        elif self.task_index == VIDEO_TASK_INDEX:
            suffix = f"_video_{self.output_scale}x"
        else:
            suffix = f"_upscaled_{self.output_scale}x"
        candidate = Path(self.output_directory) / f"{base}{suffix}.{extension}"
        counter = 1
        while candidate.exists():
            candidate = Path(self.output_directory) / f"{base}{suffix}_{counter}.{extension}"
            counter += 1
        return str(candidate)

    def start_jobs(self) -> None:
        self._update_estimate()
        if not self.ui.can_start:
            return
        if self.ui.batch_mode:
            self.batch_paths = [item.path for item in self.images]
        else:
            selected = self._selected_image()
            self.batch_paths = [selected.path] if selected is not None else []
        if not self.batch_paths:
            return
        self.batch_total = len(self.batch_paths)
        self.batch_current = 0
        self.cancel_batch = False
        self.last_output_path = ""
        self._save_settings()
        self._start_next_job()

    def _start_next_job(self) -> None:
        if self.cancel_batch or not self.batch_paths:
            self.batch_paths.clear()
            self.batch_total = 0
            self.batch_current = 0
            self.ui.batch_progress = ""
            self._update_action_state()
            return
        path = self.batch_paths.pop(0)
        index = next((i for i, item in enumerate(self.images) if item.path == path), -1)
        if index < 0:
            self._start_next_job()
            return
        self.selected_image_index = index
        self._sync_queue()
        self._load_selected_preview()
        self._update_estimate()
        if self.current_estimate and self.current_estimate.blocking:
            self._show_status("Batch stopped", "Resource settings are unsafe for the next image.")
            self.batch_paths.clear()
            return

        self.batch_current += 1
        self.current_job_id = str(uuid.uuid4())
        output_path = self._output_path_for(path)
        self.last_output_path = output_path
        self.job_started_at = time.monotonic()
        self.job_effective_megapixels = (
            self.current_estimate.effective_megapixels
            if self.current_estimate is not None
            else self.images[index].width * self.images[index].height / 1_000_000
        )
        request = JobRequest(
            job_id=self.current_job_id,
            image_path=path,
            model_path=self.model_path,
            output_path=output_path,
            output_format=FORMAT_VALUES[self.format_index],
            device=self.device_id,
            tile_size=self.tile_size,
            halo=self.halo,
            precision=self.precision,
            jpeg_quality=self.jpeg_quality,
            preserve_metadata=self.preserve_metadata,
            safe_memory=self.safe_memory,
            output_scale=self.output_scale,
        )
        self.ui.progress = 0.0
        self.ui.live_result_ready = False
        self.ui.result_ready = False
        self.ui.result_label = (
            "Denoised preview" if self.task_index == 1 else f"Preview · {self.output_scale}×"
        )
        self.ui.status_title = "Starting"
        self.ui.status_detail = f"Loading {Path(path).name} and the selected model."
        self.ui.batch_progress = (
            f"Image {self.batch_current} of {self.batch_total}" if self.batch_total > 1 else ""
        )
        self.worker.send_request(request)
        self._update_action_state()

    def cancel_job(self) -> None:
        if not self.current_job_id:
            return
        self.cancel_batch = True
        self.batch_paths.clear()
        self.worker.send_request(CancelRequest(job_id=self.current_job_id))
        self.ui.status_title = "Cancelling"
        self.ui.status_detail = "Finishing the active tile and cleaning temporary output."
        self.ui.can_cancel = False

    def poll_events(self) -> None:
        for _ in range(200):
            try:
                event_type, data = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event_type, data)
        if (
            self.worker.running
            and not self.current_job_id
            and time.monotonic() >= self._next_hardware_refresh
        ):
            self.refresh_hardware()

    def _handle_event(self, event_type: str, data: dict) -> None:
        handlers = {
            "worker_ready": self._on_worker_ready,
            "model_info": self._on_model_info,
            "capabilities_info": self._on_capabilities,
            "preview_ready": self._on_preview_ready,
            "preview_failed": self._on_preview_failed,
            "tile_update": self._on_tile_update,
            "job_started": self._on_job_started,
            "progress": self._on_progress,
            "job_completed": self._on_job_completed,
            "job_cancelled": self._on_job_cancelled,
            "job_failed": self._on_job_failed,
            "warning": self._on_warning,
            "log": self._on_log,
            "worker_error": self._on_worker_error,
            "download_progress": self._on_download_progress,
            "download_completed": self._on_download_completed,
            "download_cancelled": self._on_download_cancelled,
            "download_failed": self._on_download_failed,
            "faces_detected": self._on_faces_detected,
            "face_detection_unavailable": self._on_face_detection_unavailable,
            "video_frame_started": self._on_video_frame_started,
            "video_frame_completed": self._on_video_frame_completed,
            "video_job_completed": self._on_video_job_completed,
        }
        handler = handlers.get(event_type)
        if handler is not None:
            handler(data)

    def _on_worker_ready(self, _data: dict) -> None:
        self._show_status("Ready", "Worker ready. Add an image or choose a setup.")
        self.refresh_hardware()
        self._inspect_model()

    def _on_model_info(self, data: dict) -> None:
        if not self.model_path:
            return
        filename = str(data.get("filename", ""))
        if filename and filename != Path(self.model_path).name:
            return
        self.current_model_info = dict(data)
        self.model_scale = max(1, int(data.get("scale", 1)))
        self.ui.model_status = (
            f"Ready · {data.get('architecture', 'Unknown')} · {self.model_scale}× · "
            f"{int(data.get('parameter_count', 0)) / 1_000_000:.1f}M parameters"
        )
        self._configure_scales()
        self._apply_hardware_constraints()
        self._apply_pending_preset()
        self._update_estimate()

    def _on_capabilities(self, data: dict) -> None:
        devices = data.get("devices")
        if not isinstance(devices, list) or not devices:
            return
        self.capability_report = dict(data)
        self.devices = [device for device in devices if isinstance(device, dict)]
        if not any(str(device.get("id")) == self.device_id for device in self.devices):
            accelerated = next(
                (device for device in self.devices if str(device.get("type")) != "cpu"),
                self.devices[0],
            )
            self.device_id = str(accelerated.get("id", "cpu"))
        self.memory_snapshot.update(data)
        self._apply_hardware_constraints()

    def _on_preview_ready(self, data: dict) -> None:
        selected = self._selected_image()
        if selected is None or str(data.get("image_path", "")) != selected.path:
            return
        path = self.preview.set_source_base64(str(data.get("jpeg_base64", "")))
        if path is not None:
            self.ui.source_image = slint.Image.load_from_path(str(path))
            self.ui.image_ready = True
            source_width = max(1, int(self.ui.source_image.width))
            source_height = max(1, int(self.ui.source_image.height))
            self.ui.preview_aspect = source_width / source_height
            self.ui.source_label = f"Original · {selected.width} × {selected.height}"

    def _on_preview_failed(self, data: dict) -> None:
        self.runtime_warning = f"Preview unavailable: {data.get('error_message', 'unknown error')}"
        self.ui.estimate_warning = self.runtime_warning

    def _on_tile_update(self, data: dict) -> None:
        if data.get("job_id") != self.current_job_id:
            return
        phase = str(data.get("phase", ""))
        image_width = int(data.get("image_width", 0))
        image_height = int(data.get("image_height", 0))
        if phase == "reset":
            path = self.preview.reset_progressive(image_width, image_height)
            self.ui.result_image = slint.Image.load_from_path(str(path))
            self.ui.live_result_ready = True
            self.ui.result_ready = False
            self.ui.tile_active = False
            return
        coordinates = self.preview.mark_active_tile(
            int(data.get("output_x", 0)),
            int(data.get("output_y", 0)),
            int(data.get("output_width", 0)),
            int(data.get("output_height", 0)),
            image_width,
            image_height,
        )
        self.ui.tile_x, self.ui.tile_y, self.ui.tile_width, self.ui.tile_height = coordinates
        if phase == "started":
            if not self.ui.live_result_ready:
                path = self.preview.reset_progressive(image_width, image_height)
                self.ui.result_image = slint.Image.load_from_path(str(path))
                self.ui.live_result_ready = True
                self.ui.result_ready = False
            self.ui.tile_active = True
        elif phase == "completed":
            path = self.preview.apply_tile(
                jpeg_base64=str(data.get("jpeg_base64", "")),
                output_x=int(data.get("output_x", 0)),
                output_y=int(data.get("output_y", 0)),
                output_width=int(data.get("output_width", 0)),
                output_height=int(data.get("output_height", 0)),
                image_width=image_width,
                image_height=image_height,
            )
            if path is not None:
                self.ui.result_image = slint.Image.load_from_path(str(path))
                self.ui.live_result_ready = True
            self.ui.tile_active = False

    def _on_job_started(self, data: dict) -> None:
        if data.get("job_id") != self.current_job_id:
            return
        self.ui.status_title = "Processing"
        self.ui.status_detail = "Inference started; completed tiles appear in the preview."
        self._update_action_state()

    def _on_progress(self, data: dict) -> None:
        if data.get("job_id") != self.current_job_id:
            return
        percentage = float(data.get("percentage", 0.0))
        self.ui.progress = max(0.0, min(1.0, percentage / 100.0))
        self.ui.status_title = f"Processing · {percentage:.0f}%"
        self.ui.status_detail = (
            f"Tile {int(data.get('completed_tiles', 0))}/{int(data.get('total_tiles', 0))} · "
            f"ETA {format_duration(float(data.get('estimated_remaining_seconds', 0)))}"
        )
        self.memory_snapshot.update(data)
        self._update_hardware_display()

    def _on_job_completed(self, data: dict) -> None:
        if data.get("job_id") != self.current_job_id:
            return
        self._record_throughput(data)
        self.current_job_id = ""
        output_path = str(data.get("output_path", ""))
        if output_path:
            self.last_output_path = output_path
        self.runtime_warning = ""
        self.ui.progress = 1.0
        self.ui.tile_active = False
        self.ui.result_ready = bool(self.ui.live_result_ready)
        if self.batch_paths and not self.cancel_batch:
            self.ui.status_title = "Image complete"
            self.ui.status_detail = "Starting the next queued image."
            self._start_next_job()
            return
        completed = self.batch_total or 1
        self.batch_paths.clear()
        self.batch_total = 0
        self.batch_current = 0
        self.ui.batch_progress = ""
        self._show_status(
            "Complete",
            f"Finished {completed} image{'s' if completed != 1 else ''}. Output saved successfully.",
        )
        self.refresh_hardware()
        self._update_estimate()

    def _record_throughput(self, data: dict) -> None:
        if self.job_started_at is None or self.job_effective_megapixels <= 0:
            return
        elapsed = float(data.get("inference_seconds", 0.0)) or (
            time.monotonic() - self.job_started_at
        )
        if elapsed < 3.0:
            return
        measured = elapsed / self.job_effective_megapixels
        key = self._throughput_key(str(self._current_device().get("type", "cpu")))
        previous = self.settings.get(key)
        try:
            previous_value = float(previous) if previous is not None else None
        except (TypeError, ValueError):
            previous_value = None
        value = previous_value * 0.7 + measured * 0.3 if previous_value else measured
        self.settings.set(key, value)

    def _on_job_cancelled(self, data: dict) -> None:
        if data.get("job_id") != self.current_job_id:
            return
        self.current_job_id = ""
        self.batch_paths.clear()
        self.batch_total = 0
        self.batch_current = 0
        self.ui.batch_progress = ""
        self.ui.progress = 0.0
        self.ui.tile_active = False
        self.ui.live_result_ready = False
        self.ui.result_ready = False
        self.ui.result_image = slint.Image()
        self._show_status("Cancelled", "Temporary output was cleaned up.")
        self.refresh_hardware()
        self._update_action_state()

    def _on_job_failed(self, data: dict) -> None:
        if data.get("job_id") != self.current_job_id:
            return
        self.current_job_id = ""
        self.batch_paths.clear()
        self.batch_total = 0
        self.batch_current = 0
        self.ui.batch_progress = ""
        self.ui.progress = 0.0
        self.ui.tile_active = False
        self.ui.live_result_ready = False
        self.ui.result_ready = False
        self.ui.result_image = slint.Image()
        friendly = self._friendly_error(str(data.get("error_message", data.get("error", ""))))
        self.runtime_warning = friendly
        self._show_status("Processing failed", friendly)
        self._update_estimate()

    @staticmethod
    def _friendly_error(message: str) -> str:
        message = message or "Unknown worker error."
        lower = message.lower()
        if any(term in lower for term in ("out of memory", "cannot allocate", "allocation")):
            return (
                "The selected device ran out of memory. Choose a smaller tile, enable Safe "
                f"Memory Mode, or switch to CPU. Technical detail: {message}"
            )
        if "no space" in lower or "disk full" in lower:
            return "The output drive is full. Choose another folder or free disk space."
        if any(name in lower for name in ("cuda", "rocm", "xpu")) and "driver" in lower:
            return f"The GPU or its driver is unavailable. Refresh hardware or use CPU. {message}"
        return message

    def _on_warning(self, data: dict) -> None:
        self.runtime_warning = str(data.get("message", "Worker warning"))
        self._show_status("Warning", self.runtime_warning)
        self._update_estimate()

    def _on_log(self, data: dict) -> None:
        message = str(data.get("message", ""))
        if message == "Writing final output...":
            self.ui.status_title = "Saving"
            self.ui.status_detail = "Writing the final image atomically to disk."
        if message:
            print(f"[{str(data.get('level', 'info')).upper()}] {message}")

    def _on_worker_error(self, data: dict) -> None:
        message = str(data.get("message", "Worker process failed."))
        self.current_job_id = ""
        self.pending_preset = None
        self.pending_auto_start = False
        self.runtime_warning = message
        self._show_status("Worker error", message)
        self._update_action_state()
        if not self._shutdown:
            self.worker.start()

    def _download_matches(self, data: dict) -> bool:
        model = self._selected_catalog_model()
        return model is not None and data.get("model_id") == model.model_id

    def _on_download_progress(self, data: dict) -> None:
        if not self._download_matches(data):
            return
        downloaded = int(data.get("downloaded", 0))
        total = max(1, int(data.get("total", 0)))
        percentage = downloaded / total
        self.ui.download_visible = True
        self.ui.download_progress = percentage
        self.ui.model_status = (
            f"Downloaded {format_bytes(downloaded)} of {format_bytes(total)} "
            f"({percentage * 100:.0f}%)."
        )

    def _finish_download_ui(self) -> None:
        self.download_thread = None
        self.download_cancel = None
        self.ui.download_visible = False
        self.ui.download_progress = 0.0
        self._update_action_state()

    def _on_download_completed(self, data: dict) -> None:
        matches = self._download_matches(data)
        self._finish_download_ui()
        if not matches:
            return
        self.model_path = str(data.get("path", ""))
        self._refresh_model_option_labels()
        self.ui.model_action_text = "Model Installed"
        self.ui.model_action_enabled = False
        self.ui.model_status = "Download complete and SHA-256 verified. Inspecting model…"
        self._inspect_model()

    def _on_download_cancelled(self, data: dict) -> None:
        matches = self._download_matches(data)
        self._finish_download_ui()
        if not matches:
            return
        self.pending_preset = None
        self.pending_preset_label = ""
        self.pending_auto_start = False
        self.profile_label = "Manual"
        model = self._selected_catalog_model()
        self.ui.model_action_text = (
            f"Download {model.size_megabytes:.0f} MB" if model else "Download"
        )
        self.ui.model_action_enabled = True
        self.ui.model_status = "Download cancelled; the partial file was removed."
        self._show_status(
            "Download cancelled",
            "No model was installed; the partial download was removed.",
        )
        self._sync_inspector()

    def _on_download_failed(self, data: dict) -> None:
        matches = self._download_matches(data)
        self._finish_download_ui()
        if not matches:
            return
        self.pending_preset = None
        self.pending_preset_label = ""
        self.pending_auto_start = False
        self.profile_label = "Manual"
        model = self._selected_catalog_model()
        self.ui.model_action_text = (
            f"Download {model.size_megabytes:.0f} MB" if model else "Download"
        )
        self.ui.model_action_enabled = True
        self.runtime_warning = f"Model download failed: {data.get('message', 'unknown error')}"
        self.ui.model_status = self.runtime_warning
        self._show_status("Download failed", self.runtime_warning)
        self._sync_inspector()

    # ── Face detection ──────────────────────────────────────────────

    def detect_faces(self) -> None:
        """Send a DetectFacesRequest to the worker for the selected image."""
        image = self._selected_image()
        if image is None:
            self._show_status("No image", "Select an image before detecting faces.")
            return
        if self.current_job_id:
            return
        self.ui.face_detecting = True
        self.ui.face_status = "Detecting faces…"
        from localsr.protocol.messages import DetectFacesRequest

        self.worker.send_request(DetectFacesRequest(image_path=image.path))

    def clear_faces(self) -> None:
        """Clear detected face boxes from the UI."""
        self.detected_face_boxes = []
        self.ui.face_count = 0
        self.ui.face_status = ""

    def _on_faces_detected(self, data: dict) -> None:
        boxes = data.get("boxes", [])
        self.detected_face_boxes = boxes
        self.ui.face_detecting = False
        count = len(boxes)
        self.ui.face_count = count
        if count == 0:
            self.ui.face_status = "No faces found."
            self._show_status("Face detection", "No faces detected in this image.")
        else:
            self.ui.face_status = f"{count} face{'s' if count != 1 else ''} detected."
            self._show_status(
                "Face detection",
                f"{count} face{'s' if count != 1 else ''} detected. "
                "Face-aware restoration will use the face model on these regions.",
            )

    def _on_face_detection_unavailable(self, data: dict) -> None:
        self.ui.face_detecting = False
        self.ui.face_detection_available = False
        message = data.get("message", "Face detection is unavailable.")
        self.ui.face_status = message
        self._show_status("Face detection unavailable", message)

    # ── Video job event handlers ─────────────────────────────────────

    def _on_video_frame_started(self, _data: dict) -> None:
        pass

    def _on_video_frame_completed(self, data: dict) -> None:
        # Update progress for video jobs. The thumbnail is carried in the
        # jpeg_base64 field when present.
        pass

    def _on_video_job_completed(self, data: dict) -> None:
        if data.get("job_id") != self.current_job_id:
            return
        self.current_job_id = ""
        output_path = str(data.get("output_path", ""))
        if output_path:
            self.last_output_path = output_path
        self.batch_paths.clear()
        self.batch_total = 0
        self.batch_current = 0
        frames = int(data.get("frames_processed", 0))
        self._show_status(
            "Video complete",
            f"Processed {frames} frame{'s' if frames != 1 else ''}. Output saved.",
        )
        self.refresh_hardware()
        self._update_action_state()

    def _show_status(self, title: str, detail: str) -> None:
        self.ui.status_title = title
        self.ui.status_detail = detail

    def open_result(self) -> None:
        path = self.last_output_path
        if not path or not Path(path).is_file():
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])

    def reveal_result(self) -> None:
        path = self.last_output_path
        if not path or not Path(path).is_file():
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            subprocess.Popen(["xdg-open", str(Path(path).parent)])

    def _save_settings(self) -> None:
        self.settings.set("batch_mode", bool(self.ui.batch_mode))
        self.settings.set("task_index", self.task_index)
        self.settings.set("selected_model_id", self.model_id)
        self.settings.set("custom_model_path", self.custom_model_path)
        self.settings.set("output_scale", self.output_scale)
        self.settings.set("format_index", self.format_index)
        self.settings.set("output_directory", self.output_directory)
        self.settings.set("preserve_metadata", self.preserve_metadata)
        self.settings.set("jpeg_quality", self.jpeg_quality)
        self.settings.set("device_id", self.device_id)
        self.settings.set("tile_size", self.tile_size)
        self.settings.set("halo", self.halo)
        self.settings.set("precision", self.precision)
        self.settings.set("safe_memory", self.safe_memory)
        try:
            self.settings.save()
        except OSError as error:
            self._show_status("Settings warning", f"Could not save settings: {error}")

    def run(self) -> None:
        try:
            self.ui.run()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._save_settings()
        if self.download_cancel is not None:
            self.download_cancel.set()
        if self.download_thread is not None and self.download_thread.is_alive():
            self.download_thread.join(timeout=3)
        self.worker.stop()
        self.preview.close()


def create_slint_application(
    *,
    start_worker: bool = True,
    settings_path: str | Path | None = None,
    model_root: str | Path | None = None,
) -> SlintApplication:
    return SlintApplication(
        start_worker=start_worker,
        settings_path=settings_path,
        model_root=model_root,
    )
