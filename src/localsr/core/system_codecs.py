"""The codecs that ship with the operating system, reached through a helper program.

LocalSR's own media runtime contains no H.264, HEVC or AAC code. macOS does,
under Apple's licences, behind AVFoundation. ``localsr-media`` (see
``packaging/macos/media-helper``) reads and writes such files with the system
frameworks and exchanges raw frames with the worker over pipes, so none of that
code is ever loaded into LocalSR. Frames go through the same lossless FFV1/FLAC
intermediates as the user-installed FFmpeg route, so the rest of the pipeline
does not care which program opened a file.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from .external_ffmpeg import ExternalFFmpegError
from .media_codecs import encode_spec

ENVIRONMENT_VARIABLE = "LOCALSR_MEDIA_HELPER"
HELPER_NAME = "localsr-media"
STREAM_TAG = "localsr-media/1"
FRAME_HEADER = struct.Struct("<qiI")
SYSTEM_ENCODERS = {"h264": "h264_videotoolbox", "hevc": "hevc_videotoolbox"}

# FFmpeg's names for colour tags, as the helper reports them, to AVCOL_* values.
_PRIMARIES = {
    "bt709": 1,
    "bt470m": 4,
    "bt470bg": 5,
    "smpte170m": 6,
    "smpte240m": 7,
    "film": 8,
    "bt2020": 9,
    "smpte428": 10,
    "smpte431": 11,
    "smpte432": 12,
}
_TRANSFERS = {
    "bt709": 1,
    "gamma22": 4,
    "gamma28": 5,
    "smpte170m": 6,
    "smpte240m": 7,
    "linear": 8,
    "iec61966-2-4": 11,
    "iec61966-2-1": 13,
    "bt2020-10": 14,
    "bt2020-12": 15,
    "smpte2084": 16,
    "smpte428": 17,
    "arib-std-b67": 18,
}
_MATRICES = {
    "rgb": 0,
    "bt709": 1,
    "fcc": 4,
    "bt470bg": 5,
    "smpte170m": 6,
    "smpte240m": 7,
    "ycgco": 8,
    "bt2020nc": 9,
    "bt2020c": 10,
}


class SystemCodecsError(ExternalFFmpegError):
    """A failed or unavailable system-codec conversion."""


def helper_candidates() -> list[Path]:
    """Where the helper lives: next to the frozen worker, or in a source checkout's build tree."""
    override = os.environ.get(ENVIRONMENT_VARIABLE, "").strip()
    if override:
        return [Path(override).expanduser()]
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / HELPER_NAME)
    root = Path(__file__).resolve().parents[3]
    candidates.append(root / "build" / "media-helper" / HELPER_NAME)
    return candidates


