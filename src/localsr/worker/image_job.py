"""Execute an image recipe while owning its previews and temporary outputs."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from typing import Any

import numpy as np

from localsr.core.disk_space import require_image_room
from localsr.core.image_io import ImageManager, require_output_format
from localsr.core.inference import InferenceEngine, UnstableRestorationError
from localsr.core.live_preview import LatestPreviewEncoder, PreviewPacket
from localsr.core.model_adapter import ModelAdapter, NormalizedModelInfo
from localsr.core.model_catalog import CATALOG_BY_ID
from localsr.core.output_writer import OutputWriter, cleanup_stale_work_files
from localsr.core.pipeline import (
    ImagePipeline,
    PipelineStage,
    PipelineStageError,
    resolve_image_pipeline,
)
from localsr.protocol.messages import (
    EngineStatus,
    JobCancelled,
    JobCompleted,
    LivePreviewWarning,
    LogMessage,
    WarningMessage,
    WorkerMessage,
)
from localsr.worker.job_progress import ImageJobProgress

# How a skip note names the problem a fix was chosen for.
_FIX_NAMES = {"blur": "blur", "jpeg": "JPEG", "noise": "noise"}


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
    )
    return list(manager.notes)


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
    ) -> None:
        self.engine = engine
        self.model_adapter = model_adapter
        self.cancel_event = cancel_event
        self.emit = emit
        self.emit_preview = emit_preview
        self.preview_factory = preview_factory

    def run(self, job_id: str, data: Mapping[str, Any]) -> None:
        require_output_format(str(data["output_format"]))
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
        face_path = data.get("face_model_path")
        self.engine.release_models_except(
            {pipeline.preprocess_stages[0].model_path}
            if pipeline.preprocess_stages
            else {stage.model_path for stage in pipeline.stages}
            | ({face_path} if face_path else set())
        )
        scratch = data.get("scratch_directory")
        if scratch:
            cleanup_stale_work_files(scratch)

        try:
            # Ownership begins before image decoding: corrupt media must also
            # release an encoder, and failed writes must release every memmap.
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
                image = manager.load(data["image_path"])
                self._log("Starting inference pipeline...")
                processing_started_at = time.monotonic()
                working_image = image
                notes: list[str] = []
                for index, stage in enumerate(pipeline.preprocess_stages):
                    working_image = self._preprocess(
                        working_image, stage, index, data, progress, notes
                    )
                self._check_cancelled()
                writer, info = self._primary(working_image, pipeline, data, progress, resources)
                self._check_cancelled()
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

    def _preprocess(
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
        self._log(f"Running {stage.kind} stage with {stage.model_id}...")
        try:
            with ExitStack() as resources:
                info = self.model_adapter.inspect(stage.model_path)
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
                writer = self._process_image(image, stage, info, data, progress, halo=halo)
                resources.callback(writer.cleanup)
                self._check_cancelled()
                import torch

                # Copy before releasing the memmap; metadata and alpha remain
                # attached to the original image until the final atomic save.
                restored = np.asarray(writer.get_array())
                if restored.shape != tuple(image["tensor"].shape):
                    raise ValueError(
                        "A fix must preserve the working image dimensions and channels."
                    )
                working_image = {
                    **image,
                    "tensor": torch.from_numpy(np.array(restored, copy=True)).float().div_(255.0),
                }
                progress.complete(index, stage)
                return working_image
        except InterruptedError:
            raise
        except UnstableRestorationError as error:
            # The fix before the main model is optional. A model that cannot handle this
            # image (NAFNet's deblur runs away on line art, screentone and already sharp
            # pictures) must not stop the job: the image goes on without this fix, the job
            # says so, and nothing damaged is kept.
            model = CATALOG_BY_ID.get(stage.model_id)
            reason = str(error).split(". ")[0].rstrip(".")
            problem = _FIX_NAMES.get(stage.fix or "", "")
            note = (
                f"The {problem + ' ' if problem else ''}fix "
                f"({model.display_name if model else stage.model_id}) was skipped for this "
                f"image; the next step used the picture without it. Reason: {reason}."
            )
            self._log(f"{note} ({error})")
            self.emit(WarningMessage(message=note))
            notes.append(note)
            progress.complete(index, stage)
            return image
        except Exception as error:  # noqa: BLE001
            raise PipelineStageError(stage, str(error)) from error
        finally:
            self.engine.release_model(stage.model_path)
            self.model_adapter.release()

    def _primary(
        self,
        image: dict[str, Any],
        pipeline: ImagePipeline,
        data: Mapping[str, Any],
        progress: ImageJobProgress,
        resources: ExitStack,
    ) -> tuple[OutputWriter, NormalizedModelInfo]:
        primary, face = pipeline.primary, pipeline.face
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
        # Before any memmap exists: a photo that cannot be written must not start.
        height, width = image["tensor"].shape[-2:]
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
                    image, primary, info, face_path, face_info, data, progress, resources
                )
            if writer is None:
                writer = self._process_image(image, primary, info, data, progress)
                resources.callback(writer.cleanup)
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
    ) -> OutputWriter:
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
        )
        writer = OutputWriter(
            result.shape, dtype=np.uint8, temporary_directory=data.get("scratch_directory")
        )
        resources.callback(writer.cleanup)
        writer.mmap[:] = result
        writer.mmap.flush()
        return writer

    def _save(
        self,
        manager: ImageManager,
        writer: OutputWriter,
        image: dict[str, Any],
        info: NormalizedModelInfo,
        data: Mapping[str, Any],
    ) -> list[str]:
        return save_output(manager, writer, image, info, data, self.cancel_event)

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Cancelled between pipeline stages.")

    def _log(self, message: str) -> None:
        self.emit(LogMessage(level="info", message=message))
