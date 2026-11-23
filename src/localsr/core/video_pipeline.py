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
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace

import numpy as np
import torch
from PIL import Image

from .audio_denoise import cleaned_audio_source
from .deflicker import AnalysisFrame, deflicker_stream, prepare_analysis_frame
from .disk_space import require_video_room
from .eta import RemainingTime
from .face_compositing import blend_tile_outputs, classify_tile
from .face_detection import FaceMask, detect_faces, face_mask_for_tile_core
from .frame_interpolation import ORDERS, InterpolationTarget, interpolate_frames
from .hdr import anchor_hdr_detail, tone_map_to_sdr
from .inference import InferenceEngine, UnstableRestorationError
from .media_codecs import EXTERNAL_OUTPUT_CODECS, validate_output
from .tiling import generate_tiles
from .video_io import (
    TimedVideoFrame,
    _should_keep_partial,
    count_video_frames,
    decode_timed_frames,
    encode_video,
    probe_video,
    selected_frame_count,
    uint8_chw_to_rgb_hwc,
    validate_hdr_encoder,
)


def rgb_to_tensor(rgb: np.ndarray) -> torch.Tensor:
    """Convert an HxWx3 uint8 RGB array to a (3, H, W) float32 tensor in [0, 1]."""
    arr = rgb.astype(np.float32) / 255.0
    return torch.from_numpy(arr).permute(2, 0, 1)


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
    lossless intermediate by the user's selected FFmpeg, if any.
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
        if any(stage.fix == "colour" for stage in config.preprocess_stages):
            raise ValueError("Colour is not yet supported with HDR preservation.")
    for stage in config.preprocess_stages:
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
    interpolation = _interpolation_target(config, probe.fps, preserve_hdr, synthesizer)
    if interpolation is not None and not interpolation.applies_to(probe.fps):
        if warning_callback:
            warning_callback(
                f"This video already plays at {probe.fps:g} fps, so frame interpolation to "
                f"{interpolation.output_fps(probe.fps):g} fps was skipped."
            )
        interpolation = None
    before_upscale = interpolation is not None and config.interpolation_order == "before_upscale"

    total_frames = selected_frame_count(probe.frame_count, config.start_frame, config.end_frame)
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
    for _, item in zip(
        range(sample_frames),
        decode_timed_frames(
            config.video_path,
            start_frame=start,
            end_frame=start + sample_frames,
            hdr_mode=config.hdr_mode,
        ),
        strict=False,
    ):
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
        if stage.fix == "colour" or getattr(stage.model_info, "architecture", "") == "DDColor"
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

    # Load the fix models once and reuse them for every frame.
    for stage in config.preprocess_stages:
        engine.load_model(
            stage.model_path,
            config.device_str,
            precision_str,
            _SafeModelInfo(stage.model_info, config.safe_memory),
        )
    use_face_aware = False
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
        use_face_aware = config.face_model_path is not None and config.face_model_info is not None
        if use_face_aware:
            engine.load_model(
                config.face_model_path,
                config.device_str,
                precision_str,
                _SafeModelInfo(config.face_model_info, config.safe_memory),
            )

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

    frames_processed = 0
    cached_face_mask: FaceMask | None = None
    frames_since_detection = 0
    warned_unstable: set[int] = set()

    def frame_generator() -> Iterator[TimedVideoFrame | tuple[TimedVideoFrame, AnalysisFrame]]:
        nonlocal inference_started_at
        nonlocal frames_processed
        nonlocal cached_face_mask
        nonlocal frames_since_detection

        decoded = decode_timed_frames(
            config.video_path,
            start_frame=config.start_frame,
            end_frame=config.end_frame,
            cancel_event=cancel_event,
            hdr_mode=config.hdr_mode,
        )
        if colour_index is None:
            decoded = _prefetch(decoded, 4)
        if before_upscale and interpolation is not None:
            # On this thread, not the decoder's: one thread drives the GPU.
            decoded = interpolate_frames(decoded, interpolation, synthesizer, cancel_event)
        source_analyses: deque[AnalysisFrame] = deque()
        if colour_index is not None:
            from .video_colour import colour_frames

            metadata = deque()
            source_previews = deque()
            colour_input = decoded
            stage = config.preprocess_stages[colour_index]

            prepared = [0]
            first_output = [False]
            first_batch = min(
                COLOUR_KEYFRAME_INTERVAL + 1, total_frames or COLOUR_KEYFRAME_INTERVAL + 1
            )

            def input_pixels():
                nonlocal inference_started_at
                for item in colour_input:
                    if cancel_event.is_set():
                        raise InterruptedError("video job cancelled")
                    if inference_started_at is None:
                        inference_started_at = time.perf_counter()
                    # The UI uses a 640 px preview. Keep that small proxy with the
                    # timing metadata until the corresponding output is ready.
                    preview = None
                    if source_frame_cb is not None:
                        stride = max(1, int(np.ceil(max(item.rgb.shape[:2]) / 640)))
                        preview = item.rgb[::stride, ::stride].copy()
                    analysis = prepare_analysis_frame(item.rgb) if config.deflicker else None
                    metadata.append(
                        (
                            item.index,
                            item.timestamp,
                            item.duration,
                            item.time_base,
                            preview,
                            analysis,
                        )
                    )
                    pixels = item.rgb
                    for preceding_index, preceding in enumerate(
                        config.preprocess_stages[:colour_index]
                    ):
                        engine.switch_model(preceding.model_path)
                        try:
                            output = engine.process_frame(
                                img_tensor=rgb_to_tensor(pixels),
                                model_info=preceding.model_info,
                                tile_size=config.tile_size,
                                halo=max(config.halo, preceding.halo),
                                cancel_event=cancel_event,
                                progress_callback=lambda *_: None,
                                safe_memory=config.safe_memory,
                                whole_frame=True,
                            )
                        except UnstableRestorationError as error:
                            if preceding_index not in warned_unstable:
                                warned_unstable.add(preceding_index)
                                note = (
                                    f"The {preceding.fix or 'restoration'} fix "
                                    f"({preceding.model_id or preceding.model_path}) was "
                                    f"skipped for one or more frames because the model became "
                                    f"unstable: {error}"
                                )
                                if warning_callback is not None:
                                    warning_callback(note)
                                add_note = getattr(engine, "add_note", None)
                                if callable(add_note):
                                    add_note(note)
                        else:
                            pixels = uint8_chw_to_rgb_hwc(output)
                    # Colour needs a batch of frames before the first one can be finished;
                    # until then the frame progress cannot move, so the batch is counted.
                    prepared[0] += 1
                    if not first_output[0] and stage_cb is not None:
                        stage_cb("colour_preparing", prepared[0], first_batch)
                    yield pixels

            def colourise(pixels):
                engine.switch_model(stage.model_path)
                result = engine.process_frame(
                    img_tensor=rgb_to_tensor(pixels),
                    model_info=stage.model_info,
                    tile_size=config.tile_size,
                    halo=max(config.halo, stage.halo),
                    cancel_event=cancel_event,
                    progress_callback=lambda *_: None,
                    safe_memory=config.safe_memory,
                    whole_frame=True,
                )
                return uint8_chw_to_rgb_hwc(result)

            def colour_stream():
                for pixels in colour_frames(
                    input_pixels(), colourise, interval=COLOUR_KEYFRAME_INTERVAL
                ):
                    first_output[0] = True
                    index, timestamp, duration, time_base, preview, analysis = metadata.popleft()
                    if preview is not None:
                        source_previews.append(preview)
                    if analysis is not None:
                        source_analyses.append(analysis)
                    yield TimedVideoFrame(index, pixels, timestamp, duration, time_base)

            decoded = colour_stream()
        for output_frame_index, source_frame in enumerate(decoded):
            rgb = source_frame.rgb
            if cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            if inference_started_at is None:
                inference_started_at = time.perf_counter()
            if frame_started_cb is not None:
                frame_started_cb(output_frame_index, total_frames)
            if source_frame_cb is not None:
                source = source_previews.popleft() if colour_index is not None else rgb
                source_frame_cb(output_frame_index, total_frames, source)
            source_analysis: AnalysisFrame | None = None
            if config.deflicker:
                source_analysis = (
                    source_analyses.popleft()
                    if colour_index is not None
                    else prepare_analysis_frame(rgb)
                )

            tensor = torch.from_numpy(rgb).permute(2, 0, 1) if preserve_hdr else rgb_to_tensor(rgb)
            float_options = {"float_output": True} if preserve_hdr else {}

            def record_tile_eta(frame_index: int, stage_index: int, done: int, count: int) -> None:
                if remaining_time is None or not count:
                    return
                progress_value = (
                    frame_index
                    + stage_offsets[stage_index]
                    + stage_spans[stage_index] * (done / count)
                )
                if config.preprocess_stages and frame_index == 0 and done == 1 and stage_index > 0:
                    remaining_time.skip_warmup(time.perf_counter(), progress_value)
                else:
                    remaining_time.update(time.perf_counter(), progress_value)

            def observed_tile(
                phase,
                tile,
                pixels,
                done,
                count,
                image_w,
                image_h,
                size,
                *,
                frame_index=output_frame_index,
                source_rgb=rgb,
            ):
                if phase == "completed":
                    record_tile_eta(frame_index, primary_stage_index, done, count)
                if tile_progress_cb is not None:
                    elapsed = time.perf_counter() - job_started_at
                    remaining = remaining_time.remaining() if remaining_time else None
                    tile_progress_cb(
                        frame_index, total_frames, done, count, elapsed, remaining, size
                    )
                if tile_callback is not None:
                    if pixels is not None and preserve_hdr:
                        core = source_rgb[
                            tile.core_y : tile.core_y + tile.core_h,
                            tile.core_x : tile.core_x + tile.core_w,
                        ]
                        pixels = anchor_hdr_detail(
                            core, pixels.transpose(1, 2, 0).copy(), transfer
                        ).transpose(2, 0, 1)
                    tile_callback(phase, tile, pixels, done, count, image_w, image_h, size)

            def tile_progress(_completed: int, _total: int, _active_tile: int) -> None:
                """Tile timing is reported by ``tile_callback``; the engine requires a callback."""

            try:
                last_fix_chw: np.ndarray | None = (
                    rgb.transpose(2, 0, 1) if colour_index is not None else None
                )
                for stage_index, stage in enumerate(config.preprocess_stages):
                    if colour_index is not None and stage_index <= colour_index:
                        continue
                    if cancel_event.is_set():
                        raise InterruptedError("video job cancelled")
                    engine.switch_model(stage.model_path)
                    is_final_stage = (
                        skip_primary_sr and stage_index == len(config.preprocess_stages) - 1
                    )

                    def fix_tile_callback(
                        phase,
                        tile,
                        pixels,
                        done,
                        count,
                        image_w,
                        image_h,
                        size,
                        *,
                        frame_index=output_frame_index,
                        idx=stage_index,
                        final_stage=is_final_stage,
                        source_rgb=rgb,
                    ):
                        if phase == "completed":
                            record_tile_eta(frame_index, idx, done, count)
                        if final_stage and tile_progress_cb is not None:
                            elapsed = time.perf_counter() - job_started_at
                            remaining = remaining_time.remaining() if remaining_time else None
                            tile_progress_cb(
                                frame_index, total_frames, done, count, elapsed, remaining, size
                            )
                        if final_stage and tile_callback is not None:
                            if pixels is not None and preserve_hdr:
                                core = source_rgb[
                                    tile.core_y : tile.core_y + tile.core_h,
                                    tile.core_x : tile.core_x + tile.core_w,
                                ]
                                pixels = anchor_hdr_detail(
                                    core, pixels.transpose(1, 2, 0).copy(), transfer
                                ).transpose(2, 0, 1)
                            tile_callback(phase, tile, pixels, done, count, image_w, image_h, size)

                    try:
                        fix_chw = engine.process_frame(
                            img_tensor=tensor,
                            model_info=stage.model_info,
                            tile_size=config.tile_size,
                            halo=max(config.halo, stage.halo),
                            cancel_event=cancel_event,
                            progress_callback=tile_progress,
                            safe_memory=config.safe_memory,
                            tile_callback=fix_tile_callback,
                            whole_frame=True,
                            **float_options,
                        )
                    except UnstableRestorationError as error:
                        if stage_index not in warned_unstable:
                            warned_unstable.add(stage_index)
                            label = stage.fix or "restoration"
                            note = (
                                f"The {label} fix ({stage.model_id or stage.model_path}) was "
                                f"skipped for one or more frames because the model became "
                                f"unstable: {error}"
                            )
                            if warning_callback is not None:
                                warning_callback(note)
                            add_note = getattr(engine, "add_note", None)
                            if callable(add_note):
                                add_note(note)
                    else:
                        last_fix_chw = fix_chw
                        tensor = (
                            torch.from_numpy(np.array(fix_chw, copy=True)).float()
                            if preserve_hdr
                            else torch.from_numpy(np.array(fix_chw, copy=True)).float().div_(255.0)
                        )

                if skip_primary_sr:
                    out_chw = last_fix_chw
                elif use_face_aware:
                    # Run face detection every N frames and cache the mask.
                    if (
                        cached_face_mask is None
                        or frames_since_detection >= config.face_detection_interval
                    ):
                        cached_face_mask = detect_faces(
                            tone_map_to_sdr(rgb, transfer, 9) if preserve_hdr else rgb,
                            cancel_event=cancel_event,
                        )
                        frames_since_detection = 0
                    else:
                        frames_since_detection += 1

                    if cached_face_mask.has_faces:
                        out_chw = process_frame_face_aware(
                            engine=engine,
                            img_tensor=tensor,
                            face_mask=cached_face_mask,
                            general_model_path=config.model_path,
                            face_model_path=config.face_model_path,
                            general_model_info=model_info,
                            face_model_info=config.face_model_info,
                            device_str=config.device_str,
                            precision_str=precision_str,
                            tile_size=config.tile_size,
                            halo=config.halo,
                            cancel_event=cancel_event,
                            safe_memory=config.safe_memory,
                            face_fidelity=config.face_fidelity,
                            tile_callback=observed_tile,
                            **float_options,
                        )
                    else:
                        # No faces detected — use the general model. The face model was
                        # loaded last, and a frame with faces leaves either one active.
                        engine.switch_model(config.model_path)
                        out_chw = engine.process_frame(
                            img_tensor=tensor,
                            model_info=model_info,
                            tile_size=config.tile_size,
                            halo=config.halo,
                            cancel_event=cancel_event,
                            progress_callback=tile_progress,
                            safe_memory=config.safe_memory,
                            tile_callback=observed_tile,
                            whole_frame=True,
                            **float_options,
                        )
                else:
                    if config.preprocess_stages:
                        engine.switch_model(config.model_path)
                    out_chw = engine.process_frame(
                        img_tensor=tensor,
                        model_info=model_info,
                        tile_size=config.tile_size,
                        halo=config.halo,
                        cancel_event=cancel_event,
                        progress_callback=tile_progress,
                        safe_memory=config.safe_memory,
                        tile_callback=observed_tile,
                        whole_frame=True,
                        **float_options,
                    )
            except InterruptedError:
                raise

            if skip_primary_sr:
                if preserve_hdr:
                    out_rgb = (
                        tensor.permute(1, 2, 0).clamp(0, 1).numpy().copy()
                        if out_chw is not None
                        else rgb
                    )
                    if out_chw is not None:
                        out_rgb = anchor_hdr_detail(rgb, out_rgb, transfer)
                else:
                    out_rgb = uint8_chw_to_rgb_hwc(out_chw) if out_chw is not None else rgb
            elif preserve_hdr:
                out_rgb = out_chw.transpose(1, 2, 0)
                if output_scale != native_scale:
                    out_rgb = (
                        torch.nn.functional.interpolate(
                            torch.from_numpy(out_chw).unsqueeze(0),
                            size=(output_height, output_width),
                            mode="bicubic",
                            align_corners=False,
                            antialias=True,
                        )[0]
                        .permute(1, 2, 0)
                        .clamp(0, 1)
                        .numpy()
                        .copy()
                    )
                out_rgb = anchor_hdr_detail(rgb, out_rgb, transfer)
            else:
                out_rgb = uint8_chw_to_rgb_hwc(out_chw)
                if output_scale != native_scale:
                    out_rgb = np.array(
                        Image.fromarray(out_rgb).resize(
                            (output_width, output_height), Image.Resampling.LANCZOS
                        ),
                        dtype=np.uint8,
                    )
            frames_processed += 1

            if enhanced_frame_cb is not None:
                enhanced_frame_cb(output_frame_index, total_frames, out_rgb)
            if frame_completed_cb is not None:
                frame_completed_cb(output_frame_index, total_frames, "")

            if progress_cb is not None:
                now = time.perf_counter()
                if remaining_time is not None:
                    remaining_time.update(now, frames_processed)
                progress_cb(
                    min(frames_processed, max(0, total_frames - 1))
                    if interpolation is not None and not before_upscale and total_frames > 0
                    else frames_processed,
                    total_frames,
                    now - job_started_at,
                    remaining_time.remaining() if remaining_time else None,
                )

            restored_frame = source_frame.with_pixels(out_rgb)
            if config.deflicker:
                assert source_analysis is not None
                yield (restored_frame, source_analysis)
            else:
                yield restored_frame

    # Apply temporal de-flicker if enabled. The source-driven filter wraps the
    # restored-frame generator so the encoder receives stabilised frames.
    restored_frames = _release_after_last_frame(frame_generator(), engine, config.device_str)
    if config.deflicker:
        # Smoothing a large frame takes a moment of its own; the status names it.
        # config.deflicker_window is accepted for request compatibility and unused.
        def report_deflicker(index: int) -> None:
            if stage_cb is not None:
                stage_cb("deflickering", index + 1, total_frames)

        output_frames = _deflicker_frames(
            restored_frames,
            config.deflicker_window,
            cancel_event,
            report_deflicker,
            keep=lambda: _should_keep_partial(
                config.keep_partial if keep_partial is None else keep_partial
            ),
            device_str=config.device_str,
        )
    else:
        output_frames = restored_frames
    if interpolation is not None:
        if not before_upscale:
            output_frames = interpolate_frames(
                output_frames, interpolation, synthesizer, cancel_event
            )
            if progress_cb is not None:

                def report_interpolation_done(frames):
                    yield from frames
                    progress_cb(
                        frames_processed,
                        total_frames,
                        time.perf_counter() - job_started_at,
                        remaining_time.remaining() if remaining_time else None,
                    )

                output_frames = report_interpolation_done(output_frames)
        fps = interpolation.output_fps(fps)
    # Restore the next frames while the encoder writes the previous ones.
    if colour_index is None:
        output_frames = _prefetch(output_frames, 2)

    # The encoder consumes the generator directly so frames never accumulate
    # in memory beyond what PyAV's internal buffers hold. Compatible audio and
    # subtitle packets are trimmed and shifted onto the enhanced timeline.
    # A deliberate frame-rate override changes video speed, so passthrough
    # streams are omitted rather than silently producing drift.
    if not rate_unchanged and warning_callback:
        warning_callback(
            "FPS override changes playback speed; source audio and subtitles are omitted."
        )
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

    completed_at = time.perf_counter()
    return VideoJobResult(
        output_path=output_path,
        frames_processed=frames_processed,
        partial=output_path != config.output_video_path,
        kept_seconds=probe_video(output_path).duration_seconds
        if output_path != config.output_video_path
        else 0.0,
        kept_frames=count_video_frames(output_path)
        if output_path != config.output_video_path
        else 0,
        elapsed_seconds=completed_at - job_started_at,
        inference_seconds=(completed_at - (inference_started_at or completed_at)),
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
    out_array = np.zeros(out_shape, dtype=np.float32 if float_output else np.uint8)
    float_options = {"float_output": True} if float_output else {}

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
