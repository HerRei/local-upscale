"""PyAV-based video decode and encode for the worker.

All ffmpeg work happens here so the worker never shells out to a binary.
The encoder uses an open temp file and an atomic rename at the end so a
crashed or cancelled job never leaves a half-written output at the final
path.
"""

from __future__ import annotations

import heapq
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
    UNDECODABLE_VIDEO_MESSAGE,
    UndecodableVideoError,
    encode_spec,
    muxer_format,
    normalize_container,
    require_bundled_encoder,
    stream_copy_supported,
    validate_output,
)
from .video_audio import add_opus_stream, needs_audio_transcode, opus_packets
from .video_normalization import display_frames, square_pixel_width


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
    for side_data in frame.side_data:
        if side_data.type.name != "DISPLAYMATRIX":
            continue
        matrix = np.frombuffer(side_data, dtype=np.int32).reshape(3, 3)
        basis = matrix[:2, :2].astype(float) / 65536
        rotations = (
            np.array([[1, 0], [0, 1]]),
            np.array([[0, -1], [1, 0]]),
            np.array([[-1, 0], [0, -1]]),
            np.array([[0, 1], [-1, 0]]),
        )
        # FFmpeg/ISO BMFF stores [a b u; c d v; x y w]. The bottom-row
        # x/y values are 16.16 translations, commonly used by iPhone MOVs to
        # keep a rotated portrait in positive coordinates. They are not
        # perspective terms (u/v), and do not change its normalized pixels.
        if matrix[0, 2] or matrix[1, 2] or matrix[2, 2] != 1 << 30:
            raise ValueError("Video display transform includes unsupported perspective or scale.")
        for turns, rotation in enumerate(rotations):
            for flipped in (False, True):
                candidate = rotation @ np.diag([-1, 1]) if flipped else rotation
                if np.allclose(basis, candidate, atol=1e-4):
                    return turns, flipped
        raise ValueError("Video display transform must use a right-angle rotation or mirror.")
    return 0, False


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
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError(f"No video stream found in {path}")
        if stream.codec_context is None:
            raise UndecodableVideoError(UNDECODABLE_VIDEO_MESSAGE)
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


def _video_probe(
    container, stream, first: av.VideoFrame | None, user_ffmpeg: bool = False
) -> VideoProbe:
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
        _audio_compatibility_warning(container, user_ffmpeg=user_ffmpeg),
    )


def _audio_stream_usable(stream, container_format: str) -> bool:
    """Decodable audio can always be kept; other tracks only if copied unchanged."""
    return stream.codec_context is not None or stream_copy_supported(stream, container_format)


def _audio_compatibility_warning(
    container, container_format: str = "mp4", *, user_ffmpeg: bool = False
) -> str:
    """Describe audio changes; a selected user FFmpeg converts otherwise-unusable tracks."""
    audio = [stream for stream in container.streams if stream.type == "audio"]
    unusable = [stream for stream in audio if not _audio_stream_usable(stream, container_format)]
    if unusable and user_ffmpeg:
        return "Audio in a format LocalSR does not include is converted by your FFmpeg."
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
        container = av.open(path)
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


def _source_timing(path: str) -> tuple[float, int, float, str]:
    """Timing from the container and stream headers, which need no decoder."""
    with av.open(path) as container:
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
            _audio_compatibility_warning(container, user_ffmpeg=True),
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
        if external_ffmpeg is None:
            raise UndecodableVideoError(UNDECODABLE_VIDEO_MESSAGE)
        from .external_ffmpeg import first_frame_source

        try:
            fps, count, duration, audio_warning = _source_timing(path)
        except av.FFmpegError:
            from .external_ffmpeg import external_timing

            (fps, duration), count = external_timing(external_ffmpeg, path), 0
            audio_warning = ""
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
            ),
            preview,
        )
    with _video_decoder(path) as (container, stream):
        report("decoding_video")
        first = next(display_frames(container, stream), None)
        if first is None:
            raise ValueError("This video contains no decodable picture frames.")
        probe = _video_probe(container, stream, first, external_ffmpeg is not None)
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
        pending = None
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
            if pending is not None:
                interval = timestamp - pending.timestamp
                if interval <= 0:
                    raise ValueError("Video presentation timestamps must increase.")
                yield replace(pending, duration=interval)
                pending = None
            if end_frame is not None and index > end_frame:
                return
            rgb = _frame_rgb(stream, frame, hdr_mode)
            duration = getattr(frame, "duration", 0) or 0
            interval = (
                duration * frame.time_base if duration > 0 else 1 / (stream.average_rate or 25)
            )
            pending = TimedVideoFrame(index, rgb, timestamp, Fraction(interval), frame.time_base)
        if pending is not None:
            yield pending


