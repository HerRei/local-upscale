"""PyAV-based video decode and encode for the worker.

All ffmpeg work happens here so the worker never shells out to a binary.
The encoder uses an open temp file and an atomic rename at the end so a
crashed or cancelled job never leaves a half-written output at the final
path.
"""

from __future__ import annotations

import glob
import heapq
import logging
import os
import tempfile
import threading
import warnings
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

from .hdr import tone_map_to_sdr
from .media_codecs import (
    EXTERNAL_FFMPEG_HINT,
    EXTERNAL_OUTPUT_CODECS,
    SYSTEM_OUTPUT_CODECS,
    UNDECODABLE_VIDEO_MESSAGE,
    UndecodableVideoError,
    encode_spec,
    muxer_format,
    normalize_container,
    require_bundled_encoder,
    stream_copy_supported,
    validate_output,
)
from .output_publish import DiscardOnStop, OutputExistsError, owned_temporary, publish_output
from .pixel_budget import MAX_INPUT_PIXELS, require_pixel_budget
from .video_audio import add_converted_audio_stream, needs_audio_transcode, opus_packets
from .video_normalization import display_frames, square_pixel_width

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    fps: float
    frame_count: int
    codec: str
    duration_seconds: float
    hdr_format: str = ""
    audio_warning: str = ""
    has_audio: bool = True


@dataclass(frozen=True)
class TimedVideoFrame:
    """Pixels and their source presentation interval, independent of inference."""

    index: int
    rgb: np.ndarray
    timestamp: Fraction
    duration: Fraction
    time_base: Fraction

    def with_pixels(self, rgb: np.ndarray) -> TimedVideoFrame:
        return replace(self, rgb=rgb)


def _display_transform(frame: av.VideoFrame) -> tuple[int, bool]:
    """Return a right-angle rotation and horizontal reflection from FFmpeg's matrix.

    Pixels are normalized on decode so inference, thumbnails and exports agree.
    Track translations only position the rotated image on a presentation canvas;
    our standalone output is rebased to that image's bounds. Keep rejecting
    perspective, scaling and arbitrary rotations instead of changing geometry.
    """
    # Look up by name for compatibility across supported PyAV versions.
    side_data = frame.side_data.get("DISPLAYMATRIX")
    if side_data is None:
        return 0, False
    matrix = np.frombuffer(side_data, dtype=np.int32).reshape(3, 3)
    # FFmpeg/ISO BMFF stores [a b u; c d v; x y w]. The bottom-row
    # x/y values are 16.16 translations, commonly used by iPhone MOVs to
    # keep a rotated portrait in positive coordinates. They are not
    # perspective terms (u/v), and do not change its normalized pixels.
    if matrix[0, 2] or matrix[1, 2] or matrix[2, 2] != 1 << 30:
        raise ValueError("Video display transform includes unsupported perspective or scale.")
    return display_turns(matrix[:2, :2].astype(float) / 65536)


def display_turns(basis: np.ndarray) -> tuple[int, bool]:
    """Right-angle turns for ``np.rot90`` and a mirror flag from a 2×2 display basis.

    The basis is ``[[a, b], [c, d]]`` of an ISO BMFF track matrix; AVFoundation's
    preferred transform uses the same layout.
    """
    rotations = (
        np.array([[1, 0], [0, 1]]),
        np.array([[0, -1], [1, 0]]),
        np.array([[-1, 0], [0, -1]]),
        np.array([[0, 1], [-1, 0]]),
    )
    for turns, rotation in enumerate(rotations):
        for flipped in (False, True):
            candidate = rotation @ np.diag([-1, 1]) if flipped else rotation
            if np.allclose(basis, candidate, atol=1e-4):
                return turns, flipped
    raise ValueError("Video display transform must use a right-angle rotation or mirror.")


def _check_sdr(stream, frame) -> None:
    transfer = int(getattr(frame, "color_trc", stream.codec_context.color_trc))
    if transfer in (16, 18):
        raise ValueError(
            "Choose HDR preservation with HAT (Labs), or explicit HDR-to-SDR conversion "
            "before enhancing PQ/HLG video."
        )


def selected_frame_count(count: int, start: int | None, end: int | None) -> int:
    first = max(0, int(start or 0))
    if end is not None and int(end) < first:
        raise ValueError("Video end frame must be at or after its start frame.")
    last = min(count - 1, end) if count > 0 and end is not None else end
    return max(0, (last + 1 if last is not None else count) - first)


