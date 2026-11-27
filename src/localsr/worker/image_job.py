"""Execute an image recipe while owning its previews and temporary outputs."""

from __future__ import annotations

import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

import numpy as np
import psutil
from PIL import Image

import localsr.core.steps as step_registry
from localsr.core.disk_space import failure_fields, require_image_room
from localsr.core.face_compositing import blend_with_alpha, load_mask
from localsr.core.grain import add_grain
from localsr.core.image_io import ImageManager, require_output_format
from localsr.core.inference import InferenceEngine, UnstableRestorationError
from localsr.core.live_preview import LatestPreviewEncoder, PreviewPacket
from localsr.core.model_adapter import ModelAdapter, NormalizedModelInfo
from localsr.core.model_catalog import CATALOG_BY_ID
from localsr.core.output_publish import DiscardOnStop
from localsr.core.output_writer import OutputWriter, cleanup_stale_work_files
from localsr.core.partial_image import (
    TilesStopped,
    fill_unfinished,
    partial_output_path,
    saved_tiles,
)
from localsr.core.pipeline import (
    ImagePipeline,
    PipelineStage,
    PipelineStageError,
    resolve_image_pipeline,
)
from localsr.core.steps import FIX_SHORT_NAMES, is_model_step_stage, resolved_step_params
from localsr.protocol.messages import (
    EngineStatus,
    JobCancelled,
    JobCompleted,
    JobFailed,
    LivePreviewWarning,
    LogMessage,
    WarningMessage,
    WorkerMessage,
)
from localsr.worker.job_progress import ImageJobProgress

# 8-bit ImageManager.save allocates ~8 B/px (Pillow image + strips) plus encoder
# buffers; 16 B/px (32 B/px for 16-bit) keeps an overlapped save within a
# quarter of available RAM so concurrent inference on the next photo stays out of swap.
_OVERLAP_BYTES_PER_PIXEL = 16
_FALLBACK_AVAILABLE_MEMORY = 1 * 2**30


