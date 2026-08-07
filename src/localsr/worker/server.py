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

from PIL import Image

# Set MPS memory limits before torch is imported
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.62")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.52")

from localsr.core.hardware import get_capability_report, get_memory_snapshot
from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import ModelAdapter
from localsr.protocol.messages import (
    CapabilitiesInfo,
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
        self.model_adapter = ModelAdapter()
        self.engine = InferenceEngine(self.model_adapter)

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
                requested_job_id = msg.get("data", {}).get("job_id")
                with self.state_lock:
                    if requested_job_id == self.active_job_id:
                        self.cancel_event.set()

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
                    # Already handled in reader_thread to set the event, but we can acknowledge if no job is active
                    with self.state_lock:
                        if self.active_job_id is None:
                            self.cancel_event.clear()

                elif req_type == "job_request":
                    if self.active_job_id is not None:
                        send_message(
                            JobFailed(
                                job_id=data["job_id"], error_message="A job is already running."
                            )
                        )
                        continue

                    job_id = data["job_id"]
                    with self.state_lock:
                        self.cancel_event.clear()
                        self.active_job_id = job_id

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
                        with self.state_lock:
                            self.active_job_id = None
                            self.cancel_event.clear()
                        self.model_adapter.release()

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

        # Load image (this separates RGB and Alpha)
        send_message(LogMessage(level="info", message="Loading image..."))
        im_manager = ImageManager()
        img_data = im_manager.load(data["image_path"])

        out_file = None
        try:
            send_message(LogMessage(level="info", message="Starting inference..."))
            processing_call_started_at = time.monotonic()
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
                )
            )

        except InterruptedError:
            send_message(JobCancelled(job_id=job_id))
        finally:
            if out_file is not None:
                out_file.cleanup()


def main():
    server = WorkerServer()
    server.run()


if __name__ == "__main__":
    main()