class VideoStageError(RuntimeError):
    """An actionable encode/remux failure with the actual subsystem named."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"Video {stage} stage failed: {message}")


@contextmanager
def _video_decoder(path: str, *, frame_threads: bool = False, thread_count: int = 0):
    with av.open(
        path, options={"protocol_whitelist": "file", "max_pixels": str(MAX_INPUT_PIXELS)}
    ) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError(f"No video stream found in {path}")
        if stream.codec_context is None:
            raise UndecodableVideoError(UNDECODABLE_VIDEO_MESSAGE)
        require_pixel_budget(int(stream.width or 0), int(stream.height or 0))
        stream.codec_context.options = {
            **stream.codec_context.options,
            "max_pixels": str(MAX_INPUT_PIXELS),
        }
        stream.codec_context.thread_count = max(0, min(8, thread_count))
        if frame_threads:
            try:
                stream.thread_type = "FRAME"
            except (KeyError, ValueError):
                pass
        try:
            yield container, stream
        finally:
            # Early trim, cancellation and rejected frames leave buffered work
            # in FFmpeg's decoder threads. Flush while PyAV releases the GIL,
            # before its codec-context destructor joins those threads.
            stream.codec_context.flush_buffers()


def _video_probe(container, stream, first: av.VideoFrame | None, converter: str = "") -> VideoProbe:
    fps = float(stream.average_rate) if stream.average_rate else 0.0
    duration = float(container.duration) / 1_000_000.0 if container.duration else 0.0
    count = int(stream.frames or 0) or (int(round(duration * fps)) if fps else 0)
    turns, _ = _display_transform(first) if first is not None else (0, False)
    width, height = int(stream.width or 0), int(stream.height or 0)
    width = square_pixel_width(stream, width)
    transfer = int(getattr(first, "color_trc", stream.codec_context.color_trc))
    if turns % 2:
        width, height = height, width
    return VideoProbe(
        width,
        height,
        fps,
        count,
        str(stream.codec_context.name or "unknown"),
        duration,
        {16: "PQ", 18: "HLG"}.get(transfer, ""),
        _audio_compatibility_warning(container, converter=converter),
        any(item.type == "audio" for item in container.streams),
    )


def _audio_stream_usable(stream, container_format: str) -> bool:
    """Decodable audio can always be kept; other tracks only if copied unchanged."""
    return stream.codec_context is not None or stream_copy_supported(stream, container_format)


def _audio_compatibility_warning(
    container, container_format: str = "mp4", *, converter: str = ""
) -> str:
    """Describe audio changes; ``converter`` names the program that converts unusable tracks."""
    audio = [stream for stream in container.streams if stream.type == "audio"]
    unusable = [stream for stream in audio if not _audio_stream_usable(stream, container_format)]
    if unusable and converter:
        return f"Audio in a format LocalSR does not include is converted by {converter}."
    if unusable:
        if len(unusable) == len(audio):
            return (
                "This video's audio uses a format LocalSR does not include, so the enhanced "
                "video will have no sound. " + EXTERNAL_FFMPEG_HINT
            )
        return (
            "An additional audio track uses a format LocalSR does not include and will be "
            "omitted. " + EXTERNAL_FFMPEG_HINT
        )
    if any(needs_audio_transcode(stream, container_format) for stream in audio):
        return (
            f"For {container_format.upper()} output, audio that the container cannot carry "
            "unchanged is converted to Opus. The source audio stays unchanged."
        )
    return ""


def _source_decodable(path: str) -> bool:
    try:
        container = av.open(
            path, options={"protocol_whitelist": "file", "max_pixels": str(MAX_INPUT_PIXELS)}
        )
    except av.FFmpegError:
        if Path(path).is_file():
            return False
        raise
    with container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        return stream is None or stream.codec_context is not None


def probe_video(
    path: str, external_ffmpeg=None, temporary_directory: str | None = None
) -> VideoProbe:
    """Probe metadata and one frame to resolve its display orientation."""
    if external_ffmpeg is not None and not _source_decodable(path):
        return probe_video_preview(path, 64, None, external_ffmpeg, temporary_directory)[0]
    with _video_decoder(path) as (container, stream):
        return _video_probe(container, stream, next(display_frames(container, stream), None))


def _frame_rgb(stream, frame: av.VideoFrame, hdr_mode: str) -> np.ndarray:
    turns, flipped = _display_transform(frame)
    width = square_pixel_width(stream, frame.width)
    require_pixel_budget(frame.width, frame.height)
    require_pixel_budget(width, frame.height)
    if width != frame.width:
        frame = frame.reformat(width=width, height=frame.height, interpolation="BICUBIC")
    transfer = int(getattr(frame, "color_trc", stream.codec_context.color_trc))
    if transfer in (16, 18) and hdr_mode in {"tone_map", "preserve"}:
        # Swscale expands the source's YUV matrix/range without dropping the
        # 10-bit signal to 8-bit before the transfer/gamut conversion.
        primaries = int(getattr(frame, "color_primaries", stream.codec_context.color_primaries))
        if primaries != 9 or frame.colorspace != 9:
            raise ValueError(
                "HDR import requires BT.2020 HLG/PQ with a BT.2020 non-constant luminance matrix."
            )
        rgb = frame.to_ndarray(
            format="gbrpf32le",
            src_colorspace="BT2020",
            dst_colorspace="BT2020",
            src_color_range=frame.color_range,
            dst_color_range=2,
        )
        if hdr_mode == "tone_map":
            rgb = tone_map_to_sdr(rgb, transfer, primaries)
    else:
        _check_sdr(stream, frame)
        rgb = frame.to_ndarray(format="rgb24")
    rgb = np.rot90(rgb, turns)
    if flipped:
        rgb = np.fliplr(rgb)
    return np.ascontiguousarray(rgb)


def _converter_label(bridge, path: str, *, audio_only: bool = False) -> str:
    """Name the system codecs or the user's FFmpeg, whichever will convert ``path``."""
    if bridge is None:
        return ""
    from .media_bridge import as_bridge

    wrapped = as_bridge(bridge)
    return wrapped.converter_label(path, audio_only=audio_only) if wrapped is not None else ""


def _source_timing(
    path: str, converter: str = "your FFmpeg"
) -> tuple[float, int, float, str, bool]:
    """Timing from the container and stream headers, which need no decoder."""
    with av.open(
        path, options={"protocol_whitelist": "file", "max_pixels": str(MAX_INPUT_PIXELS)}
    ) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError(f"No video stream found in {path}")
        try:
            rate = stream.average_rate or stream.guessed_rate
        except AttributeError:
            rate = None
        fps = float(rate) if rate else 0.0
        duration = float(container.duration) / 1_000_000.0 if container.duration else 0.0
        return (
            fps,
            int(stream.frames or 0),
            duration,
            _audio_compatibility_warning(container, converter=converter),
            any(item.type == "audio" for item in container.streams),
        )