def validate_hdr_encoder(video_codec: str = "av1", external_ffmpeg=None) -> None:
    """Fail before inference if this export cannot carry 10-bit HDR."""
    try:
        validate_output(video_codec, "mkv" if video_codec == "ffv1" else "mp4", hdr=True)
        if video_codec in EXTERNAL_OUTPUT_CODECS:
            if external_ffmpeg is None:
                raise ValueError("HEVC HDR export requires a selected external FFmpeg.")
            external_ffmpeg.encoder_for(video_codec, hdr=True)
            require_bundled_encoder(encode_spec("ffv1", hdr=True))
        else:
            require_bundled_encoder(encode_spec(video_codec, hdr=True))
    except (av.FFmpegError, ValueError, RuntimeError) as error:
        raise VideoStageError("HDR encode", str(error)) from error


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
    """Encode an FFV1 master, then let the user's FFmpeg write H.264/HEVC."""
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
            **master_options,
        )
        if stage_callback is not None:
            stage_callback("external_encode")
        from .external_ffmpeg import ExternalFFmpegError, transcode_output

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
        os.replace(final, destination)
        return str(destination)
    finally:
        for temporary in (master, final):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def encode_video(
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
) -> str:
    """Encode an iterable of (rgb_uint8_HxWx3) frames into a video file.

    The enhanced picture is encoded to an app-owned temporary container, then
    remuxed with source audio/subtitles. Compatible audio streams are copied;
    audio the container cannot carry is converted to Opus. H.264/HEVC exports
    are delegated to the user's own FFmpeg from a lossless FFV1 master. The
    final destination appears only through one atomic replace.
    """
    container_format = normalize_container(container_format)
    if hdr_format:
        if hdr_format not in {"HLG", "PQ"} or sdr_bt709:
            raise ValueError("HDR output must be HLG or PQ, without SDR conversion.")
        validate_hdr_encoder(video_codec, external_ffmpeg)
    validate_output(video_codec, container_format, hdr=bool(hdr_format))
    if video_codec in EXTERNAL_OUTPUT_CODECS:
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
            source_start_seconds=source_start_seconds,
            preserve_timing=preserve_timing,
            warning_callback=warning_callback,
            sdr_bt709=sdr_bt709,
            encoder_threads=encoder_threads,
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

        def mux(packet):
            duration = durations.pop(packet.pts * packet.time_base, None)
            if duration is not None:
                packet.duration = max(1, round(duration / packet.time_base))
            output_container.mux(packet)

        from itertools import chain

        for item in chain((first,), iterator):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("video job cancelled")
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
            for packet in stream.encode(frame):
                mux(packet)
            frame_count += 1
        if frame_count == 0:
            raise ValueError("the inference pipeline produced no frames")
        for packet in stream.encode():
            mux(packet)
        output_container.close()
        output_container = None

        source = Path(audio_source) if audio_source else None
        if source is not None and source.is_file():
            copied = _remux_source_streams(
                encoded_temporary,
                source,
                mux_temporary,
                container_format=container_format,
                source_start_seconds=float(source_start_seconds),
                cancel_event=cancel_event,
                warning_callback=warning_callback,
            )
            if copied:
                os.replace(mux_temporary, destination)
            else:
                os.replace(encoded_temporary, destination)
        else:
            os.replace(encoded_temporary, destination)
        return destination_path
    except InterruptedError:
        raise
    except VideoStageError:
        raise
    except BaseException as error:
        raise VideoStageError("encode", str(error)) from error
    finally:
        if output_container is not None:
            try:
                output_container.close()
            except Exception:
                pass
        for temporary in (encoded_temporary, mux_temporary):
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _owned_output_temporary(destination: Path, phase: str, directory: str | None = None) -> Path:
    """Reserve one unique LocalSR-owned sibling for an atomic final replace."""
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.localsr-{phase}-",
        suffix=".tmp",
        dir=directory or destination.parent,
    )
    os.close(descriptor)
    return Path(name)


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