def overlap_pixel_threshold(bit_depth: int = 8) -> int:
    """Maximum output pixel count for overlapping a save with the next photo."""
    try:
        available = int(psutil.virtual_memory().available)
    except (OSError, RuntimeError, AttributeError):
        available = 0
    if available <= 0:
        available = _FALLBACK_AVAILABLE_MEMORY
    bytes_per_pixel = _OVERLAP_BYTES_PER_PIXEL * (2 if bit_depth == 16 else 1)
    return max(1, (available // 4) // bytes_per_pixel)


@dataclass(slots=True)
class _SaveTask:
    job_id: str
    manager: ImageManager
    writer: Any
    image: dict[str, Any]
    info: NormalizedModelInfo
    data: Mapping[str, Any]
    notes: list[str]
    started_at: float
    inference_started_at: float
    discard_signal: DiscardOnStop
    emit: Callable[[WorkerMessage], None]
    log: Callable[[str], None]


class SavePipeline:
    """One background save thread behind a depth-1 slot so at most two results exist."""

    def __init__(self, *, pixel_threshold: int | None = None) -> None:
        self._pixel_threshold = pixel_threshold
        self._cond = threading.Condition()
        self._pending: _SaveTask | None = None
        self._active = False
        self._closed = False
        self._thread: threading.Thread | None = None

    def exceeds_threshold(self, output_pixels: int, bit_depth: int = 8) -> bool:
        limit = (
            self._pixel_threshold
            if self._pixel_threshold is not None
            else overlap_pixel_threshold(bit_depth)
        )
        return output_pixels > limit

    def wait_if_large(self, output_pixels: int, bit_depth: int = 8) -> None:
        if self.exceeds_threshold(output_pixels, bit_depth):
            self.drain()

    def submit(self, task: _SaveTask) -> None:
        with self._cond:
            while self._active:
                self._cond.wait(timeout=0.5)
            if self._closed:
                raise RuntimeError("SavePipeline is closed.")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="localsr-save-pipeline", daemon=True
                )
                self._thread.start()
            self._pending = task
            self._active = True
            self._cond.notify_all()

    def drain(self) -> None:
        with self._cond:
            while self._active:
                self._cond.wait(timeout=0.5)

    def close(self) -> None:
        with self._cond:
            while self._active:
                self._cond.wait(timeout=0.5)
            self._closed = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join()

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._pending is None and not self._closed:
                    self._cond.wait(timeout=0.5)
                if self._pending is None and self._closed:
                    return
                task = self._pending
                self._pending = None
            assert task is not None
            try:
                self._execute(task)
            finally:
                with self._cond:
                    self._active = False
                    self._cond.notify_all()

    def _execute(self, task: _SaveTask) -> None:
        try:
            try:
                task.log("Writing final output...")
                save_notes = save_output(
                    task.manager,
                    task.writer,
                    task.image,
                    task.info,
                    task.data,
                    task.discard_signal,
                )
            except InterruptedError:
                task.emit(JobCancelled(job_id=task.job_id))
                return
            except Exception as error:  # noqa: BLE001 - a save failure stops only this photo
                traceback.print_exc(file=sys.stderr)
                task.emit(
                    JobFailed(
                        job_id=task.job_id,
                        **failure_fields(error, task.data.get("output_path")),
                    )
                )
                return
            completed_at = time.monotonic()
            notes = [*task.notes, *save_notes]
            task.emit(
                JobCompleted(
                    job_id=task.job_id,
                    elapsed_seconds=completed_at - task.started_at,
                    inference_seconds=completed_at - task.inference_started_at,
                    output_path=task.data["output_path"],
                    notes=" ".join(notes),
                )
            )
        finally:
            task.writer.cleanup()


def save_output(
    manager: ImageManager,
    writer: OutputWriter,
    image: Mapping[str, Any],
    info: NormalizedModelInfo,
    data: Mapping[str, Any],
    cancel_event: threading.Event,
) -> list[str]:
    """Write a finished image atomically in the job's format, as every image job does.

    Returns what the save left out or changed, as sentences for the job's notes.
    """
    bit_depth = int(data.get("output_bit_depth") or 8)
    manager.save(
        writer,
        data["output_path"],
        format=data["output_format"],
        quality=data["jpeg_quality"],
        preserve_metadata=data["preserve_metadata"],
        icc_profile=image.get("icc_profile"),
        safe_exif=image.get("safe_exif", {}),
        scale=info.scale,
        output_scale=int(data.get("output_scale") or info.scale),
        temporary_directory=data.get("output_temporary_directory"),
        cancel_event=cancel_event,
        **({"output_bit_depth": 16} if bit_depth == 16 else {}),
    )
    return list(manager.notes)


def _apply_stage_mask(stage: PipelineStage, stage_input: Any, stage_output: Any) -> np.ndarray:
    """Composite ``mask * stage_output + (1 - mask) * stage_input`` once in one place."""
    assert stage.mask_path is not None
    if hasattr(stage_output, "detach"):
        out_arr = stage_output.detach().cpu().numpy()
    else:
        out_arr = np.asarray(stage_output)
    in_tensor = stage_input.detach().cpu()
    height, width = int(out_arr.shape[1]), int(out_arr.shape[2])
    if tuple(in_tensor.shape[1:]) != (height, width):
        import torch.nn.functional as F

        in_tensor = F.interpolate(
            in_tensor[None], size=(height, width), mode="bilinear", align_corners=False
        )[0]
    mask = load_mask(stage.mask_path, width, height)
    if out_arr.dtype == np.uint16:
        import torch

        in_arr = (
            in_tensor.clamp(0, 1)
            .mul(65535.0)
            .round()
            .to(torch.int32)
            .numpy()
            .astype(np.uint16, copy=False)
        )
    elif out_arr.dtype == np.uint8:
        in_arr = in_tensor.clamp(0, 1).mul(255).round().byte().numpy()
    else:
        in_arr = in_tensor.numpy().astype(np.float32, copy=False)
        out_arr = out_arr.astype(np.float32, copy=False)
    return blend_with_alpha(out_arr, in_arr, mask)


def _alpha_plane(alpha: Image.Image, width: int, height: int) -> np.ndarray:
    """An 8- or 16-bit alpha as values 0..1 at ``width`` x ``height``, resized as saving does."""
    raw = np.asarray(alpha)
    values = raw.astype(np.float32) / (65535.0 if raw.dtype == np.uint16 else 255.0)
    if values.shape != (height, width):
        values = np.asarray(
            Image.fromarray(values).resize((width, height), Image.Resampling.LANCZOS),
            dtype=np.float32,
        )
    return np.clip(values, 0.0, 1.0)


def _with_matte(
    image: dict[str, Any], matte: Any, stage: PipelineStage, data: Mapping[str, Any]
) -> dict[str, Any]:
    """``image`` with its alpha multiplied by ``matte`` (height x width, 0..1); colour unchanged."""
    if hasattr(matte, "detach"):
        matte = matte.detach().cpu().float().numpy()
    values = np.asarray(matte, dtype=np.float32)
    height, width = (int(size) for size in image["tensor"].shape[-2:])
    if values.shape != (height, width):
        raise ValueError(
            f"The {stage.step_id} step returned a matte of {values.shape}, not ({height}, {width})."
        )
    values = np.clip(np.nan_to_num(values, nan=0.0), 0.0, 1.0)
    if stage.mask_path:
        # Outside the mask the alpha stays as it was.
        mask = load_mask(stage.mask_path, width, height)
        values = mask * values + (1.0 - mask)
    existing = image.get("alpha_image")
    if existing is not None:
        values = values * _alpha_plane(existing, width, height)
    if int(data.get("output_bit_depth") or 8) == 16:
        alpha = Image.fromarray(np.rint(values * 65535.0).astype(np.uint16))
    else:
        alpha = Image.fromarray(np.rint(values * 255.0).astype(np.uint8))
    return {**image, "alpha_image": alpha}


def _run_fix_model_stage(
    runner: ImageJobRunner,
    image: dict[str, Any],
    stage: PipelineStage,
    _index: int,
    data: Mapping[str, Any],
    progress: ImageJobProgress,
    notes: list[str],
    *,
    clear_grayscale: bool = False,
) -> dict[str, Any]:
    runner._log(f"Running {stage.kind} stage with {stage.model_id}...")
    try:
        with ExitStack() as resources:
            info = runner.model_adapter.inspect(stage.model_path)
            if info.scale != 1:
                raise ValueError(
                    "A preprocessing restoration checkpoint must have native scale 1×."
                )
            channels = image["tensor"].shape[0]
            if info.in_channels != channels or info.out_channels != channels:
                raise ValueError("A fix must preserve the working image channels.")
            # A fix keeps at least the overlap its own model needs, whatever the
            # upscaler after it uses.
            model = CATALOG_BY_ID.get(stage.model_id)
            halo = max(int(data["halo"]), model.recommended_halo if model else 0)
            writer = runner._process_image(image, stage, info, data, progress, halo=halo)
            resources.callback(writer.cleanup)
            runner._check_cancelled()
            import torch

            # Copy before releasing the memmap; metadata and alpha remain
            # attached to the original image until the final atomic save.
            restored = np.asarray(writer.get_array())
            if restored.shape != tuple(image["tensor"].shape):
                raise ValueError("A fix must preserve the working image dimensions and channels.")
            divisor = 65535.0 if restored.dtype == np.uint16 else 255.0
            return {
                **image,
                "grayscale": False
                if clear_grayscale or info.architecture == "DDColor"
                else image.get("grayscale", False),
                "tensor": torch.from_numpy(np.array(restored, copy=True)).float().div_(divisor),
                "_skip_preview": info.architecture == "DDColor",
            }
    except UnstableRestorationError as error:
        # The fix before the main model is optional. A model that cannot handle this
        # image (NAFNet's deblur runs away on line art, screentone and already sharp
        # pictures) must not stop the job: the image goes on without this fix, the job
        # says so, and nothing damaged is kept.
        model = CATALOG_BY_ID.get(stage.model_id)
        reason = str(error).split(". ")[0].rstrip(".")
        problem = FIX_SHORT_NAMES.get(stage.fix or "", "")
        note = (
            f"The {problem + ' ' if problem else ''}fix "
            f"({model.display_name if model else stage.model_id}) was skipped for this "
            f"image; the next step used the picture without it. Reason: {reason}."
        )
        runner._log(f"{note} ({error})")
        runner.emit(WarningMessage(message=note))
        notes.append(note)
        return {**image, "_skip_preview": True}
    finally:
        if stage.model_path:
            runner.engine.release_model(stage.model_path)
        runner.model_adapter.release()


def _run_colour_fix_stage(
    runner: ImageJobRunner,
    image: dict[str, Any],
    stage: PipelineStage,
    index: int,
    data: Mapping[str, Any],
    progress: ImageJobProgress,
    notes: list[str],
) -> dict[str, Any]:
    return _run_fix_model_stage(
        runner, image, stage, index, data, progress, notes, clear_grayscale=True
    )


def _run_grain_stage(
    _runner: ImageJobRunner,
    image: dict[str, Any],
    stage: PipelineStage,
    _index: int,
    _data: Mapping[str, Any],
    _progress: ImageJobProgress,
    _notes: list[str],
) -> dict[str, Any]:
    params = resolved_step_params(stage.step_id, stage.params)
    return {
        **image,
        "tensor": add_grain(
            image["tensor"], strength=int(params["strength"]), seed=int(params["seed"])
        ),
    }


@dataclass(frozen=True, slots=True)
class StepModel:
    """A step stage's catalog model, checked and loaded the way a fix's is, active on the
    engine while its handler runs."""

    path: str
    info: NormalizedModelInfo
    module: Any
    device: Any
    precision: Any


# A step's handler returns the updated image; a step with `output_alpha` returns a matte
# (height x width, values 0..1) that the stage multiplies into the image's alpha.
StageImpl = Callable[
    [
        "ImageJobRunner",
        dict[str, Any],
        PipelineStage,
        int,
        Mapping[str, Any],
        ImageJobProgress,
        list[str],
    ],
    Any,
]
ModelStageImpl = Callable[
    [
        "ImageJobRunner",
        dict[str, Any],
        PipelineStage,
        int,
        Mapping[str, Any],
        ImageJobProgress,
        list[str],
        StepModel,
    ],
    Any,
]


BIREFNET_INPUT_SIZE = 1024


def _run_background_stage(runner, image, _stage, _index, _data, _progress, _notes, model):
    import torch
    import torch.nn.functional as F

    runner._check_cancelled()
    rgb = image["tensor"][:3].unsqueeze(0).to(dtype=torch.float32)
    size = rgb.shape[-2:]
    rgb = F.interpolate(
        rgb, size=(BIREFNET_INPUT_SIZE, BIREFNET_INPUT_SIZE), mode="bilinear", align_corners=False
    ).to(device=model.device)
    mean = rgb.new_tensor((0.485, 0.456, 0.406)).view(1, 3, 1, 1)
    std = rgb.new_tensor((0.229, 0.224, 0.225)).view(1, 3, 1, 1)
    with torch.inference_mode():
        logits = model.module((rgb - mean) / std)
        matte = logits.sigmoid().float().cpu()
    runner._check_cancelled()
    return F.interpolate(matte, size=size, mode="bilinear", align_corners=False)[0, 0]


# Dispatch non-primary stages by registry step id; new steps add one entry here.
IMAGE_STEP_HANDLERS: dict[str, StageImpl] = {
    "blur": _run_fix_model_stage,
    "jpeg": _run_fix_model_stage,
    "noise": _run_fix_model_stage,
    "colour": _run_colour_fix_stage,
    "restore": _run_fix_model_stage,
    "grain": _run_grain_stage,
}

# Step stages that bring a catalog model (`model_task`); each handler gets it loaded.
IMAGE_MODEL_STEP_HANDLERS: dict[str, ModelStageImpl] = {"remove_background": _run_background_stage}

# Primary steps that produce colour output from monochrome sources.
_PRIMARY_GRAYSCALE_CLEARING_STEPS: frozenset[str] = frozenset({"colour"})


class ImageJobRunner:
    def __init__(
        self,
        *,
        engine: InferenceEngine,
        model_adapter: ModelAdapter,
        cancel_event: threading.Event,
        emit: Callable[[WorkerMessage], None],
        emit_preview: Callable[[PreviewPacket], None],
        preview_factory: Callable[..., LatestPreviewEncoder] = LatestPreviewEncoder,
        keep_partial: Callable[[], bool] | None = None,
        save_pipeline: SavePipeline | None = None,
        overlap_save: bool = False,
    ) -> None:
        # Whether a stop keeps the finished tiles of the upscaling step (the host asks).
        self.keep_partial = keep_partial
        self.engine = engine
        self.model_adapter = model_adapter
        self.cancel_event = cancel_event
        self.emit = emit
        self.emit_preview = emit_preview
        self.preview_factory = preview_factory
        self.save_pipeline = save_pipeline
        self.overlap_save = overlap_save

    def run(self, job_id: str, data: Mapping[str, Any]) -> None:
        bit_depth = int(data.get("output_bit_depth") or 8)
        require_output_format(
            str(data["output_format"]),
            **({"output_bit_depth": bit_depth} if bit_depth != 8 else {}),
        )
        started_at = time.monotonic()
        self.engine.neural_engine_enabled = bool(data.get("neural_engine", True))
        self.engine.status_callback = lambda message: self.emit(
            EngineStatus(job_id=job_id, message=message)
        )
        self.model_adapter.status_callback = self.engine.status_callback
        self.model_adapter.cancel_event = self.cancel_event
        self._take_engine_notes()
        pipeline = resolve_image_pipeline(
            raw_stages=data.get("stages"),
            model_path=str(data["model_path"]),
            output_scale=data.get("output_scale"),
            face_model_path=data.get("face_model_path"),
            face_fidelity=float(data.get("face_fidelity", 0.7)),
        )
        # The engine cache otherwise keeps every upscaler used since the engine started. A
        # fix runs alone: the upscaler is loaded after it, not kept beside it.
        face_path = None if pipeline.primary is None else data.get("face_model_path")
        first_preprocess_model = next(
            (stage.model_path for stage in pipeline.preprocess_stages if stage.model_path),
            None,
        )
        self.engine.release_models_except(
            {first_preprocess_model}
            if first_preprocess_model is not None
            else {stage.model_path for stage in pipeline.stages if stage.model_path}
            | ({face_path} if face_path else set())
        )
        scratch = data.get("scratch_directory")
        if scratch:
            cleanup_stale_work_files(scratch)

        try:
            # Ownership begins before image decoding: corrupt media must also
            # release an encoder, and failed writes must release every memmap.
            kept_writer: list[OutputWriter | None] = [None]
            with ExitStack() as resources:
                preview = self.preview_factory(
                    self.emit_preview,
                    lambda message: self.emit(LivePreviewWarning(job_id=job_id, message=message)),
                    enabled=bool(data.get("preview_enabled", True)),
                    max_fps=float(data.get("preview_max_fps", 2.0)),
                    max_dimension=int(data.get("preview_max_dimension", 320)),
                )
                resources.callback(preview.close)
                progress = ImageJobProgress(job_id, pipeline, data["device"], self.emit, preview)
                self._check_cancelled()
                self._log("Loading image...")
                manager = ImageManager()
                image = manager.load(
                    data["image_path"],
                    **({"bit_depth": 16} if bit_depth == 16 else {}),
                )
                self._log("Starting inference pipeline...")
                processing_started_at = time.monotonic()
                working_image = image
                notes: list[str] = []
                if pipeline.preprocess_stages and self.save_pipeline is not None:
                    self.save_pipeline.drain()
                for index, stage in enumerate(pipeline.preprocess_stages):
                    working_image = self._run_stage(
                        working_image, stage, index, data, progress, notes
                    )
                    if not working_image.get("grayscale", True):
                        manager.grayscale = False
                    if "alpha_image" in working_image:
                        manager.alpha_channel = working_image["alpha_image"]
                self._check_cancelled()
                primary_clears_grayscale = (
                    pipeline.primary is not None
                    and pipeline.primary.step_id in _PRIMARY_GRAYSCALE_CLEARING_STEPS
                )
                try:
                    writer, info = self._primary(
                        working_image,
                        pipeline,
                        data,
                        progress,
                        resources,
                        kept_writer=kept_writer,
                    )
                except TilesStopped as stopped:
                    resources.callback(stopped.writer.cleanup)
                    colour = stopped.info is not None and stopped.info.architecture == "DDColor"
                    if colour or primary_clears_grayscale:
                        manager.grayscale = False
                    if self.save_pipeline is not None:
                        self.save_pipeline.drain()
                    self._save_finished_part(
                        job_id, data, manager, image, working_image, stopped, started_at
                    )
                    return
                if info.architecture == "DDColor" or primary_clears_grayscale:
                    manager.grayscale = False
                if pipeline.postprocess_stages:
                    writer = self._run_postprocess(
                        writer,
                        working_image,
                        pipeline,
                        data,
                        progress,
                        notes,
                        resources,
                        manager,
                        kept_writer=kept_writer,
                    )
                # Every tile is done: a stop that keeps the finished part saves it whole.
                if self._discard_signal().is_set():
                    raise InterruptedError("Cancelled before the output was saved.")
                shape = getattr(writer, "shape", None)
                output_pixels = int(shape[1]) * int(shape[2]) if shape is not None else 0
                if (
                    self.save_pipeline is not None
                    and self.overlap_save
                    and not self.save_pipeline.exceeds_threshold(output_pixels, bit_depth)
                ):
                    notes.extend(self._take_engine_notes())
                    save_image = {
                        "icc_profile": image.get("icc_profile"),
                        "safe_exif": image.get("safe_exif", {}),
                    }
                    inf_start = (
                        progress.inference_started_at
                        if progress.inference_started_at is not None
                        else processing_started_at
                    )
                    kept_writer[0] = writer
                    del image, working_image
                    try:
                        self.save_pipeline.submit(
                            _SaveTask(
                                job_id=job_id,
                                manager=manager,
                                writer=writer,
                                image=save_image,
                                info=info,
                                data=dict(data),
                                notes=notes,
                                started_at=started_at,
                                inference_started_at=inf_start,
                                discard_signal=self._discard_signal(),
                                emit=self.emit,
                                log=self._log,
                            )
                        )
                    except BaseException:
                        kept_writer[0] = None
                        raise
                    return
                if self.save_pipeline is not None:
                    self.save_pipeline.drain()
                self._log("Writing final output...")
                notes.extend(self._save(manager, writer, image, info, data))
                notes.extend(self._take_engine_notes())
            completed_at = time.monotonic()
            self.emit(
                JobCompleted(
                    job_id=job_id,
                    elapsed_seconds=completed_at - started_at,
                    inference_seconds=completed_at
                    - (
                        progress.inference_started_at
                        if progress.inference_started_at is not None
                        else processing_started_at
                    ),
                    output_path=data["output_path"],
                    notes=" ".join(notes),
                )
            )
        except InterruptedError:
            self.emit(JobCancelled(job_id=job_id))

    def _take_engine_notes(self) -> list[str]:
        """What the engine's backends said about the route they took, once per job."""
        take: Callable[[], list[str]] | None = getattr(self.engine, "take_notes", None)
        return take() if callable(take) else []

    def _run_stage(
        self,
        image: dict[str, Any],
        stage: PipelineStage,
        index: int,
        data: Mapping[str, Any],
        progress: ImageJobProgress,
        notes: list[str],
    ) -> dict[str, Any]:
        self._check_cancelled()
        progress.start(index, stage)
        step = step_registry.STEPS_BY_ID.get(stage.step_id)
        brings_model = step is not None and is_model_step_stage(step)
        handlers: Mapping[str, Any] = (
            IMAGE_MODEL_STEP_HANDLERS if brings_model else IMAGE_STEP_HANDLERS
        )
        if stage.step_id not in handlers:
            raise PipelineStageError(
                stage, f"No implementation registered for step {stage.step_id!r}."
            )
        try:
            import torch

            if brings_model:
                result = self._run_model_step(image, stage, index, data, progress, notes)
            else:
                result = IMAGE_STEP_HANDLERS[stage.step_id](
                    self, image, stage, index, data, progress, notes
                )
            if step is not None and step.output_alpha:
                output_image = _with_matte(image, result, stage, data)
                skip_preview = False
            else:
                output_image = result
                skip_preview = bool(output_image.pop("_skip_preview", False))
                if stage.mask_path:
                    blended = _apply_stage_mask(stage, image["tensor"], output_image["tensor"])
                    output_image = {
                        **output_image,
                        "tensor": torch.from_numpy(np.array(blended, copy=True)),
                    }
            if not skip_preview:
                pixels = (
                    output_image["tensor"]
                    .detach()
                    .cpu()
                    .clamp(0, 1)
                    .mul(255)
                    .round()
                    .byte()
                    .numpy()
                )
                _, height, width = pixels.shape
                progress.preview.submit(
                    job_id=progress.job_id,
                    preview_kind="tile",
                    pixels=pixels,
                    force=True,
                    output_x=0,
                    output_y=0,
                    output_width=int(width),
                    output_height=int(height),
                    image_width=int(width),
                    image_height=int(height),
                    active_tile_size=max(int(width), int(height)),
                    stage_index=index,
                )
            flush_preview = getattr(progress.preview, "flush", None)
            if callable(flush_preview):
                flush_preview()
            progress.complete(index, stage)
            return output_image
        except InterruptedError:
            raise
        except PipelineStageError:
            raise
        except Exception as error:  # noqa: BLE001
            raise PipelineStageError(stage, str(error)) from error

    def _run_model_step(
        self,
        image: dict[str, Any],
        stage: PipelineStage,
        index: int,
        data: Mapping[str, Any],
        progress: ImageJobProgress,
        notes: list[str],
    ) -> Any:
        """Load a step stage's model as a fix's is loaded, run its handler, release it."""
        self._log(f"Running {stage.kind} stage with {stage.model_id}...")
        try:
            info = self.model_adapter.inspect(stage.model_path)
            device, precision = self.engine.load_model(
                stage.model_path,
                data["device"],
                data["precision"],
                info,
                safe_memory=bool(data["safe_memory"]),
            )
            self._check_cancelled()
            model = StepModel(
                path=stage.model_path,
                info=info,
                module=self.engine.active_model,
                device=device,
                precision=precision,
            )
            return IMAGE_MODEL_STEP_HANDLERS[stage.step_id](
                self, image, stage, index, data, progress, notes, model
            )
        finally:
            self.engine.release_model(stage.model_path)
            self.model_adapter.release()

    def _run_postprocess(
        self,
        writer: OutputWriter,
        working_image: dict[str, Any],
        pipeline: ImagePipeline,
        data: Mapping[str, Any],
        progress: ImageJobProgress,
        notes: list[str],
        resources: ExitStack,
        manager: ImageManager,
        *,
        kept_writer: list[OutputWriter | None] | None = None,
    ) -> OutputWriter:
        import torch

        assert pipeline.primary_index is not None
        start_index = pipeline.primary_index + 1 + (1 if pipeline.face is not None else 0)
        pixels = np.asarray(writer.get_array())
        divisor = 65535.0 if pixels.dtype == np.uint16 else 255.0
        post_image = {
            **working_image,
            "tensor": torch.from_numpy(np.array(pixels, copy=True)).float().div_(divisor),
        }
        for offset, stage in enumerate(pipeline.postprocess_stages):
            post_image = self._run_stage(
                post_image, stage, start_index + offset, data, progress, notes
            )
            if not post_image.get("grayscale", True):
                manager.grayscale = False
            if "alpha_image" in post_image:
                manager.alpha_channel = post_image["alpha_image"]
        bit_depth = int(data.get("output_bit_depth") or 8)
        if bit_depth == 16:
            final_pixels = (
                post_image["tensor"]
                .detach()
                .cpu()
                .clamp(0, 1)
                .mul(65535.0)
                .round()
                .to(torch.int32)
                .numpy()
                .astype(np.uint16, copy=False)
            )
            out_dtype = np.uint16
        else:
            final_pixels = (
                post_image["tensor"].detach().cpu().clamp(0, 1).mul(255).round().byte().numpy()
            )
            out_dtype = np.uint8
        out_writer = OutputWriter(
            final_pixels.shape, dtype=out_dtype, temporary_directory=data.get("scratch_directory")
        )
        self._register_writer(resources, out_writer, kept_writer)
        out_writer.mmap[:] = final_pixels
        out_writer.mmap.flush()
        return out_writer

    @staticmethod
    def _register_writer(
        resources: ExitStack,
        writer: OutputWriter,
        kept_writer: list[OutputWriter | None] | None,
    ) -> None:
        if kept_writer is None:
            resources.callback(writer.cleanup)
        else:
            resources.callback(
                lambda w=writer, kept=kept_writer: w.cleanup() if w is not kept[0] else None
            )

    def _primary(
        self,
        image: dict[str, Any],
        pipeline: ImagePipeline,
        data: Mapping[str, Any],
        progress: ImageJobProgress,
        resources: ExitStack,
        *,
        kept_writer: list[OutputWriter | None] | None = None,
    ) -> tuple[OutputWriter, NormalizedModelInfo]:
        primary, face = pipeline.primary, pipeline.face
        height, width = image["tensor"].shape[-2:]
        if primary is None:
            if self.save_pipeline is not None:
                self.save_pipeline.wait_if_large(
                    width * height, int(data.get("output_bit_depth") or 8)
                )
            return self._passthrough_1x(image, data, resources, width, height, kept_writer)
        assert pipeline.primary_index is not None
        progress.start(pipeline.primary_index, primary, span=1 + int(face is not None))
        if face is not None:
            progress.emit_start(pipeline.primary_index + 1, face)
        self._log("Loading primary model...")
        info = self._inspect(primary.model_path, primary)
        if int(data.get("output_scale") or 1) > info.scale:
            raise PipelineStageError(
                primary,
                f"Output scale must be between 1 and the model's native {info.scale}× scale.",
            )
        if data.get("output_scale") == 1 and info.scale > 1:
            if self.save_pipeline is not None:
                self.save_pipeline.wait_if_large(
                    width * height, int(data.get("output_bit_depth") or 8)
                )
            progress.complete(pipeline.primary_index, primary)
            return self._passthrough_1x(image, data, resources, width, height, kept_writer)
        if self.save_pipeline is not None:
            self.save_pipeline.wait_if_large(
                width * info.scale * height * info.scale,
                int(data.get("output_bit_depth") or 8),
            )
        # Before any memmap exists: a photo that cannot be written must not start.
        require_image_room(data, width, height, info.scale, image.get("alpha_image") is not None)
        face_path = data.get("face_model_path")
        face_info = self._inspect(face_path, face or primary) if face_path else None
        if face_info is not None:
            if face_info.scale != info.scale:
                raise PipelineStageError(
                    face or primary, "Face and primary checkpoints must have the same native scale."
                )
            if face_info.out_channels != info.out_channels:
                raise PipelineStageError(
                    face or primary,
                    "Face and primary checkpoints must have compatible output channels.",
                )
        try:
            writer = None
            if face_path and face_info is not None:
                writer = self._process_faces(
                    image,
                    primary,
                    info,
                    face_path,
                    face_info,
                    data,
                    progress,
                    resources,
                    kept_writer,
                )
            if writer is None:
                writer = self._process_image(
                    image, primary, info, data, progress, keep_partial=self.keep_partial
                )
                self._register_writer(resources, writer, kept_writer)
            if primary.mask_path:
                blended = _apply_stage_mask(primary, image["tensor"], writer.get_array())
                target = getattr(writer, "mmap", None)
                if target is not None:
                    target[:] = blended
                    target.flush()
                else:
                    writer.get_array()[:] = blended
        except TilesStopped as stopped:
            stopped.info = info
            raise
        except InterruptedError:
            raise
        except Exception as error:  # noqa: BLE001
            raise PipelineStageError(face or primary, str(error)) from error
        progress.complete(pipeline.primary_index, primary)
        if face is not None:
            progress.complete(pipeline.primary_index + 1, face)
        return writer, info

    def _inspect(self, path: str, stage: PipelineStage) -> NormalizedModelInfo:
        try:
            return self.model_adapter.inspect(path)
        except Exception as error:  # noqa: BLE001
            raise PipelineStageError(stage, str(error)) from error

    def _process_image(
        self,
        image: dict[str, Any],
        stage: PipelineStage,
        info: NormalizedModelInfo,
        data: Mapping[str, Any],
        progress: ImageJobProgress,
        halo: int | None = None,
        keep_partial: Callable[[], bool] | None = None,
    ) -> OutputWriter:
        bit_depth = int(data.get("output_bit_depth") or 8)
        return self.engine.process_image(
            img_data=image,
            model_info=info,
            model_path=stage.model_path,
            device_str=data["device"],
            precision_str=data["precision"],
            tile_size=data["tile_size"],
            halo=data["halo"] if halo is None else halo,
            cancel_event=self.cancel_event,
            progress_callback=progress.update,
            safe_memory=data["safe_memory"],
            tile_callback=progress.tile,
            temporary_directory=data.get("scratch_directory"),
            **({"keep_partial": keep_partial} if keep_partial is not None else {}),
            **({"output_bit_depth": 16} if bit_depth == 16 else {}),
        )

    def _save_finished_part(
        self,
        job_id: str,
        data: Mapping[str, Any],
        manager: ImageManager,
        image: dict[str, Any],
        working_image: dict[str, Any],
        stopped: TilesStopped,
        started_at: float,
    ) -> None:
        """Save a stopped photo: its finished tiles, the rest a plain resize of the image
        the model was given, under `<name>_partial`."""
        info = stopped.info
        assert info is not None
        self._log("Saving the finished part...")
        fill_unfinished(stopped.writer.get_array(), stopped.finished, working_image["tensor"])
        partial = {**data, "output_path": partial_output_path(str(data["output_path"]))}
        # The stop is what ends this job; the save itself must not be refused for it.
        notes = save_output(manager, stopped.writer, image, info, partial, threading.Event())
        output_scale = int(data.get("output_scale") or info.scale)
        self.emit(
            JobCompleted(
                job_id=job_id,
                elapsed_seconds=time.monotonic() - started_at,
                output_path=partial["output_path"],
                notes=" ".join([*notes, *self._take_engine_notes()]),
                partial=True,
                finished_tiles=saved_tiles(stopped.finished, info.scale, output_scale),
                total_tiles=stopped.total,
            )
        )

    def _process_faces(
        self,
        image: dict[str, Any],
        primary: PipelineStage,
        info: NormalizedModelInfo,
        face_path: str,
        face_info: NormalizedModelInfo,
        data: Mapping[str, Any],
        progress: ImageJobProgress,
        resources: ExitStack,
        kept_writer: list[OutputWriter | None] | None = None,
    ) -> OutputWriter | None:
        from localsr.core.face_detection import detect_faces
        from localsr.core.video_pipeline import process_frame_face_aware

        tensor = image["tensor"]
        rgb = tensor.permute(1, 2, 0).clamp(0, 1).mul(255).byte().numpy()
        mask = detect_faces(rgb, cancel_event=self.cancel_event)
        if not mask.has_faces:
            self._log("No faces detected; proceeding with the primary model.")
            return None
        self._log(
            f"Detected {len(mask.boxes)} face(s); applying fidelity-controlled face restoration."
        )
        bit_depth = int(data.get("output_bit_depth") or 8)
        result = process_frame_face_aware(
            engine=self.engine,
            img_tensor=tensor,
            face_mask=mask,
            general_model_path=primary.model_path,
            face_model_path=face_path,
            general_model_info=info,
            face_model_info=face_info,
            device_str=data["device"],
            precision_str=data["precision"],
            tile_size=data["tile_size"],
            halo=data["halo"],
            cancel_event=self.cancel_event,
            safe_memory=data["safe_memory"],
            face_fidelity=float(data.get("face_fidelity", 0.7)),
            progress_callback=progress.update,
            tile_callback=progress.tile,
            **({"bit_depth": 16} if bit_depth == 16 else {}),
        )
        writer = OutputWriter(
            result.shape,
            dtype=result.dtype,
            temporary_directory=data.get("scratch_directory"),
        )
        self._register_writer(resources, writer, kept_writer)
        writer.mmap[:] = result
        writer.mmap.flush()
        return writer

    def _passthrough_1x(
        self,
        image: dict[str, Any],
        data: Mapping[str, Any],
        resources: ExitStack,
        width: int,
        height: int,
        kept_writer: list[OutputWriter | None] | None = None,
    ) -> tuple[OutputWriter, NormalizedModelInfo]:
        """Package 1× working pixels for atomic save without running an upscaler."""
        require_image_room(data, width, height, 1, image.get("alpha_image") is not None)
        bit_depth = int(data.get("output_bit_depth") or 8)
        if bit_depth == 16:
            import torch

            pixels = (
                image["tensor"]
                .clamp(0, 1)
                .mul(65535.0)
                .round()
                .to(torch.int32)
                .numpy()
                .astype(np.uint16, copy=False)
            )
            out_dtype = np.uint16
        else:
            pixels = image["tensor"].clamp(0, 1).mul(255).round().byte().numpy()
            out_dtype = np.uint8
        writer = OutputWriter(
            pixels.shape, dtype=out_dtype, temporary_directory=data.get("scratch_directory")
        )
        self._register_writer(resources, writer, kept_writer)
        writer.mmap[:] = pixels
        writer.mmap.flush()
        channels = int(pixels.shape[0])
        info = NormalizedModelInfo("Identity", 1, channels, channels, True, False, 1, 1, "", [])
        return writer, info

    def _save(
        self,
        manager: ImageManager,
        writer: OutputWriter,
        image: dict[str, Any],
        info: NormalizedModelInfo,
        data: Mapping[str, Any],
    ) -> list[str]:
        return save_output(manager, writer, image, info, data, self._discard_signal())

    def _discard_signal(self) -> DiscardOnStop:
        """Set when a stop discards; a stop that keeps lets a finished image be saved."""
        return DiscardOnStop(self.cancel_event, self.keep_partial or False)

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Cancelled between pipeline stages.")

    def _log(self, message: str) -> None:
        self.emit(LogMessage(level="info", message=message))