def probe_video_preview(
    path: str,
    max_dimension: int = 1600,
    progress: Callable[[str], None] | None = None,
    external_ffmpeg=None,
    temporary_directory: str | None = None,
) -> tuple[VideoProbe, bytes]:
    """Read one frame once; propagate failures instead of returning a blank preview.

    A video whose format LocalSR does not include is probed through a one-frame
    lossless intermediate made by the user's own FFmpeg, when one is selected.
    """
    report = progress or (lambda _stage: None)
    report("opening")
    if not _source_decodable(path):
        from .media_bridge import (
            external_has_audio,
            external_timing,
            first_frame_source,
            undecodable_message,
        )

        if external_ffmpeg is None:
            raise UndecodableVideoError(undecodable_message(None))
        try:
            fps, count, duration, audio_warning, has_audio = _source_timing(
                path, _converter_label(external_ffmpeg, path) or "your FFmpeg"
            )
        except av.FFmpegError:
            (fps, duration), count = external_timing(external_ffmpeg, path), 0
            audio_warning = ""
            has_audio = external_has_audio(external_ffmpeg, path)
        report("decoding_video")
        with first_frame_source(
            path, ffmpeg=external_ffmpeg, temporary_directory=temporary_directory
        ) as frame_path:
            probe, preview = probe_video_preview(frame_path, max_dimension, None)
        fps = fps or probe.fps
        count = count or (int(round(duration * fps)) if fps else 0)
        return (
            replace(
                probe,
                fps=fps,
                frame_count=count,
                duration_seconds=duration,
                codec="external",
                audio_warning=audio_warning,
                has_audio=has_audio,
            ),
            preview,
        )
    with _video_decoder(path) as (container, stream):
        report("decoding_video")
        first = next(display_frames(container, stream), None)
        if first is None:
            raise ValueError("This video contains no decodable picture frames.")
        probe = _video_probe(
            container, stream, first, _converter_label(external_ffmpeg, path, audio_only=True)
        )
        if probe.hdr_format:
            report("converting_hdr")
        rgb = _frame_rgb(stream, first, "tone_map")
        report("preparing_preview")
        return probe, thumbnail_jpeg(
            rgb, max_dimension=max(64, min(2048, max_dimension)), quality=82
        )


def decode_timed_frames(
    path: str,
    start_frame: int | None = None,
    end_frame: int | None = None,
    cancel_event: threading.Event | None = None,
    *,
    hdr_mode: str = "reject",
    decoder_threads: int = 0,
):
    """Decode oriented SDR frames with exact source times and one-frame lookahead."""
    if hdr_mode not in {"reject", "tone_map", "preserve"}:
        raise ValueError("HDR mode must be reject, tone_map or preserve.")
    start = max(0, int(start_frame or 0))
    selected_frame_count(0, start, end_frame)
    with _video_decoder(path, frame_threads=True, thread_count=decoder_threads) as (
        container,
        stream,
    ):
        pending: TimedVideoFrame | None = None
        origin: Fraction | None = None
        base_time_base: Fraction | None = None
        retimed_count = 0
        try:
            for index, frame in enumerate(display_frames(container, stream, cancel_event)):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("video job cancelled")
                if index < start:
                    continue
                if frame.pts is None or frame.time_base is None:
                    raise ValueError(
                        "Video frame has no presentation timestamp; remux the source first."
                    )
                timestamp = frame.pts * frame.time_base
                if origin is None:
                    origin = timestamp
                    base_time_base = frame.time_base
                if end_frame is not None and index > end_frame:
                    if pending is not None:
                        interval = timestamp - pending.timestamp
                        yield replace(pending, duration=interval) if interval > 0 else pending
                    return
                if pending is not None:
                    assert origin is not None and base_time_base is not None
                    if timestamp <= pending.timestamp or round(
                        (timestamp - origin) / base_time_base
                    ) <= round((pending.timestamp - origin) / base_time_base):
                        # Bump by one source time-base tick rather than a full frame
                        # interval so subsequent valid frames keep their source PTS and
                        # audio stays in sync.
                        timestamp = pending.timestamp + base_time_base
                        retimed_count += 1
                    interval = timestamp - pending.timestamp
                    yield replace(pending, duration=interval)
                    pending = None
                rgb = _frame_rgb(stream, frame, hdr_mode)
                duration = getattr(frame, "duration", 0) or 0
                interval = (
                    duration * frame.time_base if duration > 0 else 1 / (stream.average_rate or 25)
                )
                pending = TimedVideoFrame(
                    index, rgb, timestamp, Fraction(interval), frame.time_base
                )
            if pending is not None:
                yield pending
        finally:
            if retimed_count:
                logger.info(
                    "Retimed %d frame(s) with non-increasing presentation timestamps.",
                    retimed_count,
                )


def validate_hdr_encoder(
    video_codec: str = "av1", external_ffmpeg=None, container: str | None = None
) -> None:
    """Fail before inference if this export cannot carry 10-bit HDR."""
    try:
        validate_output(
            video_codec, container or ("mkv" if video_codec == "ffv1" else "mp4"), hdr=True
        )
        if video_codec in EXTERNAL_OUTPUT_CODECS:
            if external_ffmpeg is None:
                raise ValueError("HEVC HDR export requires a selected external FFmpeg.")
            external_ffmpeg.encoder_for(video_codec, hdr=True)
            require_bundled_encoder(encode_spec("ffv1", hdr=True))
        else:
            require_bundled_encoder(encode_spec(video_codec, hdr=True))
    except (av.FFmpegError, ValueError, RuntimeError) as error:
        raise VideoStageError("HDR encode", str(error)) from error


