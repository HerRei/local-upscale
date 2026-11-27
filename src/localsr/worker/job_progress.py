"""Translate image inference callbacks into stage and job progress events."""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from localsr.core.eta import RemainingTime
from localsr.core.hardware import get_memory_snapshot
from localsr.core.live_preview import LatestPreviewEncoder
from localsr.core.model_catalog import CATALOG_BY_ID
from localsr.core.pipeline import ImagePipeline, PipelineStage
from localsr.core.speed_table import SpeedTable
from localsr.protocol.messages import (
    ProgressUpdate,
    StageCompleted,
    StageProgress,
    StageStarted,
    TileUpdate,
    WorkerMessage,
)

# What the steps already finished in this job took against what the table expected moves the
# estimate for the rest of the job, within these bounds.
MIN_CALIBRATION = 0.2
MAX_CALIBRATION = 5.0
# A stage with at least this many tiles leaves its first tile out of the measured speed: the
# first tile carries warm-up (compiling, caches) that does not grow with the image.
WARMUP_TILE_MIN_TILES = 3


def _subject(stage: PipelineStage) -> str:
    """What a stage is called in the speed table: its model, or its step if it has none."""
    if stage.model_id:
        return stage.model_id
    name = Path(stage.model_path).name
    return f"{stage.step_id}:{name}" if name else stage.step_id


