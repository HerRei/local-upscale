import base64
import gc
import io
import json
import os
import queue
import sys
import threading
import time
import traceback

import numpy as np
from PIL import Image, ImageOps

# Set MPS memory limits before torch is imported
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.62")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.52")

from localsr.core.hardware import get_capability_report, get_memory_snapshot
from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import ModelAdapter
from localsr.core.video_pipeline import VideoJobConfig, run_video_job
from localsr.protocol.messages import (
    CapabilitiesInfo,
    FaceDetectionUnavailable,
    FacesDetected,
    JobCancelled,
    JobCompleted,
    JobFailed,
    JobStarted,
    LogMessage,
    ModelInfo,
    PreviewFailed,
    PreviewReady,
    ProgressUpdate,
    TileUpdate,
    VideoFrameCompleted,
    VideoFrameStarted,
    VideoJobCompleted,
    WarningMessage,
    WorkerReady,
)

# Thread-safe writing to stdout
print_lock = threading.Lock()


def send_message(msg):
    with print_lock:
        sys.stdout.write(msg.to_json() + "\n")
        sys.stdout.flush()


def _encode_chw_jpeg(image, max_dimension: int, quality: int = 78) -> str:
    if hasattr(image, "detach"):
        array = image.detach().to("cpu").clamp(0, 1).mul(255).byte().numpy()
    else:
        array = image
    if array.ndim != 3:
        raise ValueError("Preview data must use channel-height-width layout.")
    if array.shape[0] == 1:
        array = array.repeat(3, axis=0)
    if array.shape[0] < 3:
        raise ValueError("Preview data must contain at least one or three channels.")
    rgb = array[:3].transpose(1, 2, 0)
    preview = Image.fromarray(rgb)
    maximum = max(64, min(2048, int(max_dimension)))
    preview.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    preview.save(output, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(output.getvalue()).decode("ascii")


class WorkerServer:
    def __init__(self):
        self.message_queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.state_lock = threading.Lock()
        self.running = True
        self.active_job_id = None
        self.pending_cancel_job_ids: set[str] = set()
        self.model_adapter = ModelAdapter()
        self.engine = InferenceEngine(self.model_adapter)

    def _request_cancel(self, requested_job_id: str) -> None:
        """Remember cancellation even if the matching job is still queued."""
        if not requested_job_id:
            return
        with self.state_lock:
            self.pending_cancel_job_ids.add(requested_job_id)
            if requested_job_id == self.active_job_id:
                self.cancel_event.set()

    def _activate_job(self, job_id: str) -> None:
        with self.state_lock:
            self.active_job_id = job_id
            if job_id in self.pending_cancel_job_ids:
                self.cancel_event.set()
            else:
                self.cancel_event.clear()

    def _deactivate_job(self, job_id: str) -> None:
        with self.state_lock:
            self.active_job_id = None
            self.pending_cancel_job_ids.discard(job_id)
            self.cancel_event.clear()

    def reader_thread_func(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as error:
                send_message(
                    LogMessage(level="error", message=f"JSON parse error in worker: {error}")
                )
                continue

            if not isinstance(msg, dict):
                send_message(LogMessage(level="error", message="Worker request must be an object."))
                continue

            self.message_queue.put(msg)

            request_type = msg.get("type")
            if request_type == "shutdown_request":
                self.cancel_event.set()
            elif request_type == "cancel_request":
                requested_job_id = str(msg.get("data", {}).get("job_id") or "")
                self._request_cancel(requested_job_id)

        # On sys.stdin EOF (pipe closed), push shutdown_request so main loop exits cleanly
        self.cancel_event.set()
        self.message_queue.put({"type": "shutdown_request"})

    def run(self):
        reader_thread = threading.Thread(target=self.reader_thread_func, daemon=True)
        reader_thread.start()

        send_message(WorkerReady())
        send_message(LogMessage(level="info", message="Worker started and ready."))

        while self.running:
            try:
                # Wait for messages
                msg = self.message_queue.get(timeout=1.0)
                req_type = msg.get("type")
                data = msg.get("data", {})

                if req_type == "shutdown_request":
                    self.running = False
                    break

                elif req_type == "inspect_request":
                    try:
                        info = self.model_adapter.inspect(data["model_path"])
                        send_message(
                            ModelInfo(
                                architecture=info.architecture,
                                scale=info.scale,
                                in_channels=info.in_channels,
                                out_channels=info.out_channels,
                                tiling_supported=info.tiling_supported,
                                half_supported=info.half_supported,
                                size_requirements_min=info.size_requirements_min,
                                size_requirements_mult=info.size_requirements_mult,
                                filename=info.filename,
                                warnings=info.warnings,
                                size_requirements_square=info.size_requirements_square,
                                parameter_count=info.parameter_count,
                                model_file_size=info.model_file_size,
                            )
                        )
                    # Model loaders may raise architecture-specific exceptions.
                    except Exception as e:  # noqa: BLE001
                        send_message(
                            LogMessage(level="error", message=f"Failed to inspect model: {e}")
                        )
                        send_message(WarningMessage(message=f"Model inspection failed: {e}"))

                elif req_type == "capabilities_request":
                    report = get_capability_report()
                    send_message(CapabilitiesInfo(**report))

                elif req_type == "preview_request":
                    image_path = str(data.get("image_path", ""))
                    try:
                        preview_data = ImageManager().load(image_path)
                        tensor = preview_data["tensor"]
                        _, height, width = tensor.shape
                        send_message(
                            PreviewReady(
                                image_path=image_path,
                                width=int(width),
                                height=int(height),
                                jpeg_base64=_encode_chw_jpeg(
                                    tensor,
                                    int(data.get("max_dimension", 1600)),
                                    quality=82,
                                ),
                            )
                        )
                        del preview_data
                        gc.collect()
                    except Exception as error:  # noqa: BLE001
                        send_message(PreviewFailed(image_path=image_path, error_message=str(error)))

                elif req_type == "cancel_request":
                    # The reader has already signalled an active or queued job.
                    # Once this ordered message reaches the main loop with no
                    # matching active job, its pending marker can be discarded.
                    requested_job_id = str(data.get("job_id") or "")
                    with self.state_lock:
                        if requested_job_id != self.active_job_id:
                            self.pending_cancel_job_ids.discard(requested_job_id)

                elif req_type == "detect_faces_request":
                    image_path = str(data.get("image_path", ""))
                    try:
                        from localsr.core.face_detection import detect_faces

                        img = Image.open(image_path)
                        img = ImageOps.exif_transpose(img)
                        rgb = np.array(img.convert("RGB"))
                        result = detect_faces(rgb)
                        boxes = [
                            {
                                "x": int(box.x),
                                "y": int(box.y),
                                "w": int(box.w),
                                "h": int(box.h),
                                "confidence": float(box.confidence),
                            }
                            for box in result.boxes
                        ]
                        send_message(FacesDetected(image_path=image_path, boxes=boxes))
                    except ImportError:
                        send_message(
                            FaceDetectionUnavailable(
                                image_path=image_path,
                                message="MediaPipe is not installed. Face detection is unavailable.",
                            )
                        )
                    except Exception as error:  # noqa: BLE001
                        send_message(
                            FaceDetectionUnavailable(
                                image_path=image_path,
                                message=f"Face detection failed: {error}",
                            )
                        )

                elif req_type == "job_request":
                    if self.active_job_id is not None:
                        send_message(
                            JobFailed(
                                job_id=data["job_id"], error_message="A job is already running."
                            )
                        )
                        continue

                    job_id = data["job_id"]
                    self._activate_job(job_id)

                    try:
                        send_message(JobStarted(job_id=job_id))
                        self._run_job(job_id, data)
                    # The process boundary must turn any inference/I/O failure
                    # into a job_failed message instead of killing the worker.
                    except Exception as e:  # noqa: BLE001
                        # Ensure we always clear up
                        traceback.print_exc(file=sys.stderr)
                        send_message(JobFailed(job_id=job_id, error_message=str(e)))
                    finally:
                        self._deactivate_job(job_id)
                        self.model_adapter.release()
                        # Release the face model from the engine cache if it
                        # was loaded for this job. The general model stays
                        # cached for the next image.
                        face_path = data.get("face_model_path")
                        if face_path:
                            self.engine.release_model(face_path)

                elif req_type == "video_job_request":
                    if self.active_job_id is not None:
                        send_message(
                            JobFailed(
                                job_id=data["job_id"], error_message="A job is already running."
                            )
                        )
                        continue

                    job_id = data["job_id"]
                    self._activate_job(job_id)

                    try:
                        send_message(JobStarted(job_id=job_id))
                        self._run_video_job(job_id, data)
                    except Exception as e:  # noqa: BLE001
                        traceback.print_exc(file=sys.stderr)
                        send_message(JobFailed(job_id=job_id, error_message=str(e)))
                    finally:
                        self._deactivate_job(job_id)
                        self.model_adapter.release()
                        self.engine.release_model()

            except queue.Empty:
                continue
            except KeyboardInterrupt:
                break
            # Keep the long-lived worker responsive after an unexpected request.
            except Exception as e:  # noqa: BLE001
                send_message(LogMessage(level="error", message=f"Worker loop error: {e}"))

    def _run_job(self, job_id, data):
        job_started_at = time.monotonic()
        inference_started_at = None
        processing_call_started_at = None
        last_progress_at = None
        last_completed = 0
        smoothed_seconds_per_tile = None
        last_memory_sample_at = 0.0
        memory_snapshot = {}

        def progress_cb(completed, total, active_tile_size):
            nonlocal last_completed
            nonlocal last_memory_sample_at
            nonlocal last_progress_at
            nonlocal memory_snapshot
            nonlocal smoothed_seconds_per_tile

            now = time.monotonic()
            elapsed = now - (inference_started_at or now)
            seconds_per_tile = elapsed / max(completed, 1)
            estimated_remaining = seconds_per_tile * max(0, total - completed)
            last_progress_at = now
            last_completed = completed

            if now - last_memory_sample_at >= 1.0 or completed == total:
                try:
                    memory_snapshot = get_memory_snapshot(data["device"])
                except (OSError, RuntimeError, ValueError):
                    memory_snapshot = {}
                last_memory_sample_at = now

            send_message(
                ProgressUpdate(
                    job_id=job_id,
                    completed_tiles=completed,
                    total_tiles=total,
                    percentage=completed / total * 100.0,
                    elapsed_seconds=elapsed,
                    estimated_remaining_seconds=estimated_remaining,
                    active_tile_size=active_tile_size,
                    device_free_memory=memory_snapshot.get("device_free_memory", 0),
                    device_allocated_memory=memory_snapshot.get("device_allocated_memory", 0),
                    system_ram_available=memory_snapshot.get("system_ram_available", 0),
                    system_memory_pressure_percent=memory_snapshot.get(
                        "system_memory_pressure_percent", 0.0
                    ),
                    system_memory_pressure_level=memory_snapshot.get(
                        "system_memory_pressure_level", "unknown"
                    ),
                    system_compressed_memory=memory_snapshot.get("system_compressed_memory", 0),
                    system_swap_used=memory_snapshot.get("system_swap_used", 0),
                    mps_tensor_allocated_memory=memory_snapshot.get(
                        "mps_tensor_allocated_memory", 0
                    ),
                    mps_driver_allocated_memory=memory_snapshot.get(
                        "mps_driver_allocated_memory", 0
                    ),
                    mps_recommended_max_memory=memory_snapshot.get("mps_recommended_max_memory", 0),
                )
            )

        def tile_cb(
            phase,
            tile,
            tile_data,
            completed,
            total,
            output_width,
            output_height,
            active_tile_size,
        ):
            nonlocal inference_started_at
            if phase == "started" and inference_started_at is None:
                # Model loading happens before the first tile. Starting the ETA
                # clock here prevents that one-off cost from being multiplied by
                # every remaining tile.
                inference_started_at = time.monotonic()
            jpeg_base64 = ""
            if phase == "completed" and tile_data is not None:
                try:
                    jpeg_base64 = _encode_chw_jpeg(tile_data, 192, quality=72)
                except (OSError, TypeError, ValueError):
                    jpeg_base64 = ""
            send_message(
                TileUpdate(
                    job_id=job_id,
                    phase=phase,
                    completed_tiles=completed,
                    total_tiles=total,
                    output_x=int(tile.out_x) if tile is not None else 0,
                    output_y=int(tile.out_y) if tile is not None else 0,
                    output_width=int(tile.out_w) if tile is not None else 0,
                    output_height=int(tile.out_h) if tile is not None else 0,
                    image_width=int(output_width),
                    image_height=int(output_height),
                    active_tile_size=int(active_tile_size),
                    jpeg_base64=jpeg_base64,
                )
            )

        send_message(LogMessage(level="info", message="Loading model..."))
        info = self.model_adapter.inspect(data["model_path"])

        face_model_path = data.get("face_model_path")
        face_info = None
        if face_model_path:
            try:
                face_info = self.model_adapter.inspect(face_model_path)
            except Exception as error:  # noqa: BLE001
                send_message(WarningMessage(message=f"Could not inspect face model: {error}"))
                face_model_path = None

        # Load image (this separates RGB and Alpha)
        send_message(LogMessage(level="info", message="Loading image..."))
        im_manager = ImageManager()
        img_data = im_manager.load(data["image_path"])

        out_file = None
        try:
            send_message(LogMessage(level="info", message="Starting inference..."))
            processing_call_started_at = time.monotonic()

            if face_model_path and face_info:
                # Face-aware image processing: detect faces, then use
                # per-tile model selection with boundary blending.
                from localsr.core.face_detection import detect_faces
                from localsr.core.video_pipeline import process_frame_face_aware

                # Convert the loaded image tensor to an RGB array for detection.
                tensor = img_data["tensor"]
                _, img_h, img_w = tensor.shape
                rgb_for_detection = tensor.permute(1, 2, 0).clamp(0, 1).mul(255).byte().numpy()
                face_mask = detect_faces(rgb_for_detection)

                if face_mask.has_faces:
                    send_message(
                        LogMessage(
                            level="info",
                            message=f"Detected {len(face_mask.boxes)} face(s). Using face-aware restoration.",
                        )
                    )
                    # process_frame_face_aware returns an (C, H*scale, W*scale) numpy array.
                    # We wrap it in a temporary OutputWriter-like object so the save path works.
                    from localsr.core.output_writer import OutputWriter

                    face_aware_result = process_frame_face_aware(
                        engine=self.engine,
                        img_tensor=tensor,
                        face_mask=face_mask,
                        general_model_path=data["model_path"],
                        face_model_path=face_model_path,
                        general_model_info=info,
                        face_model_info=face_info,
                        device_str=data["device"],
                        precision_str=data["precision"],
                        tile_size=data["tile_size"],
                        halo=data["halo"],
                        cancel_event=self.cancel_event,
                        safe_memory=data["safe_memory"],
                        progress_callback=progress_cb,
                        tile_callback=tile_cb,
                    )
                    # Write the result into an OutputWriter so the existing
                    # save logic can handle it.
                    writer = OutputWriter(face_aware_result.shape, dtype=np.uint8)
                    writer.mmap[:] = face_aware_result
                    writer.mmap.flush()
                    out_file = writer
                else:
                    send_message(
                        LogMessage(level="info", message="No faces detected. Using general model.")
                    )
                    out_file = self.engine.process_image(
                        img_data=img_data,
                        model_info=info,
                        model_path=data["model_path"],
                        device_str=data["device"],
                        precision_str=data["precision"],
                        tile_size=data["tile_size"],
                        halo=data["halo"],
                        cancel_event=self.cancel_event,
                        progress_callback=progress_cb,
                        safe_memory=data["safe_memory"],
                        tile_callback=tile_cb,
                    )
            else:
                out_file = self.engine.process_image(
                    img_data=img_data,
                    model_info=info,
                    model_path=data["model_path"],
                    device_str=data["device"],
                    precision_str=data["precision"],
                    tile_size=data["tile_size"],
                    halo=data["halo"],
                    cancel_event=self.cancel_event,
                    progress_callback=progress_cb,
                    safe_memory=data["safe_memory"],
                    tile_callback=tile_cb,
                )

            if self.cancel_event.is_set():
                send_message(JobCancelled(job_id=job_id))
                return

            send_message(LogMessage(level="info", message="Writing final output..."))
            im_manager.save(
                out_file,
                data["output_path"],
                format=data["output_format"],
                quality=data["jpeg_quality"],
                preserve_metadata=data["preserve_metadata"],
                icc_profile=img_data.get("icc_profile"),
                safe_exif=img_data.get("safe_exif", {}),
                scale=info.scale,
                output_scale=int(data.get("output_scale") or info.scale),
            )

            if out_file is not None:
                out_file.cleanup()
                out_file = None

            completed_at = time.monotonic()
            send_message(
                JobCompleted(
                    job_id=job_id,
                    elapsed_seconds=completed_at - job_started_at,
                    inference_seconds=completed_at
                    - (inference_started_at or processing_call_started_at),
                    output_path=data["output_path"],
                )
            )

        except InterruptedError:
            send_message(JobCancelled(job_id=job_id))
        finally:
            if out_file is not None:
                out_file.cleanup()

    def _run_temporal_video_job(self, job_id, data, engine_factory):
        """Run a clip-based temporal engine over the video.

        The engine contract: construct with (bundle_dir, device, precision),
        then process_clip(list[HxWx3 uint8]) -> list[HxWx3 uint8]. Clip
        windowing and overlap stitching live in localsr.core.temporal so
        every temporal family shares one boundary behavior.
        """
        raise NotImplementedError(
            "Temporal video engines are wired end-to-end but no engine is "
            "vendored in this build yet."
        )

    def _run_video_job(self, job_id, data):
        # Temporal engines route before the Spandrel inspect: their bundles
        # are not single-image checkpoints. An engine this build cannot
        # serve fails the job with a status-bar-ready message instead of a
        # stack trace, and frame-by-frame stays available.
        from localsr.core.video_engines import resolve_video_engine

        model_kind = str(data.get("model_kind", "spandrel_image"))
        engine_factory = resolve_video_engine(model_kind)
        if engine_factory is not None:
            self._run_temporal_video_job(job_id, data, engine_factory)
            return

        send_message(LogMessage(level="info", message="Inspecting model for video job..."))
        info = self.model_adapter.inspect(data["model_path"])

        face_info = None
        face_model_path = data.get("face_model_path")
        if face_model_path:
            try:
                face_info = self.model_adapter.inspect(face_model_path)
            except Exception as error:  # noqa: BLE001
                send_message(
                    WarningMessage(
                        message=f"Could not inspect face model, falling back to general only: {error}"
                    )
                )
                face_model_path = None

        config = VideoJobConfig(
            video_path=data["video_path"],
            model_path=data["model_path"],
            output_video_path=data["output_video_path"],
            model_info=info,
            device_str=data["device"],
            precision_str=data["precision"],
            tile_size=int(data["tile_size"]),
            halo=int(data["halo"]),
            safe_memory=bool(data.get("safe_memory", True)),
            container=str(data.get("container", "mp4")),
            crf=int(data.get("crf", 18)),
            start_frame=data.get("start_frame"),
            end_frame=data.get("end_frame"),
            fps_override=data.get("fps"),
            face_model_path=face_model_path,
            face_model_info=face_info,
            deflicker=bool(data.get("deflicker", False)),
            deflicker_window=int(data.get("deflicker_window", 3)),
        )

        def frame_started(frame_index: int, total_frames: int) -> None:
            send_message(
                VideoFrameStarted(
                    job_id=job_id,
                    frame_index=int(frame_index),
                    total_frames=int(total_frames),
                )
            )

        def frame_completed(frame_index: int, total_frames: int, jpeg_b64: str) -> None:
            # The thumbnail carrier. ETA is sent separately by progress_cb to
            # avoid duplicating the elapsed-time calculation here.
            send_message(
                VideoFrameCompleted(
                    job_id=job_id,
                    frame_index=int(frame_index),
                    total_frames=int(total_frames),
                    frames_processed=0,
                    elapsed_seconds=0.0,
                    estimated_remaining_seconds=0.0,
                    jpeg_base64=jpeg_b64,
                )
            )

        def progress_cb(frames_done: int, total_frames: int, elapsed: float) -> None:
            if total_frames > 0:
                per_frame = elapsed / max(frames_done, 1)
                remaining = per_frame * max(0, total_frames - frames_done)
            else:
                remaining = 0.0
            # Re-send with the real progress numbers. frame_completed above is
            # the per-frame thumbnail carrier; this one carries ETA.
            send_message(
                VideoFrameCompleted(
                    job_id=job_id,
                    frame_index=0,
                    total_frames=int(total_frames),
                    frames_processed=int(frames_done),
                    elapsed_seconds=float(elapsed),
                    estimated_remaining_seconds=float(remaining),
                    jpeg_base64="",
                )
            )

        send_message(LogMessage(level="info", message="Starting video inference..."))
        try:
            result = run_video_job(
                config=config,
                engine=self.engine,
                cancel_event=self.cancel_event,
                frame_started_cb=frame_started,
                frame_completed_cb=frame_completed,
                progress_cb=progress_cb,
            )
        except InterruptedError:
            send_message(JobCancelled(job_id=job_id))
            return

        if self.cancel_event.is_set():
            send_message(JobCancelled(job_id=job_id))
            return

        send_message(
            VideoJobCompleted(
                job_id=job_id,
                output_path=result.output_path,
                frames_processed=result.frames_processed,
                elapsed_seconds=result.elapsed_seconds,
                inference_seconds=result.inference_seconds,
            )
        )


def main():
    server = WorkerServer()
    server.run()


if __name__ == "__main__":
    main()