def _helper_version(path: Path) -> int | None:
    try:
        result = subprocess.run(
            [str(path), "version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        info = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if info.get("helper") != HELPER_NAME or info.get("stream") != STREAM_TAG:
        return None
    return int(info.get("version") or 0) or None


_detected: dict[str, SystemCodecs | None] = {}
_detect_lock = threading.Lock()


def detect_system_codecs(*, refresh: bool = False) -> SystemCodecs | None:
    """This platform's system-codec helper, or ``None``. Cached for the process."""
    if sys.platform != "darwin" and not os.environ.get(ENVIRONMENT_VARIABLE):
        return None
    with _detect_lock:
        if not refresh and "helper" in _detected:
            return _detected["helper"]
        found = None
        for candidate in helper_candidates():
            if candidate.is_file() and os.access(candidate, os.X_OK):
                version = _helper_version(candidate)
                if version:
                    found = SystemCodecs(str(candidate), version)
                    break
        _detected["helper"] = found
        return found


def _quality(crf: int) -> float:
    """Map LocalSR's 0-51 CRF scale onto VideoToolbox's 0-1 constant-quality scale."""
    return max(0.05, min(1.0, 1.0 - max(0, min(51, int(crf))) / 51 * 0.9))


def _plane_geometry(width: int, height: int, pixel_format: str) -> list[tuple[int, int]]:
    """Tight (row_bytes, rows) of the two planes of a packed nv12/p010le frame."""
    bytes_per_sample = 2 if pixel_format == "p010le" else 1
    return [
        (width * bytes_per_sample, height),
        (((width + 1) // 2) * 2 * bytes_per_sample, (height + 1) // 2),
    ]


def _fill_plane(plane, data: bytes, row_bytes: int, rows: int) -> None:
    if plane.line_size == row_bytes and plane.buffer_size == row_bytes * rows:
        plane.update(data)
        return
    padded = np.zeros(plane.buffer_size, dtype=np.uint8)
    view = padded[: plane.line_size * rows].reshape(rows, plane.line_size)
    view[:, :row_bytes] = np.frombuffer(data, dtype=np.uint8).reshape(rows, row_bytes)
    plane.update(padded.tobytes())


def _tight_plane(plane, row_bytes: int, rows: int) -> bytes:
    if plane.line_size == row_bytes:
        return bytes(plane)[: row_bytes * rows]
    view = np.frombuffer(plane, dtype=np.uint8)[: plane.line_size * rows]
    return view.reshape(rows, plane.line_size)[:, :row_bytes].tobytes()


def frame_from_payload(payload: bytes, width: int, height: int, pixel_format: str) -> av.VideoFrame:
    """Rebuild a PyAV frame from the helper's tightly packed planes."""
    geometry = _plane_geometry(width, height, pixel_format)
    expected = sum(row_bytes * rows for row_bytes, rows in geometry)
    if len(payload) != expected:
        raise SystemCodecsError(f"frame payload has {len(payload)} bytes, expected {expected}")
    frame = av.VideoFrame(width, height, pixel_format)
    offset = 0
    for plane, (row_bytes, rows) in zip(frame.planes, geometry, strict=True):
        _fill_plane(plane, payload[offset : offset + row_bytes * rows], row_bytes, rows)
        offset += row_bytes * rows
    return frame


def payload_from_frame(frame: av.VideoFrame, pixel_format: str) -> bytes:
    """Pack a frame's planes tightly for the helper; converts the pixel format first."""
    packed = frame if frame.format.name == pixel_format else frame.reformat(format=pixel_format)
    geometry = _plane_geometry(packed.width, packed.height, pixel_format)
    return b"".join(
        _tight_plane(plane, row_bytes, rows)
        for plane, (row_bytes, rows) in zip(packed.planes, geometry, strict=True)
    )


def rotate_planar(frame: av.VideoFrame, turns: int, flipped: bool) -> av.VideoFrame:
    """Apply a right-angle rotation and optional mirror to a planar YUV frame."""
    if not turns and not flipped:
        return frame
    dtype = np.uint16 if frame.format.name.endswith("10le") else np.uint8
    planes = []
    for plane in frame.planes:
        row_items = plane.line_size // np.dtype(dtype).itemsize
        view = np.frombuffer(plane, dtype=dtype)[: row_items * plane.height]
        view = view.reshape(plane.height, row_items)[:, : plane.width]
        rotated = np.rot90(view, turns)
        if flipped:
            rotated = rotated[:, ::-1]
        planes.append(np.ascontiguousarray(rotated))
    height, width = planes[0].shape
    result = av.VideoFrame(width, height, frame.format.name)
    for plane, data in zip(result.planes, planes, strict=True):
        _fill_plane(plane, data.tobytes(), data.shape[1] * data.itemsize, data.shape[0])
    for name in ("color_range", "colorspace", "color_primaries", "color_trc"):
        setattr(result, name, getattr(frame, name))
    return result


def _color_values(header: dict) -> tuple[int, int, int, int]:
    color = header.get("color") or {}
    return (
        _PRIMARIES.get(str(color.get("primaries") or ""), 2),
        _TRANSFERS.get(str(color.get("transfer") or ""), 2),
        _MATRICES.get(str(color.get("matrix") or ""), 2),
        2 if header.get("full_range") else 1,
    )


def _color_names(context) -> dict:
    def name(table: dict[str, int], value: int) -> str:
        return next((key for key, code in table.items() if code == value), "")

    return {
        "primaries": name(_PRIMARIES, int(context.color_primaries)),
        "transfer": name(_TRANSFERS, int(context.color_trc)),
        "matrix": name(_MATRICES, int(context.colorspace)),
    }


class _IntermediateVideo:
    """FFV1 video of the lossless intermediate, in display orientation."""

    def __init__(self, container, header: dict) -> None:
        from .video_io import display_turns

        self.container = container
        self.width, self.height = int(header["width"]), int(header["height"])
        self.timescale = int(header["timescale"])
        self.source_format = str(header["format"])
        transform = header.get("transform")
        basis = np.array(transform, dtype=float).reshape(2, 2) if transform else np.eye(2)
        self.turns, self.flipped = display_turns(basis)
        rotated = (self.height, self.width) if self.turns % 2 else (self.width, self.height)
        spec = encode_spec("ffv1", hdr=self.source_format == "p010le")
        fps = Fraction(str(header.get("nominal_fps") or 25)).limit_denominator(1000)
        self.stream = container.add_stream(spec.encoder, rate=fps if fps > 0 else 25)
        self.stream.width, self.stream.height = rotated
        self.stream.pix_fmt = spec.pixel_format
        self.stream.options = spec.options
        self.stream.codec_context.time_base = Fraction(1, self.timescale)
        self.tags = _color_values(header)
        context = self.stream.codec_context
        context.color_primaries, context.color_trc, context.colorspace, context.color_range = (
            self.tags
        )
        self.pixel_format = spec.pixel_format

    def write(self, pts: int, payload: bytes) -> None:
        frame = frame_from_payload(payload, self.width, self.height, self.source_format)
        frame.color_primaries, frame.color_trc, frame.colorspace, frame.color_range = self.tags
        planar = frame.reformat(format=self.pixel_format)
        planar = rotate_planar(planar, self.turns, self.flipped)
        planar.pts = pts
        planar.time_base = Fraction(1, self.timescale)
        for packet in self.stream.encode(planar):
            self.container.mux(packet)

    def flush(self) -> None:
        for packet in self.stream.encode():
            self.container.mux(packet)


class _IntermediateAudio:
    """FLAC audio of the lossless intermediate, muxed in step with the video."""

    def __init__(self, container, audio: dict) -> None:
        self.container = container
        self.source = av.open(str(audio["path"]))
        source_stream = self.source.streams.audio[0]
        self.rate = int(audio.get("sample_rate") or source_stream.rate)
        self.stream = container.add_stream("flac", rate=self.rate)
        self.stream.codec_context.layout = source_stream.layout
        self.offset = int(round(float(audio.get("start") or 0) * self.rate))
        self.frames = self.source.decode(source_stream)
        self.pending: av.AudioFrame | None = None
        self.exhausted = False

    def _next(self) -> av.AudioFrame | None:
        if self.pending is not None:
            frame, self.pending = self.pending, None
            return frame
        if self.exhausted:
            return None
        try:
            frame = next(self.frames)
        except StopIteration:
            self.exhausted = True
            return None
        frame.pts = int(frame.pts or 0) + self.offset
        frame.time_base = Fraction(1, self.rate)
        return frame

    def mux_until(self, seconds: float | None) -> None:
        while (frame := self._next()) is not None:
            if seconds is not None and frame.pts / self.rate > seconds:
                self.pending = frame
                return
            for packet in self.stream.encode(frame):
                self.container.mux(packet)

    def flush(self) -> None:
        self.mux_until(None)
        for packet in self.stream.encode():
            self.container.mux(packet)
        self.source.close()


def _creation_flags() -> int:
    return 0x0800_0000 if os.name == "nt" else 0


@dataclass(frozen=True)
class SystemCodecs:
    """The helper program for this platform's licensed codecs."""

    path: str
    version: int
    label: str = "macOS"

    @property
    def name(self) -> str:
        return f"{self.label} system codecs"

    def _run(self, arguments: list[str], *, timeout: float) -> dict:
        try:
            result = subprocess.run(
                [self.path, *arguments],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                creationflags=_creation_flags(),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise SystemCodecsError(f"Could not run the system codec helper: {error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()[-1:] or [f"exit {result.returncode}"]
            raise SystemCodecsError(detail[0])
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as error:
            raise SystemCodecsError("The system codec helper returned no result.") from error

    def probe(self, path: str) -> dict:
        return self._run(["probe", str(path)], timeout=120)

    def readable(self, path: str, *, audio_only: bool = False) -> bool:
        """Whether the system can decode this file's video (or, for audio_only, its audio)."""
        try:
            info = self.probe(path)
        except SystemCodecsError:
            return False
        if not info.get("readable"):
            return False
        track = info.get("audio" if audio_only else "video")
        return bool(track and track.get("decodable"))

    def timing(self, path: str) -> tuple[float, float]:
        info = self.probe(path)
        video = info.get("video") or {}
        return float(video.get("nominal_fps") or 0.0), float(info.get("duration") or 0.0)

    def encoder_for(self, video_codec: str, *, hdr: bool = False, container: str = "mp4") -> str:
        if video_codec not in SYSTEM_ENCODERS:
            raise SystemCodecsError(f"{self.name} do not encode {video_codec}.")
        if hdr and video_codec != "hevc":
            raise SystemCodecsError("HDR export needs HEVC.")
        if container not in {"mp4", "mov"}:
            raise SystemCodecsError(
                f"{self.name} write H.264/HEVC into MP4 or MOV. Choose MP4, or select an "
                "FFmpeg you installed for MKV."
            )
        return SYSTEM_ENCODERS[video_codec]

    @contextmanager
    def _process(self, arguments: list[str], *, stdin: bool = False) -> Iterator[subprocess.Popen]:
        with tempfile.TemporaryFile(mode="w+t") as errors:
            process = subprocess.Popen(
                [self.path, *arguments],
                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=errors,
                creationflags=_creation_flags(),
            )
            process.error_log = errors  # type: ignore[attr-defined]
            try:
                yield process
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    @staticmethod
    def _failure(process: subprocess.Popen, stage: str) -> SystemCodecsError:
        errors = process.error_log  # type: ignore[attr-defined]
        errors.seek(0)
        detail = " ".join(errors.read().strip().splitlines()[-3:])
        return SystemCodecsError(
            f"System codecs failed during {stage}: {detail or process.returncode}"
        )

    @staticmethod
    def _wait(process: subprocess.Popen, cancel_event: threading.Event | None, stage: str) -> None:
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise InterruptedError("video job cancelled")
            time.sleep(0.05)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("video job cancelled")

    def _frames(
        self, process: subprocess.Popen, cancel_event: threading.Event | None
    ) -> Iterator[tuple[int, int, bytes]]:
        stdout = process.stdout
        assert stdout is not None
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                raise InterruptedError("video job cancelled")
            header = stdout.read(FRAME_HEADER.size)
            if not header:
                return
            if len(header) != FRAME_HEADER.size:
                raise SystemCodecsError("The system codec helper stopped mid-frame.")
            pts, duration, length = FRAME_HEADER.unpack(header)
            payload = stdout.read(length)
            if len(payload) != length:
                raise SystemCodecsError("The system codec helper stopped mid-frame.")
            yield pts, duration, payload

    def convert_source(
        self,
        path: str,
        destination: str | Path,
        *,
        video: bool = True,
        frames: int = 0,
        cancel_event: threading.Event | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        """Write a lossless FFV1/FLAC MKV of ``path`` decoded by the system codecs.

        ``video=False`` converts only the soundtrack; ``frames`` limits the video to
        its first frames (a one-frame file serves the preview probe).
        """
        destination = Path(destination)
        with tempfile.TemporaryDirectory(
            prefix="localsr-media-", dir=destination.parent
        ) as scratch:
            arguments = ["decode", str(path)]
            if frames:
                arguments += ["--max-frames", str(frames), "--no-audio"]
            else:
                arguments += ["--audio", str(Path(scratch) / "audio.wav")]
            if not video:
                arguments.append("--no-video")
            with self._process(arguments) as process:
                stdout = process.stdout
                assert stdout is not None
                header_line = stdout.readline()
                if not header_line:
                    self._wait(process, cancel_event, "source conversion")
                    raise self._failure(process, "source conversion")
                header = json.loads(header_line)
                if header.get("stream") != STREAM_TAG:
                    raise SystemCodecsError("Unexpected data from the system codec helper.")
                audio = header.get("audio") or None
                count = 0
                with av.open(str(destination), "w", format="matroska") as container:
                    video_writer = (
                        _IntermediateVideo(container, header) if header.get("video") else None
                    )
                    audio_writer = _IntermediateAudio(container, audio) if audio else None
                    if video_writer is not None:
                        for pts, _duration, payload in self._frames(process, cancel_event):
                            if audio_writer is not None:
                                audio_writer.mux_until(pts / video_writer.timescale)
                            video_writer.write(pts, payload)
                            count += 1
                            if progress is not None:
                                progress(count)
                        video_writer.flush()
                    if audio_writer is not None:
                        audio_writer.flush()
                self._wait(process, cancel_event, "source conversion")
                if process.returncode != 0:
                    raise self._failure(process, "source conversion")
                if video_writer is not None and count == 0:
                    raise SystemCodecsError("The system codecs decoded no frames from this video.")

    def transcode_output(
        self,
        source: Path,
        destination: Path,
        *,
        video_codec: str,
        container: str,
        crf: int,
        hdr_format: str = "",
        width: int = 0,
        height: int = 0,
        fps: float = 30.0,
        cancel_event: threading.Event | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        """Encode a LocalSR FFV1 master into H.264/HEVC (and AAC) with the system encoders."""
        del width, height  # the master's own dimensions are used
        self.encoder_for(video_codec, hdr=bool(hdr_format), container=container)
        pixel_format = "p010le" if hdr_format else "nv12"
        with tempfile.TemporaryDirectory(
            prefix="localsr-media-", dir=Path(destination).parent
        ) as scratch:
            wav = self._master_audio(source, Path(scratch) / "audio.wav", cancel_event)
            arguments = [
                "encode",
                "--output",
                str(destination),
                "--codec",
                video_codec,
                "--quality",
                f"{_quality(crf):.3f}",
            ]
            if hdr_format:
                arguments += ["--hdr", hdr_format]
            if wav is not None:
                arguments += ["--audio", str(wav)]
            with av.open(str(source)) as master, self._process(arguments, stdin=True) as process:
                stdin = process.stdin
                assert stdin is not None
                stream = master.streams.video[0]
                stream.thread_type = "AUTO"
                time_base = stream.time_base or Fraction(1, 1000)
                header = {
                    "stream": STREAM_TAG,
                    "width": stream.width,
                    "height": stream.height,
                    "format": pixel_format,
                    "timescale": time_base.denominator,
                    "full_range": int(stream.codec_context.color_range) == 2,
                    "color": _color_names(stream.codec_context),
                    "fps": float(fps) if fps and fps > 0 else 0.0,
                }
                count = 0
                try:
                    stdin.write(json.dumps(header).encode("utf-8") + b"\n")
                    for frame in master.decode(stream):
                        if cancel_event is not None and cancel_event.is_set():
                            raise InterruptedError("video job cancelled")
                        pts = (
                            int(frame.pts if frame.pts is not None else count) * time_base.numerator
                        )
                        payload = payload_from_frame(frame, pixel_format)
                        stdin.write(FRAME_HEADER.pack(pts, 0, len(payload)))
                        stdin.write(payload)
                        count += 1
                        if progress is not None:
                            progress(count)
                    stdin.close()
                except BrokenPipeError:
                    self._wait(process, cancel_event, f"{video_codec.upper()} export")
                    raise self._failure(process, f"{video_codec.upper()} export") from None
                self._wait(process, cancel_event, f"{video_codec.upper()} export")
                if process.returncode != 0:
                    raise self._failure(process, f"{video_codec.upper()} export")
                if count == 0:
                    raise SystemCodecsError("The master video contains no frames.")

    @staticmethod
    def _master_audio(source: Path, wav: Path, cancel_event: threading.Event | None) -> Path | None:
        """Decode the master's audio to 16-bit PCM WAV, aligned to the video's start."""
        with av.open(str(source)) as master:
            audio = next((stream for stream in master.streams if stream.type == "audio"), None)
            if audio is None:
                return None
            video = master.streams.video[0]
            video_start = float((video.start_time or 0) * (video.time_base or Fraction(1, 1000)))
            channels = 1 if audio.codec_context.layout.nb_channels == 1 else 2
            rate = int(audio.rate or 48000)
            resampler = av.AudioResampler(
                format="s16", layout="mono" if channels == 1 else "stereo", rate=rate
            )
            with wave.open(str(wav), "wb") as output:
                output.setnchannels(channels)
                output.setsampwidth(2)
                output.setframerate(rate)
                first = True
                for frame in master.decode(audio):
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("video job cancelled")
                    if first:
                        first = False
                        gap = float(frame.time or 0) - video_start
                        if gap > 0.001:
                            output.writeframes(bytes(int(gap * rate) * channels * 2))
                    for converted in resampler.resample(frame):
                        output.writeframes(
                            bytes(converted.planes[0])[: converted.samples * channels * 2]
                        )
                for converted in resampler.resample(None):
                    output.writeframes(
                        bytes(converted.planes[0])[: converted.samples * channels * 2]
                    )
        return wav


__all__ = [
    "ENVIRONMENT_VARIABLE",
    "HELPER_NAME",
    "STREAM_TAG",
    "SYSTEM_ENCODERS",
    "SystemCodecs",
    "SystemCodecsError",
    "detect_system_codecs",
    "frame_from_payload",
    "helper_candidates",
    "payload_from_frame",
    "rotate_planar",
]
