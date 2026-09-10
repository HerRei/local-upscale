import base64
import gc
import importlib.util
import io
import json
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# Set MPS memory limits before torch is imported
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.62")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.52")

from localsr import __version__
from localsr.core.benchmark import (
    MEASURED_FRAME_COUNT,
    WARMUP_COUNT,
    WORKLOAD_MODEL_ID,
    WORKLOAD_VERSION,
    run_benchmark,
)
from localsr.core.hardware import get_capability_report
from localsr.core.image_formats import is_video_input
from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine
from localsr.core.live_preview import LatestPreviewEncoder, PreviewPacket
from localsr.core.model_adapter import ModelAdapter
from localsr.core.model_catalog import CATALOG_BY_ID, VIDEO_CATALOG_BY_ID, ModelStore
from localsr.core.output_writer import cleanup_stale_work_files
from localsr.core.video_pipeline import VideoJobConfig, run_video_job
from localsr.protocol.messages import (
    MIN_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    BenchmarkCancelled,
    BenchmarkCompleted,
    BenchmarkFailed,
    BenchmarkPreview,
    BenchmarkProgress,
    BenchmarkStageCompleted,
    BenchmarkStageProgress,
    BenchmarkStageStarted,
    BenchmarkStarted,
    CapabilitiesInfo,
    EngineInfo,
    FaceDetectionUnavailable,
    FacesDetected,
    JobCancelled,
    JobFailed,
    JobStarted,
    LivePreviewFrame,
    LivePreviewWarning,
    LogMessage,
    MediaInfo,
    MediaProbeFailed,
    MediaProbeProgress,
    ModelInfo,
    PreviewFailed,
    PreviewReady,
    ProtocolError,
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


def _engine_features() -> list[str]:
    features = [
        "image",
        "video_frame",
        "video_seedvr2",
        "camera_raw",
        "cancellation",
        "progressive_preview",
        "benchmark_v1",
    ]
    if importlib.util.find_spec("cv2") is not None:
        try:
            cv2 = importlib.import_module("cv2")
        except (ImportError, OSError):
            pass
        else:
            if hasattr(cv2, "FaceDetectorYN"):
                features.extend(["face_detection", "face_aware"])
    return features


class WorkerServer:
    def __init__(self):
        cleanup_stale_work_files()
        self.message_queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.state_lock = threading.Lock()
        self.running = True
        self.active_job_id = None
        self.pending_cancel_job_ids: set[str] = set()
        self.model_adapter = ModelAdapter()
        self.model_store = ModelStore()
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

                elif req_type == "handshake_request":
                    requested_version = int(data.get("protocol_version", 0))
                    if not MIN_PROTOCOL_VERSION <= requested_version <= PROTOCOL_VERSION:
                        send_message(
                            ProtocolError(
                                code="unsupported_protocol",
                                message=(
                                    f"Protocol {requested_version} is not supported; "
                                    f"this engine accepts {MIN_PROTOCOL_VERSION} through "
                                    f"{PROTOCOL_VERSION}."
                                ),
                            )
                        )
                        continue
                    send_message(
                        EngineInfo(
                            protocol_version=PROTOCOL_VERSION,
                            minimum_protocol_version=MIN_PROTOCOL_VERSION,
                            engine_id="localsr.pytorch-spandrel",
                            engine_version=__version__,
                            features=_engine_features(),
                            model_formats=[".safetensors", ".pth", ".pt", ".ckpt"],
                            video_engines=["spandrel_image", "seedvr2"],
                        )
                    )

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

                elif req_type == "media_probe_request":
                    media_path = str(data.get("media_path", ""))
                    maximum = int(data.get("max_dimension", 1600))

                    def report_probe(stage, path=media_path):
                        send_message(MediaProbeProgress(media_path=path, stage=stage))

                    try:
                        if is_video_input(media_path):
                            from localsr.core.video_io import probe_video_preview

                            probe, preview = probe_video_preview(media_path, maximum, report_probe)
                            preview_base64 = base64.b64encode(preview).decode("ascii")
                            send_message(
                                MediaInfo(
                                    media_path=media_path,
                                    media_kind="video",
                                    width=int(probe.width),
                                    height=int(probe.height),
                                    frame_count=int(probe.frame_count),
                                    fps=float(probe.fps),
                                    duration_seconds=float(probe.duration_seconds),
                                    jpeg_base64=preview_base64,
                                    hdr_format=probe.hdr_format,
                                    audio_warning=probe.audio_warning,
                                )
                            )
                        else:
                            report_probe("decoding_image")
                            preview_data = ImageManager().load(media_path)
                            tensor = preview_data["tensor"]
                            _, height, width = tensor.shape
                            report_probe("preparing_preview")
                            send_message(
                                MediaInfo(
                                    media_path=media_path,
                                    media_kind="image",
                                    width=int(width),
                                    height=int(height),
                                    jpeg_base64=_encode_chw_jpeg(tensor, maximum, quality=82),
                                )
                            )
                            del preview_data
                            gc.collect()
                    except Exception as error:  # noqa: BLE001
                        send_message(
                            MediaProbeFailed(
                                media_path=media_path,
                                error_message=str(error),
                            )
                        )

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
                        result = detect_faces(rgb, cancel_event=self.cancel_event)
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
                    previous_checkpoint_policy = getattr(
                        self.model_adapter, "allow_unverified_checkpoints", False
                    )
                    requested_checkpoint_policy = data.get("allow_unverified_checkpoint")
                    self.model_adapter.allow_unverified_checkpoints = (
                        previous_checkpoint_policy
                        if requested_checkpoint_policy is None
                        else bool(requested_checkpoint_policy)
                    )

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
                        self.model_adapter.allow_unverified_checkpoints = previous_checkpoint_policy
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
                    previous_checkpoint_policy = getattr(
                        self.model_adapter, "allow_unverified_checkpoints", False
                    )
                    requested_checkpoint_policy = data.get("allow_unverified_checkpoint")
                    self.model_adapter.allow_unverified_checkpoints = (
                        previous_checkpoint_policy
                        if requested_checkpoint_policy is None
                        else bool(requested_checkpoint_policy)
                    )

                    try:
                        send_message(JobStarted(job_id=job_id))
                        self._run_video_job(job_id, data)
                    except Exception as e:  # noqa: BLE001
                        traceback.print_exc(file=sys.stderr)
                        send_message(JobFailed(job_id=job_id, error_message=str(e)))
                    finally:
                        self._deactivate_job(job_id)
                        self.model_adapter.release()
                        self.model_adapter.allow_unverified_checkpoints = previous_checkpoint_policy
                        self.engine.release_model()

                elif req_type == "benchmark_request":
                    if self.active_job_id is not None:
                        send_message(
                            BenchmarkFailed(
                                job_id=str(data.get("job_id") or ""),
                                error_message="The inference worker is already busy.",
                            )
                        )
                        continue

                    job_id = str(data.get("job_id") or "")
                    self._activate_job(job_id)
                    try:
                        self._run_benchmark(job_id, data)
                    except InterruptedError:
                        send_message(BenchmarkCancelled(job_id=job_id))
                    except Exception as error:  # noqa: BLE001
                        traceback.print_exc(file=sys.stderr)
                        send_message(BenchmarkFailed(job_id=job_id, error_message=str(error)))
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

    def _run_benchmark(self, job_id: str, data: dict) -> None:
        """Authenticate and execute the fixed, production-path benchmark."""
        workload = str(data.get("workload") or "v1")
        if workload == "v2":
            self._run_benchmark_v2(job_id, data)
            return

        requested_model_id = str(data.get("model_id") or "")
        if requested_model_id != WORKLOAD_MODEL_ID:
            raise ValueError(
                f"{WORKLOAD_VERSION} requires the trusted {WORKLOAD_MODEL_ID} checkpoint."
            )
        model = CATALOG_BY_ID[WORKLOAD_MODEL_ID]
        supplied = Path(str(data.get("model_path") or ""))
        try:
            supplied = supplied.resolve(strict=True)
            expected = self.model_store.path_for(model).resolve(strict=True)
        except OSError as error:
            raise FileNotFoundError(
                "Download and verify the Quick model before benchmarking."
            ) from error
        if supplied != expected or not self.model_store.is_installed(model):
            raise ValueError("The benchmark checkpoint failed its trusted catalog verification.")

        send_message(
            BenchmarkStarted(
                job_id=job_id,
                workload_version=WORKLOAD_VERSION,
                warmup_count=WARMUP_COUNT,
                measured_frame_count=MEASURED_FRAME_COUNT,
            )
        )
        info = self.model_adapter.inspect(str(expected))

        def progress(completed: int, total: int) -> None:
            send_message(
                BenchmarkProgress(
                    job_id=job_id,
                    completed_frames=completed,
                    total_frames=total,
                    percentage=completed / max(1, total) * 100.0,
                )
            )

        result = run_benchmark(
            engine=self.engine,
            model_info=info,
            model_path=str(expected),
            model_name=model.name,
            device=str(data.get("device") or "cpu"),
            cancel_event=self.cancel_event,
            progress_callback=progress,
        )
        send_message(BenchmarkCompleted(job_id=job_id, result=result.to_dict()))

    def _run_benchmark_v2(self, job_id: str, data: dict) -> None:
        """Run the multi-device, multi-scene v2 benchmark (Blender-style)."""
        from localsr.core.benchmark_v2 import (
            SCENES as V2_SCENES,
        )
        from localsr.core.benchmark_v2 import (
            WORKLOAD_MODEL_ID as V2_MODEL_ID,
        )
        from localsr.core.benchmark_v2 import (
            WORKLOAD_VERSION as V2_WORKLOAD_VERSION,
        )
        from localsr.core.benchmark_v2 import (
            StageUpdate,
            aggregate_v2,
            run_device_phase,
        )
        from localsr.core.hardware import get_capability_report

        requested_model_id = str(data.get("model_id") or "")
        if requested_model_id != V2_MODEL_ID:
            raise ValueError(
                f"{V2_WORKLOAD_VERSION} requires the trusted {V2_MODEL_ID} checkpoint."
            )
        model = CATALOG_BY_ID[V2_MODEL_ID]
        supplied = Path(str(data.get("model_path") or ""))
        try:
            supplied = supplied.resolve(strict=True)
            expected = self.model_store.path_for(model).resolve(strict=True)
        except OSError as error:
            raise FileNotFoundError(
                "Download and verify the Quick model before benchmarking."
            ) from error
        if supplied != expected or not self.model_store.is_installed(model):
            raise ValueError("The benchmark checkpoint failed its trusted catalog verification.")

        devices = get_capability_report()["devices"]
        # CPU last: accelerators run first while the machine is coolest, and
        # the CPU phase benefits from warming caches anyway. ``cpu`` always
        # exists in a capability report.
        ordered = [d for d in devices if d["id"] != "cpu"] + [
            d for d in devices if d["id"] == "cpu"
        ]
        stages_per_device = len(V2_SCENES) + 2
        total_phases = len(ordered) * stages_per_device
        send_message(
            BenchmarkStarted(
                job_id=job_id,
                workload_version=V2_WORKLOAD_VERSION,
                warmup_count=0,
                measured_frame_count=total_phases,
            )
        )
        info = self.model_adapter.inspect(str(expected))

        started_at = time.monotonic()
        device_results: list[dict] = []
        for device_index, device in enumerate(ordered):
            if self.cancel_event.is_set():
                raise InterruptedError("benchmark cancelled")
            device_id = str(device["id"])

            def stage_progress(
                update: StageUpdate,
                *,
                current_device_index: int = device_index,
                current_device_id: str = device_id,
            ) -> None:
                stage_index = current_device_index * stages_per_device + update.stage_index
                percentage = (stage_index + update.fraction) / max(1, total_phases) * 100.0
                # Keep the original aggregate event for older hosts, then send
                # the richer v2 envelope understood by current hosts.
                send_message(
                    BenchmarkProgress(
                        job_id=job_id,
                        completed_frames=stage_index,
                        total_frames=total_phases,
                        percentage=percentage,
                        stage=update.stage,
                    )
                )
                common = {
                    "job_id": job_id,
                    "device": current_device_id,
                    "stage": update.stage,
                    "stage_index": stage_index + 1,
                    "stage_count": total_phases,
                    "percentage": percentage,
                }
                if update.event == "started":
                    send_message(BenchmarkStageStarted(**common))
                elif update.event == "progress":
                    send_message(
                        BenchmarkStageProgress(
                            **common,
                            completed_units=update.completed_units,
                            total_units=update.total_units,
                        )
                    )
                else:
                    send_message(BenchmarkStageCompleted(**common))

            phase = run_device_phase(
                engine=self.engine,
                model_info=info,
                model_path=str(expected),
                device_id=device_id,
                device_name=str(device.get("name") or device_id),
                device_type=str(device.get("type") or device_id),
                scenes=V2_SCENES,
                cancel_event=self.cancel_event,
                progress_callback=stage_progress,
                preview_callback=lambda render: send_message(
                    BenchmarkPreview(job_id=job_id, render=render)
                ),
            )
            device_results.append(phase.to_dict())

        result = aggregate_v2(device_results, time.monotonic() - started_at)
        send_message(BenchmarkCompleted(job_id=job_id, result=result.to_dict()))

    def _run_job(self, job_id, data):
        from localsr.worker.image_job import ImageJobRunner

        ImageJobRunner(
            engine=self.engine,
            model_adapter=self.model_adapter,
            cancel_event=self.cancel_event,
            emit=send_message,
            emit_preview=self._emit_live_preview,
            preview_factory=LatestPreviewEncoder,
        ).run(job_id, data)

    @staticmethod
    def _emit_live_preview(packet: PreviewPacket) -> None:
        send_message(
            LivePreviewFrame(
                job_id=packet.job_id,
                sequence=packet.sequence,
                preview_kind=packet.preview_kind,
                jpeg_base64=packet.jpeg_base64,
                output_x=packet.output_x,
                output_y=packet.output_y,
                output_width=packet.output_width,
                output_height=packet.output_height,
                image_width=packet.image_width,
                image_height=packet.image_height,
            )
        )

    def _run_temporal_video_job(self, job_id, data, engine_factory):
        """Stream a video through a clip-based temporal engine.

        Frames are read in 4n+1 chunks; each chunk is conditioned on the
        previous chunk's tail frames (context in, dropped from the output,
        mirroring the upstream streaming CLI). The first chunk is processed
        eagerly to learn the output dimensions before the encoder opens.
        """
        import time as _time

        from localsr.core.video_io import (
            decode_timed_frames,
            encode_video,
            probe_video,
            selected_frame_count,
        )

        video_path = data["video_path"]
        output_path = data["output_video_path"]
        probe = probe_video(video_path)
        if data.get("fps") is not None:
            if not np.isfinite(float(data["fps"])) or float(data["fps"]) <= 0:
                raise ValueError("Video FPS override must be a finite positive number.")
            send_message(
                LogMessage(
                    level="warning",
                    message="FPS override changes playback speed; source audio and subtitles are omitted.",
                )
            )
        fps = data.get("fps") or probe.fps or 25.0
        total_frames = selected_frame_count(
            probe.frame_count, data.get("start_frame"), data.get("end_frame")
        )
        window = int(data.get("temporal_window") or 9)
        overlap = max(0, min(int(data.get("temporal_overlap") or 2), window - 1))
        resolution = int(data.get("target_resolution") or 0)
        if resolution <= 0:
            resolution = min(probe.width, probe.height)
        bundle_dir = data.get("bundle_dir") or ""

        send_message(LogMessage(level="info", message="Loading the temporal video engine..."))
        engine = engine_factory(
            bundle_dir, str(data.get("device", "cpu")), str(data.get("precision", "fp32"))
        )

        started_at = _time.monotonic()
        chunk_new = 33  # 4n+1: fresh frames per streamed chunk
        decode_iter = decode_timed_frames(
            video_path,
            data.get("start_frame"),
            data.get("end_frame"),
            self.cancel_event,
            hdr_mode=str(data.get("hdr_mode", "reject")),
        )
        state = {"done": 0}
        preview_encoder = LatestPreviewEncoder(
            lambda packet: self._emit_live_preview(packet),
            lambda message: send_message(LivePreviewWarning(job_id=job_id, message=message)),
            enabled=bool(data.get("preview_enabled", True)),
            max_fps=float(data.get("preview_max_fps", 2.0)),
            max_dimension=int(data.get("preview_max_dimension", 320)),
        )

        def read_chunk() -> list:
            frames = []
            for _ in range(chunk_new):
                try:
                    frame = next(decode_iter)
                except StopIteration:
                    break
                frames.append(frame)
            return frames

        def process_chunk(chunk: list, context: list) -> list:
            if self.cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            send_message(
                VideoFrameStarted(
                    job_id=job_id,
                    frame_index=state["done"],
                    total_frames=int(total_frames),
                )
            )
            result = engine.process_frames(
                [frame.rgb for frame in context + chunk],
                resolution=resolution,
                batch_size=window,
                temporal_overlap=overlap,
                seed=42,
                cancel_event=self.cancel_event,
            )
            if self.cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            if len(result) != len(context) + len(chunk):
                raise ValueError(
                    f"Temporal engine returned {len(result)} frames for "
                    f"{len(context) + len(chunk)} input frames."
                )
            result = [
                frame.with_pixels(rgb)
                for frame, rgb in zip(chunk, result[len(context) :], strict=True)
            ]
            state["done"] += len(chunk)
            elapsed = _time.monotonic() - started_at
            per_frame = elapsed / max(1, state["done"])
            remaining = per_frame * max(0, int(total_frames) - state["done"])
            send_message(
                VideoFrameCompleted(
                    job_id=job_id,
                    frame_index=state["done"] - 1,
                    total_frames=int(total_frames),
                    frames_processed=state["done"],
                    elapsed_seconds=float(elapsed),
                    estimated_remaining_seconds=float(remaining),
                    jpeg_base64="",
                )
            )
            return result

        try:
            first_chunk = read_chunk()
            if not first_chunk:
                raise ValueError("No frames could be decoded from the video.")
            first_out = process_chunk(first_chunk, [])
            if not first_out:
                raise ValueError("The temporal engine produced no video frames.")
            out_height, out_width = first_out[0].rgb.shape[:2]

            def all_frames():
                emitted = 0
                for frame in first_out:
                    if self.cancel_event.is_set():
                        raise InterruptedError("video job cancelled")
                    preview_encoder.submit(
                        job_id=job_id,
                        preview_kind="video",
                        pixels=frame.rgb,
                        force=bool(total_frames > 0 and emitted + 1 >= total_frames),
                        output_width=int(frame.rgb.shape[1]),
                        output_height=int(frame.rgb.shape[0]),
                        image_width=int(frame.rgb.shape[1]),
                        image_height=int(frame.rgb.shape[0]),
                    )
                    emitted += 1
                    yield frame
                previous_tail = first_chunk[-overlap:] if overlap > 0 else []
                while True:
                    if self.cancel_event.is_set():
                        raise InterruptedError()
                    chunk = read_chunk()
                    if not chunk:
                        return
                    output = process_chunk(chunk, list(previous_tail))
                    previous_tail = chunk[-overlap:] if overlap > 0 else []
                    for frame in output:
                        if self.cancel_event.is_set():
                            raise InterruptedError("video job cancelled")
                        preview_encoder.submit(
                            job_id=job_id,
                            preview_kind="video",
                            pixels=frame.rgb,
                            force=bool(total_frames > 0 and emitted + 1 >= total_frames),
                            output_width=int(frame.rgb.shape[1]),
                            output_height=int(frame.rgb.shape[0]),
                            image_width=int(frame.rgb.shape[1]),
                            image_height=int(frame.rgb.shape[0]),
                        )
                        emitted += 1
                        yield frame

            encode_video(
                all_frames(),
                output_path,
                fps=float(fps),
                container_format=str(data.get("container", "mp4")),
                crf=int(data.get("crf", 18)),
                width=int(out_width),
                height=int(out_height),
                audio_source=video_path if data.get("fps") is None else None,
                preserve_timing=data.get("fps") is None,
                cancel_event=self.cancel_event,
                warning_callback=lambda message: send_message(
                    LogMessage(level="warning", message=message)
                ),
                sdr_bt709=bool(probe.hdr_format and data.get("hdr_mode") == "tone_map"),
            )
        except InterruptedError:
            send_message(JobCancelled(job_id=job_id))
            return
        finally:
            preview_encoder.close()
            engine = None  # noqa: F841 — releases the models
            try:
                import torch

                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
                elif torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass

        if self.cancel_event.is_set():
            send_message(JobCancelled(job_id=job_id))
            return

        elapsed = _time.monotonic() - started_at
        send_message(
            VideoJobCompleted(
                job_id=job_id,
                output_path=output_path,
                frames_processed=state["done"],
                elapsed_seconds=float(elapsed),
                inference_seconds=float(elapsed),
            )
        )

    def _verify_temporal_bundle(self, data) -> None:
        """Authenticate a catalog video bundle off the desktop UI thread."""
        model_id = str(data.get("video_model_id") or "")
        model = VIDEO_CATALOG_BY_ID.get(model_id)
        if model is None:
            raise ValueError("The temporal video model is not in the trusted catalog.")

        supplied = Path(str(data.get("bundle_dir") or ""))
        try:
            supplied = supplied.resolve(strict=True)
            expected = self.model_store.bundle_dir_for(model).resolve(strict=True)
        except OSError as error:
            raise FileNotFoundError("The temporal video model bundle is unavailable.") from error
        if supplied != expected:
            raise ValueError("The temporal video model path does not match the trusted catalog.")

        send_message(
            LogMessage(level="info", message="Verifying the temporal model bundle integrity...")
        )
        if not self.model_store.is_bundle_installed(model):
            raise ValueError(
                f"{model.name} failed its catalog SHA-256 check. "
                "LocalSR refused to load it; remove the bundle and download it again."
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
            self._verify_temporal_bundle(data)
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
            face_fidelity=float(data.get("face_fidelity", 0.7)),
            deflicker=bool(data.get("deflicker", False)),
            deflicker_window=int(data.get("deflicker_window", 3)),
            output_scale=data.get("output_scale"),
            hdr_mode=str(data.get("hdr_mode", "reject")),
        )

        preview_encoder = LatestPreviewEncoder(
            lambda packet: self._emit_live_preview(packet),
            lambda message: send_message(LivePreviewWarning(job_id=job_id, message=message)),
            enabled=bool(data.get("preview_enabled", True)),
            max_fps=float(data.get("preview_max_fps", 2.0)),
            max_dimension=int(data.get("preview_max_dimension", 320)),
        )

        def frame_started(frame_index: int, total_frames: int) -> None:
            send_message(
                VideoFrameStarted(
                    job_id=job_id,
                    frame_index=int(frame_index),
                    total_frames=int(total_frames),
                )
            )

        def enhanced_frame(frame_index: int, total_frames: int, pixels: np.ndarray) -> None:
            preview_encoder.submit(
                job_id=job_id,
                preview_kind="video",
                pixels=pixels,
                force=bool(total_frames > 0 and frame_index + 1 >= total_frames),
                output_width=int(pixels.shape[1]),
                output_height=int(pixels.shape[0]),
                image_width=int(pixels.shape[1]),
                image_height=int(pixels.shape[0]),
            )

        def progress_cb(frames_done: int, total_frames: int, elapsed: float) -> None:
            if total_frames > 0:
                per_frame = elapsed / max(frames_done, 1)
                remaining = per_frame * max(0, total_frames - frames_done)
            else:
                remaining = 0.0
            send_message(
                VideoFrameCompleted(
                    job_id=job_id,
                    frame_index=max(0, int(frames_done) - 1),
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
                enhanced_frame_cb=enhanced_frame,
                progress_cb=progress_cb,
                warning_callback=lambda message: send_message(
                    LogMessage(level="warning", message=message)
                ),
            )
        except InterruptedError:
            preview_encoder.close()
            send_message(JobCancelled(job_id=job_id))
            return
        finally:
            preview_encoder.close()

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
    if len(sys.argv) > 1 and sys.argv[1] == "--backend-probe":
        import argparse

        from localsr.core.backend_validation import probe

        parser = argparse.ArgumentParser()
        parser.add_argument("--backend-probe", required=True)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        result = probe(args.backend_probe)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return
    server = WorkerServer()
    server.run()


if __name__ == "__main__":
    main()