def count_video_frames(path: str) -> int:
    """The frames of a written video: its stream's own count, else its packets, read
    without decoding. A kept part of two frames has no frame rate a probe could use."""
    with av.open(str(path)) as container:
        if not container.streams.video:
            return 0
        stream = container.streams.video[0]
        if stream.frames:
            return int(stream.frames)
        return sum(1 for packet in container.demux(stream) if packet.size)


def partial_destination(destination: Path, seconds: float) -> Path:
    """`<name>_partial_<m>m<ss>s.<ext>` beside the requested output, numbered when that
    name is taken: a kept part is never lost to an earlier one of the same length."""
    whole = int(seconds)
    stem = f"{destination.stem}_partial_{whole // 60}m{whole % 60:02d}s"
    candidate = destination.with_name(f"{stem}{destination.suffix}")
    number = 2
    while os.path.lexists(candidate):
        candidate = destination.with_name(f"{stem}-{number}{destination.suffix}")
        number += 1
    return candidate


def _should_keep_partial(keep_partial: bool | Callable[[], bool]) -> bool:
    return bool(keep_partial()) if callable(keep_partial) else bool(keep_partial)


def _system_stream_export(
    frames,
    destination: Path,
    *,
    system,
    video_codec: str,
    container_format: str,
    crf: int,
    fps: float,
    audio_source: str | None,
    cancel_event: threading.Event | None,
    temporary_directory: str | None,
    keep_partial: bool | Callable[[], bool] = False,
    stage_callback=None,
    subtitle_source: str | None = None,
    reencode_audio: bool = False,
    warning_callback=None,
) -> str:
    """Pipe restored frames to the system encoder while the next ones are restored."""
    final = _owned_output_temporary(destination, "external", temporary_directory)
    can_keep_partial = bool(keep_partial)
    stopped = False
    last_end = 0.0
    origin = None

    def available_frames():
        nonlocal stopped, last_end, origin
        try:
            for frame in frames:
                if (
                    cancel_event is not None
                    and cancel_event.is_set()
                    and not _should_keep_partial(keep_partial)
                ):
                    raise InterruptedError("video job cancelled")
                if origin is None:
                    origin = frame.timestamp
                last_end = float(frame.timestamp - origin + frame.duration)
                yield frame
        except InterruptedError:
            if not _should_keep_partial(keep_partial) or origin is None:
                raise
            stopped = True
            if stage_callback is not None:
                stage_callback("partial_finalizing")

    muxed = _owned_output_temporary(destination, "mux", temporary_directory)
    try:
        with tempfile.TemporaryDirectory(
            prefix="localsr-media-", dir=destination.parent
        ) as scratch:
            wav = (
                system.source_audio(Path(audio_source), Path(scratch) / "audio.wav", cancel_event)
                if audio_source and not can_keep_partial
                else None
            )
            system.encode_frames(
                available_frames() if can_keep_partial else frames,
                final,
                video_codec=video_codec,
                container=container_format,
                crf=crf,
                fps=fps,
                audio=wav,
                cancel_event=None if can_keep_partial else cancel_event,
            )
        if stopped:
            destination = partial_destination(destination, float(last_end))
        final_cancel_event = (
            None
            if stopped or cancel_event is None
            else DiscardOnStop(cancel_event, lambda: _should_keep_partial(keep_partial))
        )
        source = Path(audio_source) if audio_source else None
        subtitles = Path(subtitle_source) if subtitle_source else None
        if can_keep_partial and (
            (source is not None and source.is_file())
            or (subtitles is not None and subtitles.is_file())
        ):
            copied = _remux_source_streams(
                final,
                source if source is not None and source.is_file() else subtitles,
                muxed,
                container_format=container_format,
                subtitle_source=subtitles if subtitles != source else None,
                reencode_audio=reencode_audio,
                warning_callback=warning_callback,
                source_start_seconds=float(origin or 0),
                force_audio_transcode=stopped,
                cut_packet_durations=stopped,
                cancel_event=final_cancel_event,
            )
            if copied:
                final, muxed = muxed, final
        publish_output(final, destination, final_cancel_event)
        return str(destination)
    finally:
        try:
            final.unlink()
        except FileNotFoundError:
            pass
        muxed.unlink(missing_ok=True)
        _remove_system_sidecars(final)
        _remove_system_sidecars(muxed)


def _remove_system_sidecars(final: Path) -> None:
    """Remove the copy macOS keeps beside a file the system encoder wrote.

    It is named `<name>.sb-...` and, for ProRes, is a full extra copy. Only the ones
    of this job's own file go.
    """
    for sidecar in final.parent.glob(glob.escape(final.name) + ".sb-*"):
        try:
            sidecar.unlink()
        except OSError:
            pass


