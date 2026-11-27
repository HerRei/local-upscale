"""Translate image inference callbacks into stage and job progress events."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from localsr.core.eta import RemainingTime
from localsr.core.hardware import get_memory_snapshot
from localsr.core.live_preview import LatestPreviewEncoder
from localsr.core.model_catalog import CATALOG_BY_ID
from localsr.core.pipeline import ImagePipeline, PipelineStage
from localsr.protocol.messages import (
    ProgressUpdate,
    StageCompleted,
    StageProgress,
    StageStarted,
    TileUpdate,
    WorkerMessage,
)


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

    def configure_work(self, width: int, height: int, upscale: int) -> None:
        pixels = width * height
        current = pixels
        weights = []
        for stage in self.pipeline.stages:
            model = CATALOG_BY_ID.get(stage.model_id)
            if stage.kind == "upscale":
                current *= upscale * upscale
            work = 512 * 512 if model and model.architecture == "DDColor" else current
            # The catalog time factor is already used for video stage timing; benchmark
            # references measure hardware, not relative speeds of individual models.
            weights.append(work * (model.time_factor if model else 1.0))
        self.weights = weights

    def start(self, index: int, stage: PipelineStage, *, span: int = 1) -> None:
        self.active_index = index
        self.active_stage = stage
        self.active_span = span
        self.stage_pixels_done = 0
        self.stage_pixels_total = 0
        self.remaining_time = None
        self.emit_start(index, stage)

    def emit_start(self, index: int, stage: PipelineStage) -> None:
        self.emit(
            StageStarted(
                job_id=self.job_id,
                stage_index=index,
                stage_count=len(self.pipeline.stages),
                stage_kind=stage.kind,
                model_id=stage.model_id,
                step=stage.step_id,
                fix=stage.fix or "",
            )
        )

    def complete(self, index: int, stage: PipelineStage) -> None:
        self.emit(
            StageCompleted(
                job_id=self.job_id,
                stage_index=index,
                stage_count=len(self.pipeline.stages),
                stage_kind=stage.kind,
                step=stage.step_id,
                fix=stage.fix or "",
            )
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
        if seconds is None and self.weighted_speed is not None:
            seconds = max(0, work - done) / max(work, 1) * active_weight * self.weighted_speed
        if seconds is None:
            return 0.0
        return seconds + future * (self.weighted_speed or 0.0)

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
        if output_width > 0 and output_height > 0:
            self.stage_pixels_total = int(output_width) * int(output_height)
        if phase == "reset":
            # Tiles start over at a smaller size; the speed in pixels per second still holds.
            self.stage_pixels_done = 0
            if self.remaining_time is not None:
                self.remaining_time.restart(self.clock(), self.stage_pixels_total)
        elif phase == "completed" and tile is not None:
            self.stage_pixels_done += int(tile.out_w) * int(tile.out_h)
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
