"""Translate image inference callbacks into stage and job progress events."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from localsr.core.hardware import get_memory_snapshot
from localsr.core.live_preview import LatestPreviewEncoder
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
    ) -> None:
        self.job_id = job_id
        self.pipeline = pipeline
        self.device = device
        self.emit = emit
        self.preview = preview
        self.clock = clock
        self.active_index = pipeline.primary_index
        self.active_stage = pipeline.primary
        self.active_span = 1 + int(pipeline.face is not None)
        self.inference_started_at: float | None = None
        self.last_memory_sample_at = 0.0
        self.memory_snapshot: dict[str, Any] = {}

    def start(self, index: int, stage: PipelineStage, *, span: int = 1) -> None:
        self.active_index = index
        self.active_stage = stage
        self.active_span = span
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
        self.emit(
            StageCompleted(
                job_id=self.job_id,
                stage_index=index,
                stage_count=len(self.pipeline.stages),
                stage_kind=stage.kind,
            )
        )

    def update(self, completed: int, total: int, active_tile_size: int) -> None:
        now = self.clock()
        elapsed = now - (
            self.inference_started_at if self.inference_started_at is not None else now
        )
        stage_count = len(self.pipeline.stages)
        fraction = max(0.0, min(1.0, completed / max(total, 1)))
        completed_weight = self.active_index + fraction * self.active_span
        percentage = completed_weight / max(stage_count, 1) * 100.0
        remaining = (
            elapsed / completed_weight * max(0.0, stage_count - completed_weight)
            if completed_weight > 0
            else 0.0
        )
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
            # Model loading must not inflate the ETA for every remaining tile.
            self.inference_started_at = self.clock()
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
