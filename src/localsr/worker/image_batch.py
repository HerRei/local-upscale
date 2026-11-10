"""Mac photo batches: Neural Engine and GPU lanes over one tile queue, parallel saves.

The desktop sends queued photos that share one model and every setting as one
batch. On the Neural Engine route, for a model the Neural Engine runs (SPAN,
Real-ESRGAN's RRDBNet, FBCNN), their tiles share one queue: two Neural Engine
runners and, on AC power, two Core ML GPU runners pull from it at one fixed
input shape, every photo is stitched on its own, and finished photos are saved
by several encoder threads.

Each photo stays its own job: it starts, reports progress and tiles, and
completes, fails or is cancelled on its own. Anything the lanes do not cover
(another model, a fix before the upscaler, Faces, a gate the lanes do not pass)
and any photo whose tiles the lanes cannot render go back to the caller, which
runs them through the normal per-photo runner.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import psutil
import torch
from PIL import Image

from localsr.core.batch_scheduler import PhotoFallback, run_photo_batch
from localsr.core.coreml_runtime import CORE_ML_GPU
from localsr.core.disk_space import (
    DiskFullError,
    failure_fields,
    image_claims,
    image_need,
    require_room,
)
from localsr.core.eta import RemainingTime
from localsr.core.image_io import ImageManager
from localsr.core.inference import InferenceEngine, _fixed_window, _span_output_is_unstable
from localsr.core.live_preview import LatestPreviewEncoder, PreviewPacket
from localsr.core.model_adapter import ModelAdapter, NormalizedModelInfo
from localsr.core.output_writer import OutputWriter
from localsr.core.pipeline import PipelineStageError, resolve_image_pipeline
from localsr.core.tiling import generate_tiles
from localsr.protocol.messages import (
    EngineStatus,
    JobCancelled,
    JobCompleted,
    JobFailed,
    JobStarted,
    LivePreviewWarning,
    LogMessage,
    WorkerMessage,
)
from localsr.worker.image_job import save_output
from localsr.worker.job_progress import ImageJobProgress

# Fixed input side per architecture, including tile core and overlap.
BATCH_SIDES = {"SPAN": 384, "ESRGAN": 384, "FBCNN": 576}
# Encoder thread counts accommodate the throughput of each architecture.
ENCODERS = {"SPAN": 6}
DEFAULT_ENCODERS = 4
# What differs between the photos of one batch; everything else must be equal.
_PHOTO_FIELDS = frozenset(
    {"job_id", "image_path", "output_path", "scratch_directory", "output_temporary_directory"}
)


def batch_settings(data: Mapping[str, Any]) -> dict[str, Any] | None:
    """The recipe a photo shares with its batch, or None when it needs the normal runner."""
    if data.get("device") != "mps" or not data.get("neural_engine", True):
        return None
    if data.get("face_model_path"):
        return None
    try:
        pipeline = resolve_image_pipeline(
            raw_stages=data.get("stages"),
            model_path=str(data["model_path"]),
            output_scale=data.get("output_scale"),
            face_model_path=data.get("face_model_path"),
        )
    except (KeyError, ValueError):
        return None  # The normal runner reports an invalid recipe for each photo.
    if pipeline.preprocess_stages or pipeline.face is not None:
        return None
    return {key: value for key, value in data.items() if key not in _PHOTO_FIELDS}


def save_bytes(width: int, height: int, scale: int, output_scale: int, alpha: bool) -> int:
    """Peak bytes one save holds for a ``width`` x ``height`` native-scale output.

    Pillow copies the transposed output (3 B/px) into an RGB image (4 B/px), and
    the save reads the output's pages; the estimate uses 8 B/px. A smaller
    output scale adds the resized RGB copy; alpha adds the RGBA image (4 B/px) and
    its scaled alpha plane (1 B/px).
    """
    native = width * height
    final = round(width * output_scale / scale) * round(height * output_scale / scale)
    total = 8 * native
    if output_scale != scale:
        total += 4 * final
    if alpha:
        total += 5 * final
    return total


def default_memory_budget() -> int:
    """What saves may hold at once: a quarter of the memory, 2 GiB when unknown."""
    # psutil works on every platform; os.sysconf does not exist on Windows.
    try:
        total = int(psutil.virtual_memory().total)
    except (OSError, RuntimeError, AttributeError):
        return 2 * 2**30
    return total // 4 if total > 0 else 2 * 2**30


def read_ac_power() -> bool:
    """Whether the Mac draws from AC power; anything unclear counts as battery."""
    try:
        result = subprocess.run(
            ["/usr/bin/pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "Now drawing from 'AC Power'" in (result.stdout or "")


class PowerSource:
    """The power source, read again at most once per ``interval`` seconds."""

    def __init__(
        self,
        read: Callable[[], bool] = read_ac_power,
        *,
        interval: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read, self._interval, self._clock = read, interval, clock
        self._lock = threading.Lock()
        self._checked: float | None = None
        self._ac = False

    def on_ac(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._checked is None or now - self._checked >= self._interval:
                self._ac = bool(self._read())
                self._checked = now
            return self._ac


class _Lane:
    """One native runner: renders a tile of any photo at the batch's fixed shape."""

    def __init__(self, runner, shape: tuple[int, int], info: NormalizedModelInfo, power=None):
        self.runner, self.shape, self.info, self.power = runner, shape, info, power
        self.unit = runner.unit
        self.tiles = 0

    @property
    def available(self) -> bool:
        # The GPU lane works only on AC power; the Neural Engine always.
        return self.power is None or self.power.on_ac()

    def __call__(self, photo: _Photo, tile) -> Any:
        window = _fixed_window(photo.tensor, tile, self.shape)
        if window is None:
            raise ValueError("A batch tile does not fit the lane's input shape.")
        pixels, top, left = window
        with torch.inference_mode():
            output = torch.as_tensor(
                self.runner.predict({"image": pixels.numpy()[None]})["restored"]
            )
            scale = self.info.scale
            expected = (1, self.info.out_channels, self.shape[0] * scale, self.shape[1] * scale)
            if tuple(output.shape) != expected:
                raise ValueError(f"Core ML returned a {tuple(output.shape)} tile, not {expected}.")
            if not torch.isfinite(output).all():
                raise PhotoFallback("Model produced NaN or Infinity.")
            core = output[
                0,
                :,
                top * scale : top * scale + tile.out_h,
                left * scale : left * scale + tile.out_w,
            ].float()
            # The normal runner retries such regions with more context.
            if self.info.architecture == "SPAN" and _span_output_is_unstable(core):
                raise PhotoFallback("SPAN produced extreme pixel values.")
            self.tiles += 1
            return core.clamp(0, 1).mul_(255).round_().byte().numpy()


