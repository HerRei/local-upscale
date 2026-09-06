"""Execute an image recipe while owning its previews and temporary outputs."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from typing import Any

import numpy as np

from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.live_preview import LatestPreviewEncoder, PreviewPacket
from localsr.core.model_adapter import ModelAdapter, NormalizedModelInfo
from localsr.core.output_writer import OutputWriter, cleanup_stale_work_files
from localsr.core.pipeline import (
    ImagePipeline,
    PipelineStage,
    PipelineStageError,
    resolve_image_pipeline,
)
from localsr.protocol.messages import (
    JobCancelled,
    JobCompleted,
    LivePreviewWarning,
    LogMessage,
    WorkerMessage,
)
from localsr.worker.job_progress import ImageJobProgress


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
        started_at = time.monotonic()
        pipeline = resolve_image_pipeline(
            raw_stages=data.get("stages"),
            model_path=str(data["model_path"]),
            output_scale=data.get("output_scale"),
            face_model_path=data.get("face_model_path"),
            face_fidelity=float(data.get("face_fidelity", 0.7)),
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
                if pipeline.preprocess is not None:
                    working_image = self._preprocess(image, pipeline.preprocess, data, progress)
                self._check_cancelled()
                writer, info = self._primary(working_image, pipeline, data, progress, resources)
                self._check_cancelled()
                self._log("Writing final output...")
                self._save(manager, writer, image, info, data)
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
                )
            )
        except InterruptedError:
            self.emit(JobCancelled(job_id=job_id))

    def _preprocess(
        self,
        image: dict[str, Any],
        stage: PipelineStage,
        data: Mapping[str, Any],
        progress: ImageJobProgress,
    ) -> dict[str, Any]:
        self._check_cancelled()
        progress.start(0, stage)
        self._log(f"Running {stage.kind} stage with {stage.model_id}...")
        try:
            with ExitStack() as resources:
                info = self.model_adapter.inspect(stage.model_path)
                if info.scale != 1:
                    raise ValueError(
                        "A preprocessing restoration checkpoint must have native scale 1×."
                    )
                writer = self._process_image(image, stage, info, data, progress)
                resources.callback(writer.cleanup)
                self._check_cancelled()
                import torch

                # Copy before releasing the memmap; metadata and alpha remain
                # attached to the original image until the final atomic save.
                restored = np.asarray(writer.get_array())
                working_image = {
                    **image,
                    "tensor": torch.from_numpy(np.array(restored, copy=True)).float().div_(255.0),
                }
                progress.complete(0, stage)
                return working_image
        except InterruptedError:
            raise
        except Exception as error:  # noqa: BLE001
            raise PipelineStageError(stage, str(error)) from error
        finally:
            self.engine.release_model(stage.model_path)

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
    ) -> OutputWriter:
        return self.engine.process_image(
            img_data=image,
            model_info=info,
            model_path=stage.model_path,
            device_str=data["device"],
            precision_str=data["precision"],
            tile_size=data["tile_size"],
            halo=data["halo"],
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
            self._log("No faces detected; the primary model completed unchanged.")
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

    @staticmethod
    def _save(
        manager: ImageManager,
        writer: OutputWriter,
        image: dict[str, Any],
        info: NormalizedModelInfo,
        data: Mapping[str, Any],
    ) -> None:
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
        )

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Cancelled between pipeline stages.")

    def _log(self, message: str) -> None:
        self.emit(LogMessage(level="info", message=message))