def _external_export(
    frames,
    destination: Path,
    *,
    video_codec: str,
    container_format: str,
    crf: int,
    external_ffmpeg,
    stage_callback,
    hdr_format: str,
    width: int,
    height: int,
    fps: float,
    cancel_event: threading.Event | None,
    temporary_directory: str | None,
    **master_options,
) -> str:
    """Encode an FFV1 master, then let the user's FFmpeg write H.264/HEVC.

    With the system's own encoders, a whole untrimmed SDR clip skips the master
    and streams straight into the hardware encoder instead.
    """
    system = getattr(external_ffmpeg, "system", None)
    if (
        system is not None
        and not hdr_format
        and master_options.get("stream_to_system", False)
        and width % 2 == 0
        and height % 2 == 0
    ):
        from .system_codecs import SystemCodecsError

        try:
            system.encoder_for(video_codec, container=container_format)
        except SystemCodecsError:
            pass
        else:
            return _system_stream_export(
                frames,
                destination,
                system=system,
                video_codec=video_codec,
                container_format=container_format,
                crf=crf,
                fps=fps,
                audio_source=master_options.get("audio_source"),
                cancel_event=cancel_event,
                temporary_directory=temporary_directory,
                keep_partial=master_options.get("keep_partial", False),
                stage_callback=stage_callback,
                subtitle_source=master_options.get("subtitle_source"),
                reencode_audio=master_options.get("reencode_audio", False),
                warning_callback=master_options.get("warning_callback"),
            )
    master_options.pop("stream_to_system", None)
    master_options.pop("keep_partial", None)
    if external_ffmpeg is None:
        raise VideoStageError(
            "encode",
            f"{video_codec.upper()} export uses the FFmpeg installed on this computer, "
            "which is not selected. " + EXTERNAL_FFMPEG_HINT,
        )
    master = _owned_output_temporary(destination, "master", temporary_directory)
    final = _owned_output_temporary(destination, "external", temporary_directory)
    try:
        encode_video(
            frames,
            str(master),
            fps=fps,
            container_format="mkv",
            video_codec="ffv1",
            crf=crf,
            width=width,
            height=height,
            cancel_event=cancel_event,
            hdr_format=hdr_format,
            temporary_directory=temporary_directory,
            replace_existing=True,
            **master_options,
        )
        if stage_callback is not None:
            stage_callback("external_encode")
        from .external_ffmpeg import ExternalFFmpegError
        from .media_bridge import transcode_output

        try:
            transcode_output(
                external_ffmpeg,
                master,
                final,
                video_codec=video_codec,
                container=container_format,
                crf=crf,
                hdr_format=hdr_format,
                width=width,
                height=height,
                fps=fps,
                cancel_event=cancel_event,
            )
        except ExternalFFmpegError as error:
            raise VideoStageError("external encode", str(error)) from error
        publish_output(final, destination, cancel_event)
        return str(destination)
    finally:
        for temporary in (master, final):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        _remove_system_sidecars(final)


def encode_video(
    frames,
    destination_path: str,
    *,
    video_codec: str = "av1",
    automatic_codec: bool = False,
    note_callback: Callable[[str], None] | None = None,
    **options,
) -> str:
    """Export video, retaining Automatic's HEVC → AV1 recovery intent."""
    from .media_bridge import automatic_video_codec
    from .video_codec_recovery import automatic_export, system_export_failure

    if video_codec == "auto":
        automatic_codec = True
        video_codec = automatic_video_codec(
            options.get("external_ffmpeg"),
            options.get("container_format", "mp4"),
            "preserve" if options.get("hdr_format") else "reject",
        )
    if (
        automatic_codec
        and video_codec == "hevc"
        and options.get("container_format", "mp4") == "mp4"
    ):
        return automatic_export(
            frames,
            destination_path,
            export=lambda replay, codec: _encode_video(
                replay, destination_path, video_codec=codec, **options
            ),
            cancel_event=options.get("cancel_event"),
            temporary_directory=options.get("temporary_directory"),
            note_callback=note_callback,
            warning_callback=options.get("warning_callback"),
        )
    try:
        return _encode_video(frames, destination_path, video_codec=video_codec, **options)
    except Exception as error:
        if video_codec in {"h264", "hevc"} and system_export_failure(error):
            from .system_codecs import SystemCodecsError

            message = f"{error}. Choose Automatic or AV1 under Advanced > Video format."
            if isinstance(error, SystemCodecsError):
                raise SystemCodecsError(message) from error
            raise VideoStageError("encode", message) from error
        raise


def _close_encoder(container, stream, mux):
    """Drain the encoder on its owning thread before closing its container."""
    try:
        if stream is not None:
            for packet in stream.encode():
                mux(packet)
    finally:
        container.close()