class _Photo:
    """One photo of a batch: its input, output writer, progress and save."""

    def __init__(self, batch: ImageBatchRunner, data, manager, image, info, shape, preview):
        self.batch, self.data, self.manager, self.info = batch, data, manager, info
        self.tensor = image["tensor"]
        # The save needs the metadata and alpha, not the decoded input.
        self.image = {key: value for key, value in image.items() if key != "tensor"}
        self.core = shape[0] - 2 * int(data["halo"])
        height, width = self.tensor.shape[-2:]
        self.width, self.height = width * info.scale, height * info.scale
        self.tiles = list(generate_tiles(width, height, self.core, int(data["halo"]), info.scale))
        self.pipeline = resolve_image_pipeline(
            raw_stages=data.get("stages"),
            model_path=str(data["model_path"]),
            output_scale=data.get("output_scale"),
        )
        self.progress = ImageJobProgress(
            data["job_id"],
            self.pipeline,
            data["device"],
            batch.emit,
            preview,
            clock=batch.clock,
            rate=batch.rate,
        )
        self.completed = 0
        self.nbytes = save_bytes(
            self.width,
            self.height,
            info.scale,
            int(data.get("output_scale") or info.scale),
            getattr(manager, "alpha_channel", None) is not None,
        )
        self.writer = OutputWriter(
            (info.out_channels, self.height, self.width),
            temporary_directory=data.get("scratch_directory"),
        )

    def start(self) -> None:
        self.batch.announce(self.data)
        self.batch.photo_started(self.width * self.height)
        self.progress.start(self.pipeline.primary_index, self.pipeline.primary)

    def dispatch(self, tile) -> None:
        # A retried tile starts again; the count stays the tiles done so far.
        self.progress.tile(
            "started",
            tile,
            None,
            self.completed,
            len(self.tiles),
            self.width,
            self.height,
            self.core,
        )

    def stitch(self, tile, pixels) -> None:
        try:
            self.writer.write_tile(pixels, tile.out_x, tile.out_y)
        except Exception as error:  # noqa: BLE001 - reported like the normal runner's
            raise PipelineStageError(self.pipeline.primary, str(error)) from error
        self.completed += 1
        total = len(self.tiles)
        self.batch.tile_done(int(tile.out_w) * int(tile.out_h))
        self.progress.tile(
            "completed", tile, pixels, self.completed, total, self.width, self.height, self.core
        )
        self.progress.update(self.completed, total, self.core)
        if self.completed == total:
            self.progress.complete(self.pipeline.primary_index, self.pipeline.primary)
            self.tensor = None

    def encode(self) -> float:
        """Save atomically; returns the seconds from the first tile to the saved file."""
        notes = save_output(
            self.manager, self.writer, self.image, self.info, self.data, self.batch.cancel_event
        )
        if notes:
            self.batch.save_notes[str(self.data["job_id"])] = notes
        started = self.progress.inference_started_at
        return self.batch.clock() - (started if started is not None else self.batch.clock())

    def close(self) -> None:
        self.tensor = None
        self.writer.cleanup()