class ImageJobProgress:
    def __init__(
        self,
        job_id: str,
        pipeline: ImagePipeline,
        device: str,
        emit: Callable[[WorkerMessage], None],
        preview: LatestPreviewEncoder,
        *,
        clock: Callable[[], float] = time.monotonic,
        rate: RemainingTime | None = None,
        request: str = "",
        backend: Callable[[PipelineStage], tuple[str, str] | None] | None = None,
        speed_table: SpeedTable | None = None,
    ) -> None:
        self.job_id = job_id
        self.pipeline = pipeline
        self.device = device
        self.emit = emit
        self.preview = preview
        self.clock = clock
        self.active_index = pipeline.primary_index if pipeline.primary_index is not None else 0
        self.active_stage = pipeline.primary or (
            pipeline.stages[-1] if pipeline.stages else PipelineStage("restore", "none", "")
        )
        self.active_span = 1 + int(pipeline.face is not None)
        self.inference_started_at: float | None = None
        self.last_memory_sample_at = 0.0
        self.memory_snapshot: dict[str, Any] = {}
        # The running stage's work in output pixels: tiles differ in size (edges, a smaller
        # tile size after a memory back-off), so tile counts do not measure work.
        self.stage_pixels_done = 0
        self.stage_pixels_total = 0
        self.remaining_time: RemainingTime | None = None
        # A batch's photos share one measured speed (the lanes' work together), fed by the
        # batch; alone a photo measures its own.
        self.rate = rate
        self.weights = [1.0] * len(pipeline.stages)
        self.weighted_speed: float | None = None
        self.last_percentage = 0.0
        # What the job asked for (device, precision, settings): the speed table finds a
        # step's last measurement by it, before the step has resolved to a backend.
        self.request = f"{device}|{request}"
        self.backend = backend
        self.speed_table = speed_table
        # The work of each stage: megapixels, or runs for a stage of fixed size.
        self.work_units = [1.0] * len(pipeline.stages)
        # Whether the weights are expected seconds (some step is in the speed table) or
        # only relative.
        self.weights_in_seconds = False
        self.measured_seconds = 0.0
        self.expected_seconds = 0.0
        self._reset_measurement()

    def _reset_measurement(self) -> None:
        self.first_started_at: float | None = None
        self.first_done_at = 0.0
        self.first_done_pixels = 0
        self.last_done_at = 0.0
        self.done_tiles = 0
        self.done_pixels = 0
        self.measurable = True
        self.resolved = ("", "")

    def configure_work(self, width: int, height: int, upscale: int) -> None:
        stages = self.pipeline.stages
        current = width * height
        fallback: list[float] = []
        units: list[float] = []
        for stage in stages:
            model = CATALOG_BY_ID.get(stage.model_id)
            if stage.kind == "upscale":
                current *= upscale * upscale
            fixed = bool(model and model.architecture == "DDColor")
            work = 512 * 512 if fixed else current
            units.append(1.0 if fixed else current / 1e6)
            # The catalog time factor is already used for video stage timing; benchmark
            # references measure hardware, not relative speeds of individual models.
            fallback.append(work * (model.time_factor if model else 1.0))
        self.work_units = units
        self.weights = fallback
        self.weights_in_seconds = False
        if self.rate is not None:
            return
        if self.speed_table is None:
            self.speed_table = SpeedTable()
        known: dict[int, float] = {}
        face_index = None
        if self.pipeline.face is not None and self.pipeline.primary_index is not None:
            face_index = self.pipeline.primary_index + 1
        for index in range(len(stages)):
            if index == face_index:
                continue
            seconds_per_unit = self.speed_table.lookup(self._subject_at(index), self.request)
            if seconds_per_unit is not None:
                known[index] = seconds_per_unit * units[index]
        if not known:
            return
        ratios = [seconds / fallback[index] for index, seconds in known.items() if fallback[index]]
        scale = statistics.median(ratios) if ratios else 1.0
        expected = [known.get(index, weight * scale) for index, weight in enumerate(fallback)]
        # A face stage runs inside its primary's measurement, which already covers it.
        if face_index is not None and self.pipeline.primary_index in known:
            expected[face_index] = 0.0
        self.weights = expected
        self.weights_in_seconds = True

    def _subject_at(self, index: int) -> str:
        """The speed table's name for the stage at ``index``; a primary with a face stage
        is measured as the two together."""
        stage = self.pipeline.stages[index]
        face = self.pipeline.face
        if face is not None and index == self.pipeline.primary_index:
            return f"{_subject(stage)}+{_subject(face)}"
        return _subject(stage)

    def start(self, index: int, stage: PipelineStage, *, span: int = 1) -> None:
        self.active_index = index
        self.active_stage = stage
        self.active_span = span
        self.stage_pixels_done = 0
        self.stage_pixels_total = 0
        self.remaining_time = None
        self._reset_measurement()
        self.emit_start(index, stage)

    def emit_start(self, index: int, stage: PipelineStage) -> None:
        self.emit(
            StageStarted(
                job_id=self.job_id,
                stage_index=index,
                stage_count=len(self.pipeline.stages),
                stage_kind=stage.kind,
                model_id=stage.model_id,
            )
        )

    def complete(self, index: int, stage: PipelineStage) -> None:
        if index == self.active_index:
            self._learn_speed(index)
        self.emit(
            StageCompleted(
                job_id=self.job_id,
                stage_index=index,
                stage_count=len(self.pipeline.stages),
                stage_kind=stage.kind,
            )
        )

    def _learn_speed(self, index: int) -> None:
        """Put the finished stage's measured speed in the speed table, and compare it with
        what was expected of it. A stage that was cut short, restarted at a smaller tile
        size or did no tiled work leaves nothing."""
        measurable, self.measurable = self.measurable, False
        if (
            not measurable
            or self.rate is not None
            or self.speed_table is None
            or self.first_started_at is None
            or self.done_tiles == 0
        ):
            return
        if self.done_tiles >= WARMUP_TILE_MIN_TILES:
            seconds = self.last_done_at - self.first_done_at
            pixels = self.done_pixels - self.first_done_pixels
        else:
            seconds = self.last_done_at - self.first_started_at
            pixels = self.done_pixels
        stage = self.pipeline.stages[index]
        model = CATALOG_BY_ID.get(stage.model_id)
        fixed = bool(model and model.architecture == "DDColor")
        units = 1.0 if fixed else pixels / 1e6
        if self.active_span > 1:
            # Its tile events mix the primary's and the faces', so the planned work stands,
            # less the share of it the measured stretch left out.
            units = self.work_units[index] * pixels / self.done_pixels
        if seconds <= 0 or units <= 0:
            return
        seconds_per_unit = seconds / units
        backend, precision = self.resolved
        self.speed_table.record(
            self._subject_at(index),
            backend or self.device.split(":", 1)[0],
            precision,
            self.request,
            seconds_per_unit,
        )
        if self.weights_in_seconds:
            end = index + self.active_span
            self.expected_seconds += sum(self.weights[index:end])
            self.measured_seconds += seconds_per_unit * self.work_units[index]

    def _calibration(self) -> float:
        """How much slower (above 1) or faster the finished stages of this job ran than the
        table expected, so load and heat carry over to the stages after them."""
        if self.expected_seconds <= 0 or self.measured_seconds <= 0:
            return 1.0
        return max(
            MIN_CALIBRATION, min(MAX_CALIBRATION, self.measured_seconds / self.expected_seconds)
        )

    def update(self, completed: int, total: int, active_tile_size: int) -> None:
        now = self.clock()
        elapsed = max(
            0.0,
            now - (self.inference_started_at if self.inference_started_at is not None else now),
        )
        stage_count = len(self.pipeline.stages)
        fraction = max(0.0, min(1.0, completed / max(total, 1)))
        if self.stage_pixels_total > 0 and completed < total:
            fraction = min(1.0, self.stage_pixels_done / self.stage_pixels_total)
        weights = self.weights
        active_weight = sum(weights[self.active_index : self.active_index + self.active_span])
        completed_weight = sum(weights[: self.active_index]) + fraction * active_weight
        percentage = completed_weight / max(sum(weights), 1) * 100.0
        if self.active_index + self.active_span < stage_count or completed < total:
            percentage = min(percentage, math.nextafter(100.0, 0.0))
        percentage = max(self.last_percentage, min(100.0, percentage))
        self.last_percentage = percentage
        remaining = self._remaining(now, completed, total)
        if now - self.last_memory_sample_at >= 1.0 or completed == total:
            try:
                self.memory_snapshot = get_memory_snapshot(self.device)
            except (OSError, RuntimeError, ValueError):
                self.memory_snapshot = {}
            self.last_memory_sample_at = now

        indexes = [(self.active_index, self.active_stage)]
        if self.pipeline.face is not None and self.active_stage is self.pipeline.primary:
            indexes.append((self.pipeline.primary_index + 1, self.pipeline.face))
        for index, stage in indexes:
            self.emit(
                StageProgress(
                    job_id=self.job_id,
                    stage_index=index,
                    stage_count=stage_count,
                    stage_kind=stage.kind,
                    completed_units=completed,
                    total_units=total,
                    percentage=percentage,
                )
            )
        memory = self.memory_snapshot
        self.emit(
            ProgressUpdate(
                job_id=self.job_id,
                completed_tiles=completed,
                total_tiles=total,
                percentage=percentage,
                elapsed_seconds=elapsed,
                estimated_remaining_seconds=remaining,
                active_tile_size=active_tile_size,
                device_free_memory=memory.get("device_free_memory", 0),
                device_allocated_memory=memory.get("device_allocated_memory", 0),
                system_ram_available=memory.get("system_ram_available", 0),
                system_memory_pressure_percent=memory.get("system_memory_pressure_percent", 0.0),
                system_memory_pressure_level=memory.get("system_memory_pressure_level", "unknown"),
                system_compressed_memory=memory.get("system_compressed_memory", 0),
                system_swap_used=memory.get("system_swap_used", 0),
                mps_tensor_allocated_memory=memory.get("mps_tensor_allocated_memory", 0),
                mps_driver_allocated_memory=memory.get("mps_driver_allocated_memory", 0),
                mps_recommended_max_memory=memory.get("mps_recommended_max_memory", 0),
            )
        )

    def _remaining(self, now: float, completed: int, total: int) -> float:
        """Estimate future stages using the latest measured weighted work rate."""
        if self.stage_pixels_total > 0:
            done, work = self.stage_pixels_done, self.stage_pixels_total
        else:
            done, work = completed, max(total, 1)
        if self.rate is not None:
            speed = self.rate.seconds_per_unit()
            seconds = None if speed is None else max(0, work - done) * speed
        else:
            if self.remaining_time is None:
                self.remaining_time = RemainingTime(work)
            self.remaining_time.update(now, done)
            speed = self.remaining_time.seconds_per_unit()
            seconds = self.remaining_time.remaining()
        active_weight = sum(self.weights[self.active_index : self.active_index + self.active_span])
        if speed is not None and active_weight > 0:
            self.weighted_speed = speed * work / active_weight
        future = sum(self.weights[self.active_index + self.active_span :])
        if self.weights_in_seconds:
            calibration = self._calibration()
            if seconds is None:
                if self.weighted_speed is None and self.expected_seconds <= 0:
                    return 0.0
                share = max(0, work - done) / max(work, 1)
                seconds = share * active_weight * calibration
            return seconds + future * calibration
        if seconds is None and self.weighted_speed is not None:
            seconds = max(0, work - done) / max(work, 1) * active_weight * self.weighted_speed
        if seconds is None:
            return 0.0
        return seconds + future * (self.weighted_speed or 0.0)

    def _resolve_backend(self) -> tuple[str, str]:
        """The backend and precision the engine settled on for the running stage (it has
        loaded its model by the first tile), or an empty answer when it cannot say."""
        if self.backend is None:
            return ("", "")
        try:
            found = self.backend(self.active_stage)
        except Exception:  # noqa: BLE001 - a missing label must not stop a job
            return ("", "")
        if (
            isinstance(found, tuple)
            and len(found) == 2
            and all(isinstance(part, str) for part in found)
        ):
            return (found[0].split(":", 1)[0], found[1])
        return ("", "")

    def tile(
        self,
        phase: str,
        tile: Any,
        tile_data: Any,
        completed: int,
        total: int,
        output_width: int,
        output_height: int,
        active_tile_size: int,
    ) -> None:
        if phase == "started" and self.inference_started_at is None:
            # Model loading must not count as time spent on the first tile.
            self.inference_started_at = self.clock()
        if phase == "started" and self.first_started_at is None:
            self.first_started_at = self.clock()
            self.resolved = self._resolve_backend()
        if output_width > 0 and output_height > 0:
            self.stage_pixels_total = int(output_width) * int(output_height)
        if phase == "reset":
            # Tiles start over at a smaller size; the speed in pixels per second still holds.
            self.stage_pixels_done = 0
            self.measurable = False
            if self.remaining_time is not None:
                self.remaining_time.restart(self.clock(), self.stage_pixels_total)
        elif phase == "completed" and tile is not None:
            pixels = int(tile.out_w) * int(tile.out_h)
            self.stage_pixels_done += pixels
            if self.done_tiles == 0:
                self.first_done_at, self.first_done_pixels = self.clock(), pixels
            self.last_done_at = self.clock()
            self.done_tiles += 1
            self.done_pixels += pixels
        if phase == "completed" and tile_data is not None:
            self.preview.submit(
                job_id=self.job_id,
                preview_kind="tile",
                pixels=tile_data,
                force=bool(total > 0 and completed >= total),
                output_x=int(tile.out_x),
                output_y=int(tile.out_y),
                output_width=int(tile.out_w),
                output_height=int(tile.out_h),
                image_width=int(output_width),
                image_height=int(output_height),
                active_tile_size=int(active_tile_size),
                stage_index=self.active_index,
            )
        self.emit(
            TileUpdate(
                job_id=self.job_id,
                stage_index=self.active_index,
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
                jpeg_base64="",
            )
        )