def _encode_video(
    frames,
    destination_path: str,
    *,
    fps: float,
    container_format: str = "mp4",
    video_codec: str = "av1",
    crf: int = 18,
    width: int,
    height: int,
    audio_source: str | None = None,
    subtitle_source: str | None = None,
    reencode_audio: bool = False,
    source_start_seconds: float = 0.0,
    preserve_timing: bool = True,
    cancel_event: threading.Event | None = None,
    warning_callback=None,
    sdr_bt709: bool = False,
    hdr_format: str = "",
    temporary_directory: str | None = None,
    encoder_threads: int = 0,
    external_ffmpeg=None,
    fast: bool = False,
    stage_callback: Callable[[str], None] | None = None,
    stream_to_system: bool = False,
    replace_existing: bool = False,
    keep_partial: bool | Callable[[], bool] = False,
    declare_frame_rate: bool = True,
) -> str:
    """Encode an iterable of (rgb_uint8_HxWx3) frames into a video file.

    The enhanced picture is encoded to an app-owned temporary container, then
    remuxed with source audio/subtitles. Compatible audio streams are copied;
    audio the container cannot carry is converted to Opus. H.264/HEVC exports
    are delegated to the user's own FFmpeg from a lossless FFV1 master; with
    ``stream_to_system`` (a whole, untrimmed clip of timed frames) the system's
    hardware encoder receives the frames directly. The final destination appears
    only through one atomic publication that fails if the name is already taken;
    ``replace_existing`` is for an app-owned temporary destination that is
    expected to exist.
    """
    container_format = normalize_container(container_format)
    if hdr_format:
        if hdr_format not in {"HLG", "PQ"} or sdr_bt709:
            raise ValueError("HDR output must be HLG or PQ, without SDR conversion.")
        validate_hdr_encoder(video_codec, external_ffmpeg, container_format)
    validate_output(video_codec, container_format, hdr=bool(hdr_format))
    if video_codec in EXTERNAL_OUTPUT_CODECS + SYSTEM_OUTPUT_CODECS:
        return _external_export(
            frames,
            Path(destination_path),
            video_codec=video_codec,
            container_format=container_format,
            crf=crf,
            external_ffmpeg=external_ffmpeg,
            stage_callback=stage_callback,
            hdr_format=hdr_format,
            width=width,
            height=height,
            fps=fps,
            cancel_event=cancel_event,
            temporary_directory=temporary_directory,
            audio_source=audio_source,
            subtitle_source=subtitle_source,
            source_start_seconds=source_start_seconds,
            preserve_timing=preserve_timing,
            warning_callback=warning_callback,
            sdr_bt709=sdr_bt709,
            encoder_threads=encoder_threads,
            stream_to_system=stream_to_system and preserve_timing,
            keep_partial=keep_partial,
        )
    spec = encode_spec(
        video_codec, crf=crf, hdr=bool(hdr_format), threads=encoder_threads, fast=fast
    )
    require_bundled_encoder(spec)
    pixel_format = spec.pixel_format
    destination = Path(destination_path)
    encoded_temporary = _owned_output_temporary(destination, "video", temporary_directory)
    mux_temporary = _owned_output_temporary(destination, "mux", temporary_directory)
    output_container = None
    stream = None
    mux = None
    frame = None
    try:
        fps_fraction = (
            Fraction(str(float(fps))).limit_denominator(100_000) if fps else Fraction(25, 1)
        )
        iterator = iter(frames)
        first = next(iterator, None)
        if first is None:
            raise ValueError("the inference pipeline produced no frames")
        timed = isinstance(first, TimedVideoFrame) and preserve_timing
        origin = first.timestamp if timed else Fraction(0)
        time_base = first.time_base if timed else Fraction(1, 1) / fps_fraction
        if timed:
            source_start_seconds = float(origin)
        output_container = av.open(
            str(encoded_temporary), mode="w", format=muxer_format(container_format)
        )
        stream = output_container.add_stream(spec.encoder, rate=fps_fraction)
        if not declare_frame_rate:
            # Without a nominal rate Matroska writes no DefaultDuration. GStreamer (Linux
            # WebKit) uses that duration for every frame of a variable-rate file, so a
            # seek lands on an earlier frame whose assumed duration overlaps the target.
            stream.codec_context.framerate = Fraction(0, 1)
        stream.codec_context.thread_count = max(0, min(8, encoder_threads))
        stream.time_base = time_base
        stream.codec_context.time_base = time_base
        stream.width = int(width)
        stream.height = int(height)
        stream.pix_fmt = pixel_format
        if sdr_bt709:
            stream.codec_context.color_primaries = 1
            stream.codec_context.color_trc = 1
            stream.codec_context.colorspace = 1
            stream.codec_context.color_range = 1
        stream.options = dict(spec.options)
        if hdr_format:
            transfer = 18 if hdr_format == "HLG" else 16
            stream.codec_context.color_primaries = 9
            stream.codec_context.color_trc = transfer
            stream.codec_context.colorspace = 9
            stream.codec_context.color_range = 1
        frame_count = 0
        durations = {}
        previous_pts = None
        last_end = Fraction(0)

        def mux(packet):
            duration = durations.pop(packet.pts * packet.time_base, None)
            if duration is not None:
                packet.duration = max(1, round(duration / packet.time_base))
            output_container.mux(packet)

        from itertools import chain

        stopped = False

        def available_frames():
            nonlocal stopped
            try:
                for item in chain((first,), iterator):
                    if (
                        cancel_event is not None
                        and cancel_event.is_set()
                        and not _should_keep_partial(keep_partial)
                    ):
                        stopped = True
                        return
                    yield item
            except InterruptedError:
                stopped = True

        for item in available_frames():
            rgb = item.rgb if isinstance(item, TimedVideoFrame) else item
            if hdr_format:
                if rgb.dtype != np.float32 or not np.isfinite(rgb).all():
                    raise ValueError(
                        "HDR export requires finite float32 HDR RGB; 8-bit frames are refused."
                    )
            elif rgb.dtype != np.uint8:
                rgb = np.clip(rgb, 0, 255).astype(np.uint8)
            if rgb.shape != (int(height), int(width), 3):
                raise ValueError(
                    f"frame {frame_count} has shape {rgb.shape}; expected ({height}, {width}, 3)"
                )
            if hdr_format:
                frame = av.VideoFrame.from_ndarray(np.clip(rgb, 0, 1), format="gbrpf32le")
                frame = frame.reformat(
                    format=pixel_format,
                    src_colorspace="BT2020",
                    dst_colorspace="BT2020",
                    src_color_range=2,
                    dst_color_range=1,
                )
                frame.color_primaries, frame.color_trc, frame.colorspace, frame.color_range = (
                    9,
                    transfer,
                    9,
                    1,
                )
            else:
                frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            if sdr_bt709:
                frame = frame.reformat(
                    format=pixel_format, src_colorspace="ITU709", dst_colorspace="ITU709"
                )
                frame.color_primaries = frame.color_trc = frame.colorspace = frame.color_range = 1
            frame.pts = round((item.timestamp - origin) / time_base) if timed else frame_count
            frame.time_base = time_base
            if previous_pts is not None and frame.pts <= previous_pts:
                raise ValueError("Output presentation timestamps must increase.")
            previous_pts = frame.pts
            durations[frame.pts * time_base] = item.duration if timed else time_base
            last_end = frame.pts * time_base + durations[frame.pts * time_base]
            for packet in stream.encode(frame):
                mux(packet)
            frame_count += 1
        if stopped and (not _should_keep_partial(keep_partial) or not frame_count):
            raise InterruptedError("video job cancelled")
        if stopped and stage_callback is not None:
            stage_callback("partial_finalizing")
        frame = None  # drop the last frame before the encoder closes
        try:
            _close_encoder(output_container, stream, mux)
        finally:
            output_container = None

        if stopped:
            destination = partial_destination(destination, float(last_end))
        final_cancel_event = (
            None
            if stopped or cancel_event is None
            else DiscardOnStop(cancel_event, lambda: _should_keep_partial(keep_partial))
        )

        def publish(temporary: Path) -> None:
            if replace_existing:
                os.replace(temporary, destination)
            else:
                publish_output(temporary, destination, final_cancel_event)

        source = Path(audio_source) if audio_source else None
        sub_source = Path(subtitle_source) if subtitle_source else None
        if (source is not None and source.is_file()) or (
            sub_source is not None and sub_source.is_file()
        ):
            copied = _remux_source_streams(
                encoded_temporary,
                source if source is not None and source.is_file() else sub_source,
                mux_temporary,
                container_format=container_format,
                subtitle_source=(
                    sub_source if sub_source is not None and sub_source != source else None
                ),
                reencode_audio=reencode_audio,
                force_audio_transcode=stopped,
                cut_packet_durations=stopped,
                source_start_seconds=float(source_start_seconds),
                cancel_event=final_cancel_event,
                warning_callback=warning_callback,
            )
            publish(mux_temporary if copied else encoded_temporary)
        else:
            publish(encoded_temporary)
        return str(destination)
    except (InterruptedError, VideoStageError, OutputExistsError):
        raise
    except BaseException as error:
        raise VideoStageError("encode", str(error)) from error
    finally:
        frame = None  # drop any active frame before flushing and closing the encoder
        if output_container is not None:
            try:
                _close_encoder(output_container, stream if mux is not None else None, mux)
            except Exception:
                pass
        for temporary in (encoded_temporary, mux_temporary):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _owned_output_temporary(destination: Path, phase: str, directory: str | None = None) -> Path:
    """Reserve one unique LocalSR-owned sibling for an atomic final replace."""
    return owned_temporary(directory or destination.parent, phase)


