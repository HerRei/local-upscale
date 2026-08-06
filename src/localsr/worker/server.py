import json
import os
import queue
import sys
import threading
import time
import traceback

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
    ProgressUpdate,
    WarningMessage,
    WorkerReady,
)

# Thread-safe writing to stdout
print_lock = threading.Lock()


def send_message(msg):
    with print_lock:
        sys.stdout.write(msg.to_json() + "\n")
        sys.stdout.flush()


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
        inference_started_at = None
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
            if last_progress_at is None:
                seconds_per_tile = elapsed / max(completed, 1)
            else:
                completed_delta = max(1, completed - last_completed)
                seconds_per_tile = (now - last_progress_at) / completed_delta
            if smoothed_seconds_per_tile is None:
                smoothed_seconds_per_tile = seconds_per_tile
            else:
                smoothed_seconds_per_tile = (
                    smoothed_seconds_per_tile * 0.65 + seconds_per_tile * 0.35
                )
            estimated_remaining = smoothed_seconds_per_tile * max(0, total - completed)
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
                    system_ram_available=memory_snapshot.get("system_ram_available", 0),
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
            inference_started_at = time.monotonic()
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
            )

            if out_file is not None:
                out_file.cleanup()
                out_file = None

            send_message(JobCompleted(job_id=job_id))

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
