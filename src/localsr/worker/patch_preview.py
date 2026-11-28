"""Run a plan on a square of one photo: the preview patch.

The window shows what the ticked fixes and the upscale will do on a part of the open photo
before a job starts. The square runs through the job's own step functions, so what it shows
is what the job makes; its results are kept in memory per file, region and step prefix, so
a changed last step runs only itself.
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from PIL import Image

import localsr.core.steps as step_registry
from localsr.core.image_formats import is_video_input
from localsr.core.image_io import ImageManager
from localsr.core.output_writer import default_work_directory
from localsr.core.pipeline import ImagePipeline, PipelineStage, resolve_image_pipeline
from localsr.core.steps import FACE_STAGE_KIND, is_model_step_stage
from localsr.protocol.messages import (
    PatchCompleted,
    PatchStage,
    PatchStarted,
    WarningMessage,
    WorkerMessage,
)

PATCH_CACHE_BYTES = 128 * 1024 * 1024
# DDColor looks at a 512 x 512 picture whatever it is given, so a bigger picture only costs
# time to reduce.
COLOUR_REDUCED_SIDE = 512
_COLOUR_PLANES_KEPT = 16
# DDColor costs about 0.9 GB while idle. It stays loaded between patches only where the device
# has room for it next to the models a job loads: a 16 GB Mac (11.8 GiB for the GPU) does, an
# 8 GB one (about 5 GiB) does not.
COLOUR_RESIDENT_MIN_TOTAL = 10 * 1024**3
COLOUR_RESIDENT_MIN_FREE = 2 * 1024**3
_GEOMETRIES_KEPT = 256

FACE_NOTE = "Face restoration is not shown in the preview patch."
MASK_NOTE = "A masked step is not shown in the preview patch."
ALPHA_NOTE = "This step changes transparency, which the preview patch cannot show."


def _strength_of(chip: PipelineStage | None) -> float:
    """How much of a fix the stage asks for, 1.0 when it names no strength."""
    return 1.0 if chip is None else float(chip.params.get("strength", 1.0))


def _file_identity(path: str) -> tuple:
    stat = os.stat(path)
    return (
        os.path.abspath(path),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def colour_may_stay_loaded(device: str) -> bool:
    """Whether this device has memory to keep the Colour model loaded between patches."""
    try:
        from localsr.core.hardware import get_memory_snapshot

        snapshot = get_memory_snapshot(device)
    except Exception:  # noqa: BLE001 - no reading means no reason to hold 0.9 GB
        return False
    return (
        snapshot["device_total_memory"] >= COLOUR_RESIDENT_MIN_TOTAL
        and snapshot["device_free_memory"] >= COLOUR_RESIDENT_MIN_FREE
    )


def release_resident_colour(engine: Any, cache: PatchCache | None) -> None:
    """Let go of the Colour model the patches kept: the patch was switched off."""
    if cache is None or cache.colour_model is None:
        return
    path, cache.colour_model = cache.colour_model, None
    engine.release_model(path)


def _model_identity(path: str) -> tuple:
    if not path:
        return ()
    try:
        stat = os.stat(path)
    except OSError:
        return (path,)
    return (os.path.abspath(path), stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True, slots=True)
class _Geometry:
    """Where the region sits in the picture and in its padded crop."""

    width: int
    height: int
    box: tuple[int, int, int, int]  # the padded crop, in source pixels
    offset: tuple[int, int]  # the region inside the crop
    size: tuple[int, int]  # the region, cut to the picture


@dataclass(frozen=True, slots=True)
class _Done:
    """One finished step: the padded picture the next step starts from, and the picture shown."""

    working: np.ndarray  # uint8, channels first
    png: bytes
    width: int
    height: int
    seconds: float
    scale: int
    skipped: bool = False
    note: str = ""

    @property
    def cost(self) -> int:
        return int(self.working.nbytes) + len(self.png)


class PatchCache:
    """Finished steps, 128 MB of them, oldest out first."""

    def __init__(self, budget: int = PATCH_CACHE_BYTES) -> None:
        self.budget = budget
        self.steps: OrderedDict[tuple, _Done] = OrderedDict()
        self.bytes = 0
        self.geometries: OrderedDict[tuple, _Geometry] = OrderedDict()
        # (colour planes, picture width, picture height) per file and colour model.
        self.planes: OrderedDict[tuple, tuple[np.ndarray, int, int]] = OrderedDict()
        # The Colour model a patch left loaded in the engine, so the next photo does not load
        # it again. The engine owns the weights; jobs, the idle release and memory pressure
        # release them there, and this only says which model the patches keep.
        self.colour_model: str | None = None

    def clear(self) -> None:
        self.steps.clear()
        self.geometries.clear()
        self.planes.clear()
        self.bytes = 0

    def get(self, key: tuple) -> _Done | None:
        done = self.steps.get(key)
        if done is not None:
            self.steps.move_to_end(key)
        return done

    def put(self, key: tuple, done: _Done) -> None:
        previous = self.steps.pop(key, None)
        if previous is not None:
            self.bytes -= previous.cost
        if done.cost > self.budget:
            return
        while self.steps and self.bytes + done.cost > self.budget:
            _, oldest = self.steps.popitem(last=False)
            self.bytes -= oldest.cost
        self.steps[key] = done
        self.bytes += done.cost

    def remember(self, store: OrderedDict, key: tuple, value: Any, limit: int) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > limit:
            store.popitem(last=False)


class _NullPreview:
    def submit(self, **_kwargs: Any) -> None:
        pass

    def flush(self) -> None:
        pass


class _NullProgress:
    """What a job's step functions report to; the patch reports its own steps."""

    def __init__(self, patch_id: str) -> None:
        self.job_id = patch_id
        self.preview = _NullPreview()

    def start(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def emit_start(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def complete(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def tile(self, *_args: Any, **_kwargs: Any) -> None:
        pass


@dataclass(slots=True)
class _Source:
    tensor: Any  # float32 (3, H, W), 0..1

    @property
    def width(self) -> int:
        return int(self.tensor.shape[-1])

    @property
    def height(self) -> int:
        return int(self.tensor.shape[-2])


def load_source(path: str, frame_index: int, cancel_event: threading.Event) -> _Source:
    """The picture the patch cuts from: the photo as a job decodes it, or a video's frame."""
    import torch

    if is_video_input(path):
        from localsr.core.video_io import decode_timed_frames

        index = max(0, int(frame_index))
        frames = decode_timed_frames(
            path, index, index, cancel_event, hdr_mode="tone_map", decoder_threads=1
        )
        try:
            frame = next(frames, None)
        finally:
            frames.close()
        if frame is None:
            raise ValueError("This video has no frame to preview.")
        rgb = np.ascontiguousarray(np.asarray(frame.rgb, dtype=np.uint8).transpose(2, 0, 1))
        return _Source(torch.from_numpy(rgb).float().div_(255.0))
    return _Source(ImageManager().load(path)["tensor"])


def _geometry(source: _Source, region: Mapping[str, int], padding: int) -> _Geometry:
    width, height = source.width, source.height
    x, y = int(region["x"]), int(region["y"])
    if x >= width or y >= height:
        raise ValueError("The preview region is outside the picture.")
    region_width = min(int(region["width"]), width - x)
    region_height = min(int(region["height"]), height - y)
    left, top = max(0, x - padding), max(0, y - padding)
    right = min(width, x + region_width + padding)
    bottom = min(height, y + region_height + padding)
    return _Geometry(
        width,
        height,
        (left, top, right, bottom),
        (x - left, y - top),
        (region_width, region_height),
    )


def _to_uint8(tensor: Any) -> np.ndarray:
    return tensor.detach().cpu().clamp(0, 1).mul(255).round().byte().numpy()


def _to_image(working: np.ndarray) -> dict[str, Any]:
    import torch

    return {"tensor": torch.from_numpy(working).float().div_(255.0)}


def _encode_png(picture: np.ndarray) -> bytes:
    encoded = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(picture.transpose(1, 2, 0))).save(
        encoded, format="PNG", compress_level=1
    )
    return encoded.getvalue()


def _nearest_index(count: int, source_count: int, start: int, stop: int) -> np.ndarray:
    """The cell of a ``count``-long plane that each of the pixels ``start`` to ``stop`` of a
    ``source_count``-long picture reads: the cell torch's nearest resize gives it."""
    scale = np.float32(count) / np.float32(source_count)
    cells = np.floor(np.arange(start, stop, dtype=np.float32) * scale).astype(np.int64)
    return np.minimum(cells, count - 1)


class PatchRunner:
    def __init__(
        self,
        *,
        engine: Any,
        model_adapter: Any,
        cancel_event: threading.Event,
        emit: Callable[[WorkerMessage], None],
        cache: PatchCache,
    ) -> None:
        from localsr.worker.image_job import ImageJobRunner

        self.engine = engine
        self.model_adapter = model_adapter
        self.cancel_event = cancel_event
        self.emit = emit
        self.cache = cache
        self._warnings: list[str] = []
        self.runner = ImageJobRunner(
            engine=engine,
            model_adapter=model_adapter,
            cancel_event=cancel_event,
            emit=self._collect,
            emit_preview=lambda _packet: None,
        )
        self.source: _Source | None = None

    def _collect(self, message: WorkerMessage) -> None:
        # The only message a step function sends that matters here is the one that says a fix
        # was left out; the rest is the job's own progress, which the patch reports itself.
        if isinstance(message, WarningMessage):
            self._warnings.append(message.message)

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Cancelled between preview steps.")

    def run(self, patch_id: str, data: Mapping[str, Any]) -> None:
        started_at = time.monotonic()
        self.emit(PatchStarted(patch_id=patch_id))
        pipeline, chips = self._plan(data)
        stage_count = len(chips)
        job = self._job_data(data)
        path = str(data["image_path"])
        frame = int(data.get("frame_index", -1))
        region = data["region"]
        region_key = tuple(int(region[name]) for name in ("x", "y", "width", "height"))
        padding = int(data.get("padding", 32))
        identity = _file_identity(path)
        base_key = (identity, frame, region_key, padding)
        keys = [self._stage_key(chip, job, pipeline) for chip in chips]
        # A fix with a strength below 1 also keeps its whole result under a key without the
        # strength, so a new strength mixes that result again and the model does not run.
        whole_keys = [self._stage_key(chip, job, pipeline, whole=True) for chip in chips]

        finished: list[_Done] = []
        for index in range(stage_count):
            done = self.cache.get((*base_key, tuple(keys[: index + 1])))
            if done is None:
                break
            finished.append(done)
            self._announce(patch_id, index, stage_count, done, cached=True)

        self.source = None
        geometry_key = base_key
        try:
            self.engine.neural_engine_enabled = bool(job.get("neural_engine", True))
            self.engine.status_callback = None
            self.model_adapter.status_callback = None
            self.model_adapter.cancel_event = self.cancel_event
            self.runner._take_engine_notes()
            keep = {chip.model_path for chip in chips if chip is not None and chip.model_path}
            if self.cache.colour_model is not None:
                keep.add(self.cache.colour_model)
            self.engine.release_models_except(keep)
            geometry = self.cache.geometries.get(geometry_key)
            if len(finished) < stage_count and (not finished or geometry is None):
                source = self._load(path, frame)
                geometry = _geometry(source, region, padding)
                self.cache.remember(self.cache.geometries, geometry_key, geometry, _GEOMETRIES_KEPT)
            for index in range(len(finished), stage_count):
                self._check_cancelled()
                chip = chips[index]
                if finished:
                    working, scale = finished[-1].working, finished[-1].scale
                else:
                    assert geometry is not None and self.source is not None
                    left, top, right, bottom = geometry.box
                    working = _to_uint8(self.source.tensor[:, top:bottom, left:right])
                    scale = 1
                assert geometry is not None
                strength = _strength_of(chip)
                ran = True
                if strength < 1.0:
                    whole_key = (*base_key, (*keys[:index], whole_keys[index]))
                    whole = self.cache.get(whole_key)
                    ran = whole is None
                    if whole is None:
                        whole = self._run_one(
                            (patch_id, index, stage_count),
                            chip,
                            pipeline,
                            job,
                            working,
                            scale,
                            geometry,
                            (identity, frame, path),
                            whole=True,
                        )
                        self.cache.put(whole_key, whole)
                    done = self._remix(whole, working, strength, geometry)
                else:
                    done = self._run_one(
                        (patch_id, index, stage_count),
                        chip,
                        pipeline,
                        job,
                        working,
                        scale,
                        geometry,
                        (identity, frame, path),
                    )
                self.cache.put((*base_key, tuple(keys[: index + 1])), done)
                finished.append(done)
                self._announce(patch_id, index, stage_count, done, cached=not ran)
        finally:
            self.source = None
        self.emit(PatchCompleted(patch_id=patch_id, elapsed_seconds=time.monotonic() - started_at))

    def _announce(
        self, patch_id: str, index: int, count: int, done: _Done, *, cached: bool
    ) -> None:
        if done.skipped:
            self.emit(
                PatchStage(
                    patch_id=patch_id,
                    stage_index=index,
                    stage_count=count,
                    state="skipped",
                    note=done.note,
                )
            )
            return
        self.emit(
            PatchStage(
                patch_id=patch_id,
                stage_index=index,
                stage_count=count,
                state="done",
                png_base64=base64.b64encode(done.png).decode("ascii"),
                width=done.width,
                height=done.height,
                elapsed_seconds=done.seconds,
                cached=cached,
            )
        )

    def _load(self, path: str, frame: int) -> _Source:
        if self.source is None:
            self.source = load_source(path, frame, self.cancel_event)
        return self.source

    @staticmethod
    def _job_data(data: Mapping[str, Any]) -> dict[str, Any]:
        """The fields the job's step functions read. The output is never written, but the
        disk check of the upscaler reads where a job would have put it."""
        work = str(default_work_directory())
        return {
            "device": data["device"],
            "precision": data["precision"],
            "tile_size": int(data["tile_size"]),
            "halo": int(data["halo"]),
            "safe_memory": bool(data["safe_memory"]),
            "neural_engine": bool(data.get("neural_engine", True)),
            "output_scale": data.get("output_scale"),
            "output_bit_depth": 8,
            "output_format": "png",
            "output_path": os.path.join(work, "patch.png"),
            "output_temporary_directory": work,
        }

    @staticmethod
    def _plan(data: Mapping[str, Any]) -> tuple[ImagePipeline, list[PipelineStage | None]]:
        """The stages a job would resolve, and one entry per chip: ``None`` is Faces, which is
        fused with the upscaler in a job and is not run here."""
        raw = [item for item in data["stages"] if isinstance(item, dict)]
        faced = any(item.get("kind") == FACE_STAGE_KIND for item in raw)
        raw = [item for item in raw if item.get("kind") != FACE_STAGE_KIND]
        if not raw:
            raise ValueError("The preview has no step to run.")
        primary_path = ""
        for matches in (
            lambda item: item.get("role") == "primary",
            lambda item: item.get("kind") == "upscale",
            lambda item: item.get("kind") == "restore" and not item.get("role"),
        ):
            found = next((item for item in raw if matches(item)), None)
            if found is not None:
                primary_path = str(found.get("model_path") or "")
                break
        pipeline = resolve_image_pipeline(
            raw_stages=raw,
            model_path=primary_path,
            output_scale=data.get("output_scale"),
        )
        chips: list[PipelineStage | None] = list(pipeline.stages)
        if faced and pipeline.primary_index is not None:
            chips.insert(pipeline.primary_index + 1, None)
        return pipeline, chips

    @staticmethod
    def _stage_key(
        chip: PipelineStage | None,
        job: Mapping[str, Any],
        pipeline: ImagePipeline,
        *,
        whole: bool = False,
    ) -> tuple:
        """What a finished step is cached under; ``whole`` leaves the strength out."""
        if chip is None:
            return ("face",)
        params = {k: v for k, v in chip.params.items() if not (whole and k == "strength")}
        return (
            chip.kind,
            chip.step_id,
            chip.fix,
            chip.model_id,
            _model_identity(chip.model_path),
            json.dumps(params, sort_keys=True, default=str),
            chip.mask_path,
            job["device"],
            job["precision"],
            job["output_scale"] if chip is pipeline.primary else None,
        )

    def _run_one(
        self,
        position: tuple[str, int, int],
        chip: PipelineStage | None,
        pipeline: ImagePipeline,
        job: dict[str, Any],
        working: np.ndarray,
        scale: int,
        geometry: _Geometry,
        source: tuple[tuple, int, str],
        *,
        whole: bool = False,
    ) -> _Done:
        """Run one step. With ``whole`` a fix runs at full strength, whatever it asks for."""
        patch_id, index, count = position
        identity, frame, path = source
        skip_note = self._skip_reason(chip)
        if skip_note:
            return self._finish(working, scale, geometry, 0.0, skipped=True, note=skip_note)
        assert chip is not None
        self.emit(
            PatchStage(patch_id=patch_id, stage_index=index, stage_count=count, state="running")
        )
        started_at = time.monotonic()
        self._warnings.clear()
        is_primary = pipeline.primary is not None and chip is pipeline.primary
        if whole and "strength" in chip.params:
            stripped = replace(
                chip, params={k: v for k, v in chip.params.items() if k != "strength"}
            )
            pipeline = ImagePipeline(
                tuple(stripped if item is chip else item for item in pipeline.stages),
                pipeline.primary_index,
            )
            chip = stripped
        info = self.model_adapter.inspect(chip.model_path) if chip.model_path else None
        step = step_registry.STEPS_BY_ID.get(chip.step_id)
        brings_model = step is not None and is_model_step_stage(step)
        if info is not None and info.architecture == "DDColor" and not brings_model:
            result = self._colour(chip, info, working, geometry, job, identity, frame, path)
            result_scale = scale
        elif is_primary:
            result, result_scale = self._primary(chip, pipeline, working, scale, job)
        else:
            image = self.runner._run_stage(
                _to_image(working), chip, index, job, _NullProgress(patch_id), []
            )
            result = _to_uint8(image["tensor"])
            result_scale = scale
            if self._warnings:
                return self._finish(
                    working, scale, geometry, 0.0, skipped=True, note=self._warnings[0]
                )
        return self._finish(result, result_scale, geometry, time.monotonic() - started_at)

    def _remix(
        self, whole: _Done, working: np.ndarray, strength: float, geometry: _Geometry
    ) -> _Done:
        """The fix's whole result mixed with the picture it started from, as a job mixes it."""
        if whole.skipped:
            return whole
        from localsr.worker.image_job import apply_strength

        mixed = apply_strength(strength, _to_image(working)["tensor"], whole.working)
        return self._finish(np.ascontiguousarray(mixed), whole.scale, geometry, whole.seconds)

    @staticmethod
    def _skip_reason(chip: PipelineStage | None) -> str:
        if chip is None:
            return FACE_NOTE
        if chip.mask_path:
            return MASK_NOTE
        step = step_registry.STEPS_BY_ID.get(chip.step_id)
        if step is not None and step.output_alpha:
            return ALPHA_NOTE
        return ""

    @staticmethod
    def _finish(
        working: np.ndarray,
        scale: int,
        geometry: _Geometry,
        seconds: float,
        *,
        skipped: bool = False,
        note: str = "",
    ) -> _Done:
        """Cut the padding off, as many pixels as the picture has grown, for the picture shown."""
        offset_x, offset_y = geometry.offset
        width, height = geometry.size
        picture = working[
            :,
            offset_y * scale : (offset_y + height) * scale,
            offset_x * scale : (offset_x + width) * scale,
        ]
        return _Done(
            working=working,
            png=b"" if skipped else _encode_png(picture),
            width=int(picture.shape[2]),
            height=int(picture.shape[1]),
            seconds=seconds,
            scale=scale,
            skipped=skipped,
            note=note,
        )

    def _primary(
        self,
        chip: PipelineStage,
        pipeline: ImagePipeline,
        working: np.ndarray,
        scale: int,
        job: Mapping[str, Any],
    ) -> tuple[np.ndarray, int]:
        with ExitStack() as resources:
            writer, info = self.runner._primary(
                _to_image(working), pipeline, job, _NullProgress(""), resources
            )
            pixels = np.array(writer.get_array(), copy=True)
        final = int(job.get("output_scale") or info.scale)
        if info.scale > 1 and final < info.scale:
            # A job resizes to the size asked for when it saves; the patch has no save, so the
            # picture is resized here, before the steps that follow.
            height, width = (
                pixels.shape[1] * final // info.scale,
                pixels.shape[2] * final // info.scale,
            )
            pixels = np.stack(
                [
                    np.asarray(
                        Image.fromarray(plane).resize((width, height), Image.Resampling.LANCZOS)
                    )
                    for plane in pixels
                ]
            )
            return pixels, scale * final
        return pixels, scale * info.scale

    def _colour(
        self,
        chip: PipelineStage,
        info: Any,
        working: np.ndarray,
        geometry: _Geometry,
        job: Mapping[str, Any],
        identity: tuple,
        frame: int,
        path: str,
    ) -> np.ndarray:
        """Colour the padded crop with colours guessed from the whole picture.

        A tight crop changes the model's guess, so the planes come from the whole picture
        reduced; they are kept per file and model, and moving the patch reuses them.
        """
        from localsr.image_models.ddcolor.engine import lab_to_rgb, rgb_to_lab

        key = (identity, frame, _model_identity(chip.model_path), job["device"], job["precision"])
        planes = self.cache.planes.get(key)
        if planes is None:
            planes = self._colour_planes(chip, info, job, path, frame)
            self.cache.remember(self.cache.planes, key, planes, _COLOUR_PLANES_KEPT)
        ab, width, height = planes
        left, top, right, bottom = geometry.box
        rows = _nearest_index(ab.shape[1], height, top, bottom)
        columns = _nearest_index(ab.shape[2], width, left, right)
        crop_ab = ab[:, rows][:, :, columns].transpose(1, 2, 0)
        rgb = working.transpose(1, 2, 0).astype(np.float32) / 255.0
        lightness = rgb_to_lab(rgb)[:, :, :1]
        coloured = lab_to_rgb(np.concatenate((lightness, crop_ab), axis=-1))
        return np.ascontiguousarray(
            np.rint(coloured * 255.0).clip(0, 255).astype(np.uint8).transpose(2, 0, 1)
        )

    def _colour_planes(
        self, chip: PipelineStage, info: Any, job: Mapping[str, Any], path: str, frame: int
    ) -> tuple[np.ndarray, int, int]:
        import cv2

        from localsr.image_models.ddcolor.engine import predict_ab

        source = self._load(path, frame)
        picture = source.tensor.permute(1, 2, 0).numpy().astype(np.float32, copy=False)
        longest = max(source.width, source.height)
        if longest > COLOUR_REDUCED_SIDE:
            factor = COLOUR_REDUCED_SIDE / longest
            picture = cv2.resize(
                picture,
                (max(1, round(source.width * factor)), max(1, round(source.height * factor))),
                interpolation=cv2.INTER_AREA,
            )
        self._check_cancelled()
        try:
            device, _precision = self.engine.load_model(
                chip.model_path,
                job["device"],
                job["precision"],
                info,
                safe_memory=bool(job["safe_memory"]),
            )
            self._check_cancelled()
            ab = predict_ab(
                self.engine.active_model, picture, device=device, cancel_event=self.cancel_event
            )
        finally:
            # The model stays loaded for the next photo while the patch is on and the device
            # has room: loading it costs more than the prediction. Anything that needs the
            # memory releases it through the engine like every other warm model.
            if colour_may_stay_loaded(str(job["device"])):
                self.cache.colour_model = chip.model_path
            else:
                self.cache.colour_model = None
                self.engine.release_model(chip.model_path)
            self.model_adapter.release()
        return ab[0].numpy(), source.width, source.height