def _packet_time(packet: av.Packet) -> float:
    timestamp = packet.dts if packet.dts is not None else packet.pts
    return (
        float(timestamp * packet.time_base) if timestamp is not None and packet.time_base else 0.0
    )


def _packet_end_time(packet: av.Packet) -> float:
    start = _packet_time(packet)
    if packet.duration is None or packet.time_base is None:
        return start
    return start + max(0.0, float(packet.duration * packet.time_base))


def _shift_packet_to_trimmed_timeline(packet: av.Packet, start_seconds: float) -> None:
    if start_seconds == 0.0 or packet.time_base is None:
        return
    offset = int(round(start_seconds / float(packet.time_base)))
    if packet.pts is not None:
        packet.pts = max(0, packet.pts - offset)
    if packet.dts is not None:
        packet.dts = max(0, packet.dts - offset)


def _next_trimmed_packet(
    iterator, start_seconds: float, end_seconds: float, cancel_event=None, cut_durations=False
):
    """Return the next packet overlapping the selected source time range."""
    for packet in iterator:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("video job cancelled")
        if packet.dts is None and packet.pts is None:
            continue
        packet_start = _packet_time(packet)
        packet_end = _packet_end_time(packet)
        if packet_end <= start_seconds:
            continue
        if packet_start >= end_seconds:
            continue
        if cut_durations and packet.time_base and packet.duration:
            clipped = min(packet_end, end_seconds) - max(packet_start, start_seconds)
            packet.duration = max(0, round(clipped / float(packet.time_base)))
        _shift_packet_to_trimmed_timeline(packet, start_seconds)
        return packet
    return None


