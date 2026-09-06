"""PyAV-based video decode and encode for the worker.

All ffmpeg work happens here so the worker never shells out to a binary.
The encoder uses an open temp file and an atomic rename at the end so a
crashed or cancelled job never leaves a half-written output at the final
path.
"""

from __future__ import annotations

import os
import tempfile
import threading
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    fps: float
    frame_count: int
    codec: str
    duration_seconds: float


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
    Reject perspective/arbitrary transforms instead of silently changing framing.
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
        if matrix[0, 2] or matrix[1, 2] or matrix[2, 0] or matrix[2, 1]:
            raise ValueError(
                "Video display transform includes unsupported translation or perspective."
            )
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
            "HDR (PQ/HLG) video needs an SDR conversion before enhancement. "
            "LocalSR currently exports 8-bit SDR video."
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
def _video_decoder(path: str, *, frame_threads: bool = False):
    with av.open(path) as container:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValueError(f"No video stream found in {path}")
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


def probe_video(path: str) -> VideoProbe:
    """Probe metadata and one frame to resolve its display orientation."""
    with _video_decoder(path) as (container, stream):
        avg_fps = stream.average_rate
        fps = float(avg_fps) if avg_fps else 0.0
        frame_count = int(stream.frames or 0)
        if frame_count == 0 and container.duration:
            duration_sec = float(container.duration) / 1_000_000.0
            frame_count = int(round(duration_sec * fps)) if fps else 0
        duration = float(container.duration) / 1_000_000.0 if container.duration else 0.0
        first = next(container.decode(stream), None)
        turns, _ = _display_transform(first) if first is not None else (0, False)
        width, height = int(stream.width or 0), int(stream.height or 0)
        if turns % 2:
            width, height = height, width
        return VideoProbe(
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            codec=str(stream.codec_context.name or "unknown"),
            duration_seconds=duration,
        )


def decode_frames(
    path: str,
    start_frame: int | None = None,
    end_frame: int | None = None,
):
    """Yield (frame_index, rgb_uint8_HxWx3) for every decoded frame.

    Frames are converted to RGB on decode. start_frame/end_frame are
    inclusive 0-indexed bounds; None means unbounded on that side.
    """
    for frame in decode_timed_frames(path, start_frame, end_frame):
        yield frame.index, frame.rgb


def decode_timed_frames(
    path: str,
    start_frame: int | None = None,
    end_frame: int | None = None,
    cancel_event: threading.Event | None = None,
):
    """Decode oriented SDR frames with exact source times and one-frame lookahead."""
    start = max(0, int(start_frame or 0))
    selected_frame_count(0, start, end_frame)
    with _video_decoder(path, frame_threads=True) as (container, stream):
        pending = None
        for index, frame in enumerate(container.decode(stream)):
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
            _check_sdr(stream, frame)
            rgb = frame.to_ndarray(format="rgb24")
            turns, flipped = _display_transform(frame)
            rgb = np.rot90(rgb, turns)
            if flipped:
                rgb = np.fliplr(rgb)
            rgb = np.ascontiguousarray(rgb)
            duration = getattr(frame, "duration", 0) or 0
            interval = (
                duration * frame.time_base if duration > 0 else 1 / (stream.average_rate or 25)
            )
            pending = TimedVideoFrame(index, rgb, timestamp, Fraction(interval), frame.time_base)
        if pending is not None:
            yield pending


def encode_video(
    frames,
    destination_path: str,
    *,
    fps: float,
    container_format: str = "mp4",
    codec: str = "libx264",
    crf: int = 18,
    width: int,
    height: int,
    pixel_format: str = "yuv420p",
    audio_source: str | None = None,
    source_start_seconds: float = 0.0,
    preserve_timing: bool = True,
    cancel_event: threading.Event | None = None,
    warning_callback=None,
) -> str:
    """Encode an iterable of (rgb_uint8_HxWx3) frames into a video file.

    The enhanced picture is encoded to an app-owned temporary container, then
    remuxed with compatible source audio/subtitles without re-encoding those
    streams. The final destination appears only through one atomic replace.
    """
    destination = Path(destination_path)
    encoded_temporary = _owned_output_temporary(destination, "video")
    mux_temporary = _owned_output_temporary(destination, "mux")
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
        output_container = av.open(str(encoded_temporary), mode="w", format=container_format)
        stream = output_container.add_stream(codec, rate=fps_fraction)
        stream.time_base = time_base
        stream.codec_context.time_base = time_base
        stream.width = int(width)
        stream.height = int(height)
        stream.pix_fmt = pixel_format
        # Keep decode and presentation order aligned so VFR packet durations
        # describe the displayed interval, including the final held frame.
        stream.options = {"crf": str(max(0, min(51, int(crf)))), "preset": "medium", "bf": "0"}
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
            if rgb.dtype != np.uint8:
                rgb = np.clip(rgb, 0, 255).astype(np.uint8)
            if rgb.shape != (int(height), int(width), 3):
                raise ValueError(
                    f"frame {frame_count} has shape {rgb.shape}; expected ({height}, {width}, 3)"
                )
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
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


def _owned_output_temporary(destination: Path, phase: str) -> Path:
    """Reserve one unique LocalSR-owned sibling for an atomic final replace."""
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.localsr-{phase}-",
        suffix=".tmp",
        dir=destination.parent,
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


def _next_packet(iterator):
    for packet in iterator:
        if packet.dts is not None or packet.pts is not None:
            return packet
    return None


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
        if not auxiliary_inputs:
            return False

        output_container = av.open(str(output_path), mode="w", format=container_format)
        video_out = output_container.add_stream_from_template(video_in)
        auxiliary_outputs: dict[int, object] = {}
        for stream in auxiliary_inputs:
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
                    f"{stream.codec_context.name or 'audio'} is incompatible with {container_format}: {error}",
                ) from error

        selected_aux = [stream for stream in auxiliary_inputs if stream.index in auxiliary_outputs]
        if not selected_aux:
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
            else None
        )
        source_end_seconds = source_start_seconds + (duration or float("inf"))
        video_packets = video_container.demux(video_in)
        auxiliary_packets = source_container.demux(*selected_aux)
        video_packet = _next_packet(video_packets)
        auxiliary_packet = _next_trimmed_packet(
            auxiliary_packets, source_start_seconds, source_end_seconds, cancel_event
        )
        while video_packet is not None or auxiliary_packet is not None:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            use_video = auxiliary_packet is None or (
                video_packet is not None
                and _packet_time(video_packet) <= _packet_time(auxiliary_packet)
            )
            if use_video:
                packet = video_packet
                packet.stream = video_out
                output_container.mux(packet)
                video_packet = _next_packet(video_packets)
            else:
                packet = auxiliary_packet
                packet_time = _packet_time(packet)
                if duration is None or packet_time <= duration + 0.05:
                    packet.stream = auxiliary_outputs[packet.stream.index]
                    output_container.mux(packet)
                auxiliary_packet = _next_trimmed_packet(
                    auxiliary_packets, source_start_seconds, source_end_seconds, cancel_event
                )
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