class ImageBatchRunner:
    """Run what the lanes can of one batch; hand the rest back for the normal runner."""

    def __init__(
        self,
        *,
        engine: InferenceEngine,
        model_adapter: ModelAdapter,
        cancel_event: threading.Event,
        emit: Callable[[WorkerMessage], None],
        emit_preview: Callable[[PreviewPacket], None],
        preview_factory: Callable[..., LatestPreviewEncoder] = LatestPreviewEncoder,
        power: PowerSource | None = None,
        memory_budget: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self.model_adapter = model_adapter
        self.cancel_event = cancel_event
        self.emit = emit
        self.emit_preview = emit_preview
        self.preview_factory = preview_factory
        self.power = power or PowerSource()
        self.memory_budget = memory_budget or default_memory_budget()
        self.clock = clock
        self.lane_tile_counts: dict[str, int] = {}
        # What each saved photo's save left out, until its job_completed carries it.
        self.save_notes: dict[str, list[str]] = {}
        self._announced: set[str] = set()
        self._focus = ""
        # The speed of all lanes together over the whole batch: a photo's remaining time is
        # its unfinished pixels at this speed, whichever lane reports last.
        self.rate = RemainingTime(1.0)
        self._pixels_done = 0
        self._photos_started = 0
        self._started_pixels = 0
        self._photo_count = 0

    def announce(self, data: Mapping[str, Any]) -> None:
        """A photo's JobStarted, once: the desktop then shows this photo."""
        job_id = str(data["job_id"])
        self._focus = job_id
        if job_id not in self._announced:
            self._announced.add(job_id)
            self.emit(JobStarted(job_id=job_id))

    def run(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Report every photo the lanes finish, fail or cancel; return the others in order."""
        self.lane_tile_counts = {}
        if self.cancel_event.is_set():
            for data in jobs:
                self.emit(JobCancelled(job_id=str(data["job_id"])))
            return []
        info = self._plan(jobs)
        if info is None:
            return list(jobs)
        encoders = self._encoders_that_fit(jobs, info)
        if encoders is None:
            return list(jobs)
        shape = (BATCH_SIDES[info.architecture],) * 2
        data = jobs[0]
        self.announce(data)
        remaining = list(jobs)
        try:
            self.engine.neural_engine_enabled = True
            self.engine.status_callback = lambda message: self.emit(
                EngineStatus(job_id=self._focus, message=message)
            )
            self.engine.release_models_except({data["model_path"]})
            self.engine.load_model(
                data["model_path"],
                data["device"],
                data["precision"],
                info,
                safe_memory=data["safe_memory"],
            )
            model = self.engine.active_model
            if not callable(getattr(model, "batch_lanes", None)):
                return remaining
            model.set_cancel_event(self.cancel_event)
            handed = self._decode_first(remaining)
            if not handed:
                return []
            tensor = handed["first"][1]["tensor"]
            sample = self._sample(tensor, shape, int(data["halo"]), info.scale)
            del tensor
            # The GPU lanes are loaded on battery too; they take tiles only on AC power.
            with torch.inference_mode():
                runners = model.batch_lanes(sample, shape, gpu=True)
            del sample
        except InterruptedError:
            for item in remaining:
                self.emit(JobCancelled(job_id=str(item["job_id"])))
            return []
        except Exception as error:  # noqa: BLE001 - the normal runner reports per photo
            self._log(f"Photo batch lanes are unavailable; using the normal runner: {error}")
            return remaining
        if not runners:
            return remaining
        return self._run_lanes(remaining, handed, model, runners, info, shape, encoders)

    def _plan(self, jobs: list[dict[str, Any]]) -> NormalizedModelInfo | None:
        if len(jobs) < 2:
            return None
        settings = batch_settings(jobs[0])
        if settings is None or any(batch_settings(data) != settings for data in jobs[1:]):
            return None
        try:
            info = self.model_adapter.inspect(str(jobs[0]["model_path"]))
        except Exception:  # noqa: BLE001 - the normal runner reports it for each photo
            return None
        side = BATCH_SIDES.get(info.architecture)
        if side is None or info.in_channels != 3 or info.out_channels != 3:
            return None
        return info if side > 2 * int(jobs[0]["halo"]) else None

    def _encoders_that_fit(
        self, jobs: list[dict[str, Any]], info: NormalizedModelInfo
    ) -> int | None:
        """The encoder count whose open photos fit on the disk, fewer than usual when needed.

        None when not even one encoder fits: the photos then go one at a time through the
        normal runner, which refuses each that cannot be written, with the reason.
        """
        usual = ENCODERS.get(info.architecture, DEFAULT_ENCODERS)
        largest = None
        for data in jobs:
            try:
                with Image.open(data["image_path"]) as header:
                    size, alpha = header.size, "A" in header.mode
            except Exception:  # noqa: BLE001 - that photo fails on its own when it is decoded
                continue
            if largest is None or size[0] * size[1] > largest[0][0] * largest[0][1]:
                largest = (size, alpha)
        if largest is None:
            return usual
        (width, height), alpha = largest
        data = jobs[0]
        need = image_need(
            width, height, info.scale, data.get("output_scale"), str(data["output_format"]), alpha
        )
        for encoders in range(usual, 0, -1):
            try:
                require_room(image_claims(data, need, encoders))
            except DiskFullError as error:
                refusal = error
                continue
            if encoders < usual:
                self._log(f"Photo batch lanes run with {encoders} encoders: the disk is short.")
            return encoders
        self._log(f"Photo batch lanes are off; the photos run one at a time. {refusal}")
        return None

    def _decode_first(self, remaining: list[dict[str, Any]]) -> dict[str, tuple]:
        """Decode the first photo that opens; it supplies the gate's sample tile.

        It comes in a dict that the batch empties when it opens that photo, so no
        other reference keeps the decoded input alive for the whole batch.
        """
        while remaining:
            self._check_cancelled()
            data = remaining[0]
            manager = ImageManager()
            try:
                return {"first": (data, manager.load(data["image_path"]), manager)}
            except Exception as error:  # noqa: BLE001 - this photo only, as in the normal runner
                remaining.pop(0)
                self.announce(data)
                self.emit(JobFailed(job_id=str(data["job_id"]), error_message=str(error)))
        return {}

    @staticmethod
    def _sample(tensor: torch.Tensor, shape, halo: int, scale: int) -> torch.Tensor:
        height, width = tensor.shape[-2:]
        tile = next(generate_tiles(width, height, shape[0] - 2 * halo, halo, scale))
        window = _fixed_window(tensor, tile, shape)
        if window is None:
            raise ValueError("The batch overlap does not fit the lane shape.")
        return window[0][None].contiguous()

    def _run_lanes(
        self, remaining, handed, model, runners, info, shape, encoders
    ) -> list[dict[str, Any]]:
        data = remaining[0]
        preview = self.preview_factory(
            self.emit_preview,
            lambda message: self.emit(LivePreviewWarning(job_id=self._focus, message=message)),
            enabled=bool(data.get("preview_enabled", True)),
            max_fps=float(data.get("preview_max_fps", 2.0)),
            max_dimension=int(data.get("preview_max_dimension", 320)),
        )
        lanes = [
            _Lane(runner, shape, info, self.power if runner.unit == CORE_ML_GPU else None)
            for runner in runners
        ]
        # Each lane's first tile may still carry a one-off cost.
        self.rate = RemainingTime(1.0, warmup=len(lanes))
        self._pixels_done = self._photos_started = self._started_pixels = 0
        self._photo_count = len(remaining)

        def open_photo(item):
            self._check_cancelled()
            decoded = handed.pop("first", None)
            if decoded is not None and decoded[0] is item:
                _, image, manager = decoded
            else:
                manager = ImageManager()
                image = manager.load(item["image_path"])
            return _Photo(self, item, manager, image, info, shape, preview)

        last = [self.clock()]

        def finished(item, inference_seconds):
            now = self.clock()
            # Photos overlap: report each one's share of the batch's time, so the
            # queue's time estimate stays right for later batches.
            elapsed, last[0] = now - last[0], now
            self.emit(
                JobCompleted(
                    job_id=str(item["job_id"]),
                    elapsed_seconds=elapsed,
                    inference_seconds=max(inference_seconds, 0.0),
                    output_path=item["output_path"],
                    notes=" ".join(self.save_notes.pop(str(item["job_id"]), [])),
                )
            )

        try:
            returned = run_photo_batch(
                remaining,
                open_photo=open_photo,
                lanes=lanes,
                cancel=self.cancel_event,
                finished=finished,
                failed=lambda item, error: self.emit(
                    JobFailed(
                        job_id=str(item["job_id"]),
                        **failure_fields(error, item.get("output_path")),
                    )
                ),
                cancelled=lambda item: self.emit(JobCancelled(job_id=str(item["job_id"]))),
                lane_failed=lambda lane, error: self._lane_failed(model, lane, error),
                encoders=encoders,
                memory_budget=self.memory_budget,
            )
        finally:
            preview.close()
            handed.clear()
            for lane in lanes:
                self.lane_tile_counts[lane.unit] = (
                    self.lane_tile_counts.get(lane.unit, 0) + lane.tiles
                )
        return returned

    def photo_started(self, pixels: int) -> None:
        """A photo's tiles begin: the batch's size is that of the photos seen so far, scaled."""
        self._photos_started += 1
        self._started_pixels += pixels
        self.rate.total = float(self._started_pixels / self._photos_started * self._photo_count)

    def tile_done(self, pixels: int) -> None:
        self._pixels_done += pixels
        self.rate.update(self.clock(), float(self._pixels_done))

    def _lane_failed(self, model, lane: _Lane, error: BaseException) -> None:
        self._log(f"A batch lane on {lane.unit} stopped: {error}")
        # The next batch loads fresh runners instead of this one again.
        forget = getattr(model, "forget_batch_unit", None)
        if callable(forget):
            forget(lane.unit)

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Cancelled before the photo started.")

    def _log(self, message: str) -> None:
        self.emit(LogMessage(level="info", message=message))