def _next_trimmed_packet(iterator, start_seconds: float, end_seconds: float, cancel_event=None):
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
        _shift_packet_to_trimmed_timeline(packet, start_seconds)
        return packet
    return None


def _remux_source_streams(
    video_path: Path,
    source_path: Path,
    output_path: Path,
    *,
    container_format: str,
    source_start_seconds: float = 0.0,
    cancel_event: threading.Event | None = None,
    warning_callback=None,
) -> bool:
    """Interleave encoded video and compatible source audio/subtitles."""
    video_container = None
    source_container = None
    output_container = None
    try:
        video_container = av.open(str(video_path))
        source_container = av.open(str(source_path))
        video_in = next(
            (stream for stream in video_container.streams if stream.type == "video"), None
        )
        if video_in is None:
            raise VideoStageError("remux", "the enhanced temporary file has no video stream")

        auxiliary_inputs = [
            stream for stream in source_container.streams if stream.type in {"audio", "subtitle"}
        ]
        audio_warning = _audio_compatibility_warning(source_container, container_format)
        if audio_warning:
            if warning_callback:
                warning_callback(audio_warning)
            else:
                warnings.warn(audio_warning, stacklevel=2)
            auxiliary_inputs = [
                stream
                for stream in auxiliary_inputs
                if stream.type != "audio" or _audio_stream_usable(stream, container_format)
            ]
        if not auxiliary_inputs:
            return False

        output_container = av.open(
            str(output_path), mode="w", format=muxer_format(container_format)
        )
        # Copy the encoded stream's parameters; its decoder name (libdav1d) is not an encoder.
        video_out = output_container.add_stream_from_template(video_in, opaque=True)
        auxiliary_outputs: dict[int, object] = {}
        converted_audio: dict[int, object] = {}
        for stream in auxiliary_inputs:
            if needs_audio_transcode(stream, container_format):
                converted_audio[stream.index] = add_opus_stream(output_container, stream)
                continue
            try:
                auxiliary_outputs[stream.index] = output_container.add_stream_from_template(stream)
            except (av.FFmpegError, ValueError, OSError) as error:
                if stream.type == "subtitle":
                    # The requested container does not support this subtitle
                    # codec. The enhanced video remains valid and the stream is
                    # not falsely advertised as copied.
                    message = f"Subtitle stream {stream.index} was omitted: incompatible with {container_format}."
                    if warning_callback:
                        warning_callback(message)
                    else:
                        warnings.warn(message, stacklevel=2)
                    continue
                raise VideoStageError(
                    "audio remux",
                    f"{getattr(stream.codec_context, 'name', 'audio')} is incompatible with {container_format}: {error}",
                ) from error

        selected_aux = [stream for stream in auxiliary_inputs if stream.index in auxiliary_outputs]
        if not selected_aux and not converted_audio:
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

        def copied_packets():
            if not selected_aux:
                return
            iterator = source_container.demux(*selected_aux)
            while (
                packet := _next_trimmed_packet(
                    iterator, source_start_seconds, source_end_seconds, cancel_event
                )
            ) is not None:
                packet.stream = auxiliary_outputs[packet.stream.index]
                yield packet

        iterators = [video_packets(), copied_packets()]
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
    except BaseException:
        raise
    finally:
        for container in (output_container, source_container, video_container):
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