def _remux_source_streams(
    video_path: Path,
    source_path: Path,
    output_path: Path,
    *,
    container_format: str,
    subtitle_source: Path | None = None,
    reencode_audio: bool = False,
    force_audio_transcode: bool = False,
    cut_packet_durations: bool = False,
    source_start_seconds: float = 0.0,
    cancel_event: threading.Event | None = None,
    warning_callback=None,
) -> bool:
    """Interleave encoded video and compatible source audio/subtitles."""
    video_container = None
    source_container = None
    subtitle_container = None
    output_container = None
    try:
        video_container = av.open(str(video_path), options={"protocol_whitelist": "file"})
        source_container = av.open(str(source_path), options={"protocol_whitelist": "file"})
        if subtitle_source is not None and subtitle_source.is_file():
            subtitle_container = av.open(
                str(subtitle_source), options={"protocol_whitelist": "file"}
            )
        video_in = next(
            (stream for stream in video_container.streams if stream.type == "video"), None
        )
        if video_in is None:
            raise VideoStageError("remux", "the enhanced temporary file has no video stream")

        audio_inputs = [stream for stream in source_container.streams if stream.type == "audio"]
        subtitle_inputs = [
            stream
            for stream in (subtitle_container or source_container).streams
            if stream.type == "subtitle"
        ]
        if not reencode_audio:
            audio_warning = _audio_compatibility_warning(source_container, container_format)
            if audio_warning:
                if warning_callback:
                    warning_callback(audio_warning)
                else:
                    warnings.warn(audio_warning, stacklevel=2)
                audio_inputs = [
                    stream
                    for stream in audio_inputs
                    if _audio_stream_usable(stream, container_format)
                ]
        if not audio_inputs and not subtitle_inputs:
            return False

        output_container = av.open(
            str(output_path), mode="w", format=muxer_format(container_format)
        )
        # Copy the encoded stream's parameters; its decoder name (libdav1d) is not an encoder.
        video_out = output_container.add_stream_from_template(video_in, opaque=True)
        auxiliary_outputs: dict[int, object] = {}
        subtitle_outputs: dict[int, object] = {}
        converted_audio: dict[int, object] = {}
        for stream in audio_inputs:
            if (
                force_audio_transcode
                or needs_audio_transcode(stream, container_format)
                or (reencode_audio and normalize_container(container_format) != "mkv")
            ):
                converted_audio[stream.index] = add_converted_audio_stream(
                    output_container, stream, container_format
                )
                continue
            try:
                auxiliary_outputs[stream.index] = output_container.add_stream_from_template(stream)
            except (av.FFmpegError, ValueError, OSError) as error:
                raise VideoStageError(
                    "audio remux",
                    f"{getattr(stream.codec_context, 'name', 'audio')} is incompatible with {container_format}: {error}",
                ) from error

        for stream in subtitle_inputs:
            try:
                target_map = (
                    subtitle_outputs if subtitle_container is not None else auxiliary_outputs
                )
                target_map[stream.index] = output_container.add_stream_from_template(stream)
            except (av.FFmpegError, ValueError, OSError):
                # The requested container does not support this subtitle
                # codec. The enhanced video remains valid and the stream is
                # not falsely advertised as copied.
                message = f"Subtitle stream {stream.index} was omitted: incompatible with {container_format}."
                if warning_callback:
                    warning_callback(message)
                else:
                    warnings.warn(message, stacklevel=2)

        selected_aux = [
            stream
            for stream in (
                audio_inputs + ([] if subtitle_container is not None else subtitle_inputs)
            )
            if stream.index in auxiliary_outputs
        ]
        selected_subs = (
            [stream for stream in subtitle_inputs if stream.index in subtitle_outputs]
            if subtitle_container is not None
            else []
        )
        if not selected_aux and not selected_subs and not converted_audio:
            output_container.close()
            output_container = None
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass
            return False

        duration = (
            float(video_in.duration * video_in.time_base)
            if video_in.duration is not None and video_in.time_base is not None
            # Matroska stores no per-stream duration; use the container's.
            else float(video_container.duration) / 1_000_000
            if video_container.duration
            else None
        )
        source_end_seconds = source_start_seconds + (duration or float("inf"))

        def video_packets():
            for packet in video_container.demux(video_in):
                if packet.dts is not None or packet.pts is not None:
                    packet.stream = video_out
                    yield packet

        def copied_packets(container, streams, outputs):
            if not streams:
                return
            iterator = container.demux(*streams)
            while (
                packet := _next_trimmed_packet(
                    iterator,
                    source_start_seconds,
                    source_end_seconds,
                    cancel_event,
                    cut_packet_durations,
                )
            ) is not None:
                packet.stream = outputs[packet.stream.index]
                yield packet

        iterators = [
            video_packets(),
            copied_packets(source_container, selected_aux, auxiliary_outputs),
            copied_packets(subtitle_container, selected_subs, subtitle_outputs),
        ]
        iterators.extend(
            opus_packets(
                source_path, index, output, source_start_seconds, source_end_seconds, cancel_event
            )
            for index, output in converted_audio.items()
        )
        # One pending packet per stream, ordered by decode timestamp.
        pending = []
        try:
            for index, iterator in enumerate(iterators):
                if (packet := next(iterator, None)) is not None:
                    heapq.heappush(pending, (_packet_time(packet), index, packet))
            while pending:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("video job cancelled")
                _, index, packet = heapq.heappop(pending)
                output_container.mux(packet)
                if (packet := next(iterators[index], None)) is not None:
                    heapq.heappush(pending, (_packet_time(packet), index, packet))
        finally:
            for iterator in iterators:
                iterator.close()
        output_container.close()
        output_container = None
        return True
    except (VideoStageError, InterruptedError):
        raise
    except (av.FFmpegError, ValueError, OSError) as error:
        raise VideoStageError("audio/subtitle remux", str(error)) from error
    finally:
        for container in (output_container, subtitle_container, source_container, video_container):
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass


def uint8_chw_to_rgb_hwc(array_chw: np.ndarray) -> np.ndarray:
    """Convert a (C, H, W) uint8 array to an HxWx3 uint8 RGB array.

    Single-channel inputs are repeated to 3 channels so the encoder always
    gets RGB.
    """
    if array_chw.ndim != 3:
        raise ValueError("Expected a (C, H, W) array.")
    if array_chw.shape[0] == 1:
        array_chw = np.repeat(array_chw, 3, axis=0)
    if array_chw.shape[0] < 3:
        raise ValueError("Expected at least one or three channels.")
    return np.ascontiguousarray(array_chw[:3].transpose(1, 2, 0))


def thumbnail_jpeg(rgb: np.ndarray, max_dimension: int = 192, quality: int = 72) -> bytes:
    """Produce a small JPEG bytes blob for a frame thumbnail."""
    image = Image.fromarray(rgb)
    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
