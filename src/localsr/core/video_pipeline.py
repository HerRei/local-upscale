"""Frame-by-frame video super-resolution pipeline.

This is the simple, flicker-prone pipeline: each frame is restored
independently using the existing image InferenceEngine. A future
VideoSRAdapter (RealBasicVSR/RVRT) will plug into the same loop and
replace the per-frame call with a multi-frame temporal call without
changing the decode/encode/cancel/progress plumbing here.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import torch

from .audio_denoise import cleaned_audio_source
from .deflicker import AnalysisFrame, deflicker_stream
from .disk_space import require_video_room
from .eta import RemainingTime
from .face_compositing import blend_tile_outputs, classify_tile
from .face_detection import FaceMask, detect_faces, face_mask_for_tile_core
from .frame_interpolation import ORDERS, InterpolationTarget
from .inference import InferenceEngine
from .media_codecs import EXTERNAL_OUTPUT_CODECS, validate_output
from .tiling import generate_tiles
from .video_chain import VideoChainContext, build_video_stages, run_video_chain
from .video_chain import rgb_to_tensor as rgb_to_tensor
from .video_io import (
    TimedVideoFrame,
    _should_keep_partial,
    count_video_frames,
    decode_timed_frames,
    encode_video,
    probe_video,
    selected_frame_count,
    validate_hdr_encoder,
)


def _prefetch(items, depth: int):
    """Run an iterator on a background thread, up to ``depth`` items ahead.

    Decoding, restoring and encoding then overlap instead of taking turns: the
    encoder and large array copies release the GIL while the next frame is
    restored on the Neural Engine or GPU. Items and the producer's exception
    arrive in order; closing the consumer stops the producer after its current
    item.
    """
    buffer: queue.Queue = queue.Queue(maxsize=depth)
    stopped = threading.Event()
    finished = object()

    def offer(entry) -> bool:
        while not stopped.is_set():
            try:
                buffer.put(entry, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def produce() -> None:
        try:
            for item in items:
                if not offer((item, None)):
                    return
            offer((finished, None))
        except BaseException as error:  # noqa: BLE001 - re-raised by the consumer
            offer((finished, error))

    worker = threading.Thread(target=produce, name="localsr-video-prefetch", daemon=True)
    worker.start()
    try:
        while True:
            item, error = buffer.get()
            if item is finished:
                if error is not None:
                    raise error
                return
            yield item
    finally:
        stopped.set()
        worker.join()
        close = getattr(items, "close", None)
        if callable(close):
            close()


def _deflicker_frames(
    restored: Iterator[
        TimedVideoFrame
        | np.ndarray
        | tuple[TimedVideoFrame | np.ndarray, AnalysisFrame | np.ndarray]
    ],
    window: int = 3,
    cancel_event: threading.Event | None = None,
    on_frame: Callable[[int], None] | None = None,
    keep: Callable[[], bool] | None = None,
    *,
    device_str: str = "cpu",
) -> Iterator[TimedVideoFrame | np.ndarray]:
    """Source-driven de-flicker wrapper preserving the streaming/cancel contract.

    ``window`` is kept for compatibility with callers that still pass it and is
    ignored by the new engine (which uses a fixed +-3 frame shot window).
    """
    _ = window

    def paired():
        for item in restored:
            if isinstance(item, tuple):
                yield item
            else:
                source = item.rgb if isinstance(item, TimedVideoFrame) else item
                yield (item, source)

    yield from deflicker_stream(
        paired(),
        device_str=device_str,
        cancel_event=cancel_event,
        on_frame=on_frame,
        keep=keep,
    )


@dataclass(frozen=True)
class VideoFixStage:
    model_path: str
    model_info: object
    model_id: str = ""
    halo: int = 0
    fix: str | None = None
    time_factor: float = 1.0
    step_id: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.step_id:
            if self.fix:
                resolved = self.fix
            elif getattr(self.model_info, "architecture", "") == "DDColor":
                resolved = "colour"
            else:
                resolved = "restore"
            object.__setattr__(self, "step_id", resolved)


# Keyframe spacing of the temporal video colour (see video_colour.colour_frames).
COLOUR_KEYFRAME_INTERVAL = 12


@dataclass
class VideoJobConfig:
    video_path: str
    model_path: str
    output_video_path: str
    model_info: object | None
    device_str: str
    precision_str: str
    tile_size: int
    halo: int
    safe_memory: bool
    container: str = "mp4"
    video_codec: str = "av1"
    crf: int = 18
    start_frame: int | None = None
    end_frame: int | None = None
    fps_override: float | None = None
    face_model_path: str | None = None
    face_model_info: object | None = None
    face_detection_interval: int = 5
    deflicker: bool = False
    # Kept in requests and settings for compatibility; ignored by the new deflicker engine.
    deflicker_window: int = 3
    output_scale: int | None = None
    face_fidelity: float = 0.7
    hdr_mode: str = "reject"
    temporary_directory: str | None = None
    # A MediaBridge (system codecs and/or the user's FFmpeg) for formats LocalSR omits.
    external_ffmpeg: object | None = None
    # Source of audio/subtitles when it differs from the decoded picture source.
    audio_path: str | None = None
    # Optional frame interpolation (see frame_interpolation.py): "off", "x2", "x4",
    # "fps60" or "fps120", run before or after the upscale.
    interpolation: str = "off"
    interpolation_order: str = "after_upscale"
    # Optional speech audio cleanup via DeepFilterNet3 ONNX.
    audio_denoise: bool = False
    audio_denoise_bundle_dir: str | None = None
    audio_denoiser: object | None = None
    # The worker resolves the initial codec before inference; retain retry intent.
    automatic_codec: bool = False
    preprocess_stages: tuple[VideoFixStage, ...] = ()
    primary_time_factor: float = 1.0
    keep_partial: bool = True


@dataclass
class VideoJobResult:
    output_path: str
    frames_processed: int
    elapsed_seconds: float
    inference_seconds: float
    partial: bool = False
    kept_seconds: float = 0.0
    kept_frames: int = 0


def run_video_job(
    config: VideoJobConfig,
    engine: InferenceEngine,
    cancel_event: threading.Event,
    frame_started_cb: Callable[[int, int], None] | None = None,
    frame_completed_cb: Callable[[int, int, str], None] | None = None,
    enhanced_frame_cb: Callable[[int, int, np.ndarray], None] | None = None,
    progress_cb: Callable[[int, int, float, float | None], None] | None = None,
    tile_callback: Callable[..., None] | None = None,
    tile_progress_cb: Callable[..., None] | None = None,
    warning_callback: Callable[[str], None] | None = None,
    source_frame_cb: Callable[[int, int, np.ndarray], None] | None = None,
    stage_cb: Callable[..., None] | None = None,
    audio_progress_cb: Callable[[int, int, float, float | None], None] | None = None,
    synthesizer=None,
    keep_partial: bool | Callable[[], bool] | None = None,
) -> VideoJobResult:
    """Run a video upscale job end-to-end.

    frame_started_cb(frame_index, total_frames) and
    frame_completed_cb(frame_index, total_frames, jpeg_b64) are called
    per frame. progress_cb(frames_done, total_frames, elapsed_seconds, remaining_seconds)
    is called after each frame; the remaining time is None until the speed is measured. All callbacks are optional. With
    ``config.interpolation`` set, ``synthesizer`` makes the in-between frames.

    Sources in a format LocalSR does not include are first converted to a
    lossless intermediate by the user's selected FFmpeg, if any. A video only
    the system codecs decode is read from them while the job runs instead.
    """
    from dataclasses import replace

    from .media_bridge import automatic_video_codec, decodable_source, validate_export

    # Restore's last fix is represented as the primary model by the UI. Treat
    # DDColor as a fix so the same temporal stage handles both restore modes.
    if getattr(config.model_info, "architecture", "") == "DDColor":
        config = replace(
            config,
            preprocess_stages=(
                *config.preprocess_stages,
                VideoFixStage(
                    model_path=config.model_path,
                    model_info=config.model_info,
                    fix="colour",
                    time_factor=config.primary_time_factor,
                ),
            ),
            model_path="",
            model_info=None,
        )

    if config.video_codec == "auto":
        config = replace(
            config,
            automatic_codec=True,
            video_codec=automatic_video_codec(
                config.external_ffmpeg, config.container, config.hdr_mode
            ),
        )

    validate_output(config.video_codec, config.container)
    validate_export(config.external_ffmpeg, config.video_codec, config.container)
    with decodable_source(
        config.video_path,
        ffmpeg=config.external_ffmpeg,
        temporary_directory=config.temporary_directory,
        cancel_event=cancel_event,
        output_container=config.container,
        on_convert=(lambda: stage_cb("external_decode")) if stage_cb else None,
        decode_audio=bool(config.audio_denoise and config.fps_override is None),
        stream_video=True,
    ) as (frame_source, audio_source):
        return _run_video_job(
            replace(config, video_path=frame_source, audio_path=audio_source),
            engine,
            cancel_event,
            frame_started_cb=frame_started_cb,
            frame_completed_cb=frame_completed_cb,
            enhanced_frame_cb=enhanced_frame_cb,
            progress_cb=progress_cb,
            tile_callback=tile_callback,
            tile_progress_cb=tile_progress_cb,
            warning_callback=warning_callback,
            source_frame_cb=source_frame_cb,
            stage_cb=stage_cb,
            audio_progress_cb=audio_progress_cb,
            synthesizer=synthesizer,
            keep_partial=keep_partial,
        )


def _run_video_job(
    config: VideoJobConfig,
    engine: InferenceEngine,
    cancel_event: threading.Event,
    *,
    frame_started_cb,
    frame_completed_cb,
    enhanced_frame_cb,
    progress_cb,
    tile_callback,
    tile_progress_cb,
    warning_callback,
    source_frame_cb,
    stage_cb,
    audio_progress_cb=None,
    synthesizer=None,
    keep_partial: bool | Callable[[], bool] | None = None,
) -> VideoJobResult:
    # Python 3.11's Windows monotonic clock can tick more slowly than a tile.
    # Measure short inference intervals with the highest-resolution clock so
    # a completed tile does not incorrectly report zero elapsed time / ETA.
    job_started_at = time.perf_counter()
    inference_started_at: float | None = None

    probe = probe_video(config.video_path)
    preserve_hdr = bool(probe.hdr_format and config.hdr_mode == "preserve")
    transfer = 18 if probe.hdr_format == "HLG" else 16
    precision_str = "fp32" if preserve_hdr else config.precision_str
    if preserve_hdr:
        validate_hdr_encoder(config.video_codec, config.external_ffmpeg, config.container)
        if config.deflicker:
            raise ValueError("De-flicker is not yet supported with HDR preservation.")
        if any(
            stage.step_id == "colour" or stage.fix == "colour" for stage in config.preprocess_stages
        ):
            raise ValueError("Colour is not yet supported with HDR preservation.")
    for stage in config.preprocess_stages:
        if stage.model_info is None:
            continue
        if int(getattr(stage.model_info, "scale", 0)) != 1:
            raise ValueError("A preprocessing restoration checkpoint must have native scale 1×.")
        if (
            int(getattr(stage.model_info, "in_channels", 3)) != 3
            or int(getattr(stage.model_info, "out_channels", 3)) != 3
        ):
            raise ValueError("A fix must preserve the working frame channels.")
    if config.fps_override is not None and (
        not np.isfinite(config.fps_override) or config.fps_override <= 0
    ):
        raise ValueError("Video FPS override must be a finite positive number.")
    fps = config.fps_override if config.fps_override else probe.fps
    if fps <= 0:
        fps = 25.0
    deinterlace = next(
        (stage for stage in config.preprocess_stages if stage.step_id == "deinterlace"), None
    )
    deinterlace_active = deinterlace is not None and not (
        deinterlace.params.get("parity", "auto") == "auto" and probe.field_order == "progressive"
    )
    if deinterlace is not None and not deinterlace_active:
        add_note = getattr(engine, "add_note", None)
        if callable(add_note):
            add_note("Already progressive · kept as it is")
    field_mode = deinterlace_active and deinterlace.params.get("mode", "frame") == "field"
    if field_mode:
        fps *= 2
    interpolation = _interpolation_target(config, fps, preserve_hdr, synthesizer)
    if interpolation is not None and not interpolation.applies_to(fps):
        if warning_callback:
            warning_callback(
                f"This video already plays at {fps:g} fps, so frame interpolation to "
                f"{interpolation.output_fps(fps):g} fps was skipped."
            )
        interpolation = None
    before_upscale = interpolation is not None and config.interpolation_order == "before_upscale"

    total_frames = selected_frame_count(probe.frame_count, config.start_frame, config.end_frame)
    if field_mode:
        total_frames *= 2
    if before_upscale and interpolation is not None:
        total_frames = interpolation.expected_frames(total_frames, fps)
    # One estimate for the tile and the frame reports. Model loading and the first tile,
    # which compiles and warms up, are not part of the speed it measures.
    remaining_time = RemainingTime(total_frames) if total_frames > 0 else None

    model_info = config.model_info
    native_scale = int(getattr(model_info, "scale", 1)) if model_info is not None else 1
    output_scale = int(config.output_scale or native_scale)
    if model_info is not None:
        if output_scale < 1 or output_scale > native_scale:
            raise ValueError(
                f"Video output scale must be between 1 and the model's native {native_scale}× scale."
            )
    elif output_scale != 1:
        raise ValueError("Video output scale must be 1 when no upscaling model is used.")
    # At 1×, an upscaling model (native scale > 1) or empty model is skipped;
    # 1× restoration models (Restore task) keep running as usual.
    skip_primary_sr = output_scale == 1 and (model_info is None or native_scale > 1)
    if preserve_hdr:
        checked_infos = (
            *(stage.model_info for stage in config.preprocess_stages),
            *(() if skip_primary_sr else (config.model_info, config.face_model_info)),
        )
        for info in checked_infos:
            if info is not None and str(getattr(info, "architecture", "")).upper() != "HAT":
                raise ValueError("HDR preservation currently supports HAT models only (Labs).")
    output_width = probe.width * output_scale
    output_height = probe.height * output_scale
    from .media_bridge import validate_export

    validate_export(
        config.external_ffmpeg,
        config.video_codec,
        config.container,
        size=(output_width, output_height),
        fps=float(fps),
    )
    require_video_room(
        {
            "temporary_directory": config.temporary_directory,
            "output_video_path": config.output_video_path,
        },
        output_width,
        output_height,
        total_frames,
        master=config.video_codec in EXTERNAL_OUTPUT_CODECS,
    )

    rate_unchanged = config.fps_override is None
    raw_audio_source = config.audio_path or config.video_path
    with cleaned_audio_source(
        raw_audio_source,
        enabled=bool(config.audio_denoise and rate_unchanged and probe.has_audio),
        bundle_dir=config.audio_denoise_bundle_dir,
        denoiser=config.audio_denoiser,  # type: ignore[arg-type]
        temporary_directory=config.temporary_directory,
        output_video_path=config.output_video_path,
        cancel_event=cancel_event,
        stage_cb=stage_cb,
        progress_cb=audio_progress_cb,
        started_at=job_started_at,
    ) as (effective_audio_source, reencode_audio):
        # Audio cleanup reports its own percentage up to 100; the frame loop that comes
        # next (and its model loading) starts a new step, or the app reads 100 as saving.
        if stage_cb is not None:
            stage_cb("reading_frames")
        return _run_video_frames(
            config,
            engine,
            cancel_event,
            probe=probe,
            preserve_hdr=preserve_hdr,
            transfer=transfer,
            precision_str=precision_str,
            fps=fps,
            interpolation=interpolation,
            before_upscale=before_upscale,
            total_frames=total_frames,
            remaining_time=remaining_time,
            model_info=model_info,
            native_scale=native_scale,
            output_scale=output_scale,
            skip_primary_sr=skip_primary_sr,
            output_width=output_width,
            output_height=output_height,
            job_started_at=job_started_at,
            inference_started_at=inference_started_at,
            effective_audio_source=effective_audio_source,
            raw_audio_source=raw_audio_source,
            reencode_audio=reencode_audio,
            rate_unchanged=rate_unchanged,
            frame_started_cb=frame_started_cb,
            frame_completed_cb=frame_completed_cb,
            enhanced_frame_cb=enhanced_frame_cb,
            progress_cb=progress_cb,
            tile_callback=tile_callback,
            tile_progress_cb=tile_progress_cb,
            warning_callback=warning_callback,
            source_frame_cb=source_frame_cb,
            stage_cb=stage_cb,
            synthesizer=synthesizer,
            keep_partial=keep_partial,
        )


def _video_has_colour(config: "VideoJobConfig", sample_frames: int = 3) -> bool:
    """True when the first frames carry real colour (the photo colour check, per frame)."""
    import cv2

    from localsr.image_models.ddcolor import COLORFUL_INPUT_CHROMA_THRESHOLD

    start = config.start_frame or 0
    # Closed when done: the helper decoding a streamed source stops with it.
    with closing(
        decode_timed_frames(
            config.video_path,
            start_frame=start,
            end_frame=start + sample_frames,
            hdr_mode=config.hdr_mode,
        )
    ) as frames:
        for _, item in zip(range(sample_frames), frames, strict=False):
            rgb = item.rgb
            if rgb.dtype != np.uint8:
                rgb = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
            lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
            chroma = float(np.hypot(lab[:, :, 1] - 128.0, lab[:, :, 2] - 128.0).mean())
            if chroma > COLORFUL_INPUT_CHROMA_THRESHOLD:
                return True
    return False


def _run_video_frames(
    config: VideoJobConfig,
    engine: InferenceEngine,
    cancel_event: threading.Event,
    *,
    probe,
    preserve_hdr: bool,
    transfer: int,
    precision_str: str,
    fps: float,
    interpolation,
    before_upscale: bool,
    total_frames: int,
    remaining_time,
    model_info,
    native_scale: int,
    output_scale: int,
    skip_primary_sr: bool,
    output_width: int,
    output_height: int,
    job_started_at: float,
    inference_started_at: float | None,
    effective_audio_source: str,
    raw_audio_source: str,
    reencode_audio: bool,
    rate_unchanged: bool,
    frame_started_cb,
    frame_completed_cb,
    enhanced_frame_cb,
    progress_cb,
    tile_callback,
    tile_progress_cb,
    warning_callback,
    source_frame_cb,
    stage_cb,
    synthesizer=None,
    keep_partial: bool | Callable[[], bool] | None = None,
) -> VideoJobResult:
    # A frame pair that does not fit next to the fix and upscaling models may park them
    # in system memory; the engine brings them back before its next frame.
    park = getattr(engine, "park_models", None)
    if interpolation is not None and callable(park) and hasattr(synthesizer, "memory_relief"):
        synthesizer.memory_relief = park
    # Decide about Colour before loading anything, so a skipped DDColor never
    # occupies graphics memory.
    colour_indices = [
        i
        for i, stage in enumerate(config.preprocess_stages)
        if stage.step_id == "colour"
        or stage.fix == "colour"
        or getattr(stage.model_info, "architecture", "") == "DDColor"
    ]
    if len(colour_indices) > 1:
        raise ValueError("Only one video colour stage is supported.")
    colour_index = colour_indices[0] if colour_indices else None
    if colour_index is not None and _video_has_colour(config):
        # Colour invents colours for black-and-white footage; on a colour video it
        # would replace the real colours with guesses.
        note = "This video already has colour, so Colour was skipped."
        config = replace(
            config,
            preprocess_stages=tuple(
                stage for i, stage in enumerate(config.preprocess_stages) if i != colour_index
            ),
        )
        colour_index = None
        if warning_callback is not None:
            warning_callback(note)
        add_note = getattr(engine, "add_note", None)
        if callable(add_note):
            add_note(note)

    stage_weights = [max(0.05, float(stage.time_factor)) for stage in config.preprocess_stages]
    if not skip_primary_sr:
        stage_weights.append(max(0.05, float(config.primary_time_factor)))
    total_stage_weight = sum(stage_weights) or 1.0
    stage_offsets: list[float] = []
    running_weight = 0.0
    for weight in stage_weights:
        stage_offsets.append(running_weight / total_stage_weight)
        running_weight += weight
    stage_spans = [weight / total_stage_weight for weight in stage_weights]
    primary_stage_index = len(config.preprocess_stages)
    use_face_aware = (
        not skip_primary_sr
        and config.face_model_path is not None
        and config.face_model_info is not None
    )

    ctx = VideoChainContext(
        config=config,
        engine=engine,
        cancel_event=cancel_event,
        preserve_hdr=preserve_hdr,
        transfer=transfer,
        precision_str=precision_str,
        fps=fps,
        interpolation=interpolation,
        before_upscale=before_upscale,
        total_frames=total_frames,
        remaining_time=remaining_time,
        model_info=model_info,
        native_scale=native_scale,
        output_scale=output_scale,
        skip_primary_sr=skip_primary_sr,
        use_face_aware=use_face_aware,
        output_width=output_width,
        output_height=output_height,
        job_started_at=job_started_at,
        colour_index=colour_index,
        has_preprocess_stages=bool(config.preprocess_stages),
        stage_offsets=stage_offsets,
        stage_spans=stage_spans,
        primary_stage_index=primary_stage_index,
        frame_started_cb=frame_started_cb,
        frame_completed_cb=frame_completed_cb,
        enhanced_frame_cb=enhanced_frame_cb,
        progress_cb=progress_cb,
        tile_callback=tile_callback,
        tile_progress_cb=tile_progress_cb,
        warning_callback=warning_callback,
        source_frame_cb=source_frame_cb,
        stage_cb=stage_cb,
        synthesizer=synthesizer,
        keep_partial=keep_partial,
        now_fn=lambda: time.perf_counter(),
        detect_faces_fn=lambda rgb, cancel_event=None: detect_faces(rgb, cancel_event=cancel_event),
        process_face_aware_fn=process_frame_face_aware,
        deflicker_fn=_deflicker_frames,
        prefetch_fn=_prefetch,
        release_after_last_fn=_release_after_last_frame,
        should_keep_partial_fn=_should_keep_partial,
        colour_keyframe_interval=COLOUR_KEYFRAME_INTERVAL,
        inference_started_at=inference_started_at,
        field_order=probe.field_order,
    )
    # Build and validate the stage chain before loading any models.
    stages = build_video_stages(ctx)

    # Load the fix models once and reuse them for every frame.
    for stage in config.preprocess_stages:
        if stage.model_path and stage.model_info is not None:
            engine.load_model(
                stage.model_path,
                config.device_str,
                precision_str,
                _SafeModelInfo(stage.model_info, config.safe_memory),
            )
    if not skip_primary_sr:
        # Load the model once and reuse it for every frame.
        engine.load_model(
            config.model_path,
            config.device_str,
            precision_str,
            # safe_memory is enforced at the engine level via model_info.
            _SafeModelInfo(model_info, config.safe_memory),
        )
        # If face-aware mode is enabled, also pre-load the face model.
        if use_face_aware:
            engine.load_model(
                config.face_model_path,
                config.device_str,
                precision_str,
                _SafeModelInfo(config.face_model_info, config.safe_memory),
            )

    output_frames = run_video_chain(
        decode_timed_frames(
            config.video_path,
            start_frame=config.start_frame,
            end_frame=config.end_frame,
            cancel_event=cancel_event,
            hdr_mode=config.hdr_mode,
        ),
        ctx,
        stages=stages,
    )
    if interpolation is not None:
        fps = interpolation.output_fps(fps)

    # The encoder consumes the generator directly so frames never accumulate
    # in memory beyond what PyAV's internal buffers hold. Compatible audio and
    # subtitle packets are trimmed and shifted onto the enhanced timeline.
    # A deliberate frame-rate override changes video speed, so passthrough
    # streams are omitted rather than silently producing drift.
    if not rate_unchanged and warning_callback:
        warning_callback(
            "FPS override changes playback speed; source audio and subtitles are omitted."
        )
    try:
        output_path = encode_video(
            output_frames,
            config.output_video_path,
            fps=fps,
            container_format=config.container,
            video_codec=config.video_codec,
            automatic_codec=config.automatic_codec,
            note_callback=getattr(engine, "add_note", None),
            crf=config.crf,
            width=output_width,
            height=output_height,
            audio_source=effective_audio_source if rate_unchanged else None,
            subtitle_source=raw_audio_source if rate_unchanged and reencode_audio else None,
            reencode_audio=reencode_audio,
            preserve_timing=rate_unchanged,
            cancel_event=cancel_event,
            warning_callback=warning_callback,
            sdr_bt709=bool(probe.hdr_format and config.hdr_mode == "tone_map"),
            hdr_format=probe.hdr_format if preserve_hdr else "",
            temporary_directory=config.temporary_directory,
            external_ffmpeg=config.external_ffmpeg,
            stage_callback=stage_cb,
            # A whole clip can stream into the system's hardware encoder; trims keep
            # the lossless master, which carries their trimmed audio.
            stream_to_system=config.start_frame is None and config.end_frame is None,
            keep_partial=config.keep_partial if keep_partial is None else keep_partial,
        )
    finally:
        # Ends the decoder now, also on a failure: a streamed source's helper stops
        # with it instead of waiting for the garbage collector.
        output_frames.close()

    completed_at = time.perf_counter()
    return VideoJobResult(
        output_path=output_path,
        frames_processed=ctx.frames_processed,
        partial=output_path != config.output_video_path,
        kept_seconds=probe_video(output_path).duration_seconds
        if output_path != config.output_video_path
        else 0.0,
        kept_frames=count_video_frames(output_path)
        if output_path != config.output_video_path
        else 0,
        elapsed_seconds=completed_at - job_started_at,
        inference_seconds=(completed_at - (ctx.inference_started_at or completed_at)),
    )


def _release_after_last_frame(frames, engine, device_str: str):
    """Free the fix and upscaling models once the last frame has passed through them.

    Interpolating after the upscale, de-flicker and the encoder still work on the last
    frames; on a shared-memory Mac the freed graphics memory is theirs, not the models'.
    """
    yield from frames
    release = getattr(engine, "release_model", None)
    if callable(release):
        release()
    kind = device_str.split(":", 1)[0]
    if kind == "mps" and torch.backends.mps.is_available():
        torch.mps.empty_cache()
    elif kind == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()


def _interpolation_target(
    config: VideoJobConfig, source_fps: float, preserve_hdr: bool, synthesizer
) -> InterpolationTarget | None:
    """Validate the interpolation request before any model loads."""
    target = InterpolationTarget.parse(config.interpolation)
    if target is None:
        return None
    if config.interpolation_order not in ORDERS:
        raise ValueError("Frame interpolation runs before or after the upscale.")
    if config.fps_override is not None:
        raise ValueError(
            "Frame interpolation keeps the source timing; it cannot be combined with an "
            "FPS override."
        )
    if preserve_hdr:
        raise ValueError(
            "Frame interpolation is not yet available with HDR preservation. "
            "Convert to SDR or turn interpolation off."
        )
    if synthesizer is None:
        raise ValueError("Frame interpolation needs its interpolation model; download it first.")
    if source_fps <= 0:
        raise ValueError("Frame interpolation needs the source frame rate.")
    return target


def process_frame_face_aware(
    engine: InferenceEngine,
    img_tensor: torch.Tensor,
    face_mask: FaceMask,
    general_model_path: str,
    face_model_path: str,
    general_model_info,
    face_model_info,
    device_str: str,
    precision_str: str,
    tile_size: int,
    halo: int,
    cancel_event: threading.Event,
    safe_memory: bool = True,
    face_fidelity: float = 0.7,
    face_threshold_high: float = 0.85,
    face_threshold_low: float = 0.15,
    progress_callback: Callable[[int, int, int], None] | None = None,
    tile_callback: Callable[..., None] | None = None,
    float_output: bool = False,
    bit_depth: int = 8,
) -> np.ndarray:
    """Process a single frame with per-tile model selection.

    Tiles whose core overlaps a face box >= face_threshold_high are run
    through the face model. Tiles with overlap <= face_threshold_low use
    the general model. Boundary tiles run both and are alpha-blended.

    Both models must share the same scale and output channels. Since the
    current face checkpoint has no native fidelity input, ``face_fidelity``
    linearly blends the restored result with a bicubic reconstruction of the
    original inside the smoothed face mask. Non-face pixels keep the general
    model result.

    progress_callback and tile_callback are optional — when provided,
    they receive the same per-tile notifications as the standard
    process_frame path so the GUI's progress bar and progressive
    preview work during face-aware restoration.
    """
    import gc

    if int(face_model_info.scale) != int(general_model_info.scale):
        raise ValueError("Face and primary checkpoints must have the same native scale.")
    if int(face_model_info.out_channels) != int(general_model_info.out_channels):
        raise ValueError("Face and primary checkpoints must have compatible output channels.")

    _, h, w = img_tensor.shape
    scale = general_model_info.scale
    out_channels = general_model_info.out_channels
    out_shape = (out_channels, h * scale, w * scale)
    out_dtype = np.float32 if float_output else (np.uint16 if bit_depth == 16 else np.uint8)
    out_array = np.zeros(out_shape, dtype=out_dtype)
    float_options = (
        {"float_output": True} if float_output else ({"bit_depth": 16} if bit_depth == 16 else {})
    )

    # Load both models once. Subsequent switching via switch_model is
    # instant — no torch reload, no VRAM spike.
    general_safe = _SafeModelInfo(general_model_info, safe_memory)
    face_safe = _SafeModelInfo(face_model_info, safe_memory)
    engine.load_model(general_model_path, device_str, precision_str, general_safe)
    engine.load_model(face_model_path, device_str, precision_str, face_safe)

    device = engine._loaded_device
    precision = engine._loaded_precision

    tiles = list(generate_tiles(w, h, tile_size, halo, scale))
    total_tiles = len(tiles)
    engine.prepare_tiles(tile_size, halo, general_model_path)
    engine.prepare_tiles(tile_size, halo, face_model_path)

    def identity_core_for(tile) -> np.ndarray:
        """Return the source core bicubic-resampled to the model output size."""
        import torch.nn.functional as functional

        source = img_tensor[
            :,
            tile.core_y : tile.core_y + tile.core_h,
            tile.core_x : tile.core_x + tile.core_w,
        ].unsqueeze(0)
        resized = functional.interpolate(
            source.float(),
            size=(tile.out_h, tile.out_w),
            mode="bicubic",
            align_corners=False,
            antialias=True,
        )
        if float_output:
            return resized.squeeze(0).clamp(0, 1).cpu().numpy()
        if bit_depth == 16:
            return (
                resized.squeeze(0)
                .clamp(0, 1)
                .mul(65535.0)
                .round()
                .to(torch.int32)
                .cpu()
                .numpy()
                .astype(np.uint16, copy=False)
            )
        return resized.squeeze(0).clamp(0, 1).mul(255).round().byte().cpu().numpy()

    for i, tile in enumerate(tiles):
        if cancel_event.is_set():
            raise InterruptedError("Cancelled")

        # Compute face overlap for this tile's core.
        alpha = face_mask_for_tile_core(
            face_mask.mask, tile.core_x, tile.core_y, tile.core_w, tile.core_h
        )
        tile_class = classify_tile(alpha, face_threshold_high, face_threshold_low)

        if tile_callback is not None:
            tile_callback("started", tile, None, i, total_tiles, w * scale, h * scale, tile_size)

        if tile_class == "face":
            engine.switch_model(general_model_path)
            general_core = engine.run_tile(
                tile,
                img_tensor,
                general_model_info,
                device,
                precision,
                halo,
                engine.active_model,
                **float_options,
            )
            identity_core = identity_core_for(tile)
            if face_fidelity <= 0.0:
                core_output = blend_tile_outputs(
                    identity_core,
                    general_core,
                    alpha,
                    fidelity=0.0,
                    identity_output=identity_core,
                )
            else:
                engine.switch_model(face_model_path)
                face_core = engine.run_tile(
                    tile,
                    img_tensor,
                    face_model_info,
                    device,
                    precision,
                    halo,
                    engine.active_model,
                    **float_options,
                )
                core_output = blend_tile_outputs(
                    face_core,
                    general_core,
                    alpha,
                    fidelity=face_fidelity,
                    identity_output=identity_core,
                )
        elif tile_class == "general":
            engine.switch_model(general_model_path)
            core_output = engine.run_tile(
                tile,
                img_tensor,
                general_model_info,
                device,
                precision,
                halo,
                engine.active_model,
                **float_options,
            )
        else:  # boundary
            engine.switch_model(general_model_path)
            general_core = engine.run_tile(
                tile,
                img_tensor,
                general_model_info,
                device,
                precision,
                halo,
                engine.active_model,
                **float_options,
            )
            engine.switch_model(face_model_path)
            face_core = engine.run_tile(
                tile,
                img_tensor,
                face_model_info,
                device,
                precision,
                halo,
                engine.active_model,
                **float_options,
            )
            core_output = blend_tile_outputs(
                face_core,
                general_core,
                alpha,
                fidelity=face_fidelity,
                identity_output=identity_core_for(tile),
            )

        out_array[:, tile.out_y : tile.out_y + tile.out_h, tile.out_x : tile.out_x + tile.out_w] = (
            core_output
        )

        if tile_callback is not None:
            tile_callback(
                "completed", tile, core_output, i + 1, total_tiles, w * scale, h * scale, tile_size
            )

        if progress_callback is not None:
            progress_callback(i + 1, total_tiles, tile_size)

        if safe_memory:
            import torch as _torch

            if device is not None:
                if device.type == "mps":
                    _torch.mps.empty_cache()
                elif device.type == "cuda":
                    _torch.cuda.empty_cache()
                elif device.type == "xpu":
                    _torch.xpu.empty_cache()
            gc.collect()

    return out_array


@dataclass
class _SafeModelInfo:
    """Wraps a NormalizedModelInfo to expose safe_memory to InferenceEngine.

    InferenceEngine.load_model reads model_info.safe_memory to decide the
    GPU memory ceiling. NormalizedModelInfo does not carry that field, so
    we wrap it transparently for the video path only.
    """

    inner: object
    safe_memory: bool

    def __getattr__(self, name):
        return getattr(self.inner, name)
