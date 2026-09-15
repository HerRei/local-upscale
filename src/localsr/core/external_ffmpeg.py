"""Optional, user-installed FFmpeg for patent-licensed formats.

LocalSR never downloads, bundles or links this program. When the user selects
an FFmpeg executable they installed themselves, LocalSR runs it as a separate
process to convert H.264/HEVC/AAC and other formats that LocalSR's own media
runtime deliberately omits. Frames travel through lossless FFV1/FLAC files in
LocalSR's temporary directory, so no code from that program is loaded here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import av

from .media_codecs import (
    EXTERNAL_FFMPEG_HINT,
    UNDECODABLE_VIDEO_MESSAGE,
    stream_copy_supported,
    video_stream_decodable,
)

ENVIRONMENT_VARIABLE = "LOCALSR_EXTERNAL_FFMPEG"
_VERSION_PATTERN = re.compile(r"^ffmpeg version (\S+)", re.MULTILINE)
_H264_ENCODERS = (
    "libx264",
    "h264_videotoolbox",
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "h264_mf",
)
_HEVC_ENCODERS = (
    "libx265",
    "hevc_videotoolbox",
    "hevc_nvenc",
    "hevc_qsv",
    "hevc_amf",
    "hevc_mf",
)
_COMMON_LOCATIONS = (
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
    "/snap/bin/ffmpeg",
    r"C:\ffmpeg\bin\ffmpeg.exe",
)


class ExternalFFmpegError(RuntimeError):
    """A failed or unavailable user-installed FFmpeg conversion."""


@dataclass(frozen=True)
class ExternalFFmpeg:
    path: str
    version: str
    encoders: frozenset[str]

    def encoder_for(self, video_codec: str, *, hdr: bool = False) -> str:
        candidates = _H264_ENCODERS if video_codec == "h264" else _HEVC_ENCODERS
        if hdr:
            candidates = ("libx265",)
        for name in candidates:
            if name in self.encoders:
                return name
        label = "10-bit HEVC (libx265)" if hdr else video_codec.upper()
        raise ExternalFFmpegError(
            f"The selected FFmpeg ({self.path}) has no {label} encoder. Install a full FFmpeg build."
        )


def discover_candidates() -> list[str]:
    """Return plausible installed FFmpeg executables, without running them."""
    found: list[str] = []
    which = shutil.which("ffmpeg")
    for candidate in ([which] if which else []) + list(_COMMON_LOCATIONS):
        if candidate and Path(candidate).is_file() and candidate not in found:
            found.append(candidate)
    return found


def _creation_flags() -> int:
    return 0x0800_0000 if os.name == "nt" else 0


def load_external_ffmpeg(path: str | None) -> ExternalFFmpeg | None:
    """Validate the user's selected FFmpeg; ``None`` or empty means disabled."""
    selected = (path or os.environ.get(ENVIRONMENT_VARIABLE, "")).strip()
    if not selected:
        return None
    executable = Path(selected).expanduser()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ExternalFFmpegError(
            f"The selected external FFmpeg is not an executable file: {selected}"
        )
    try:
        version = subprocess.run(
            [str(executable), "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_creation_flags(),
        )
        encoders = subprocess.run(
            [str(executable), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ExternalFFmpegError(f"Could not run the selected FFmpeg: {error}") from error
    match = _VERSION_PATTERN.search(version.stdout)
    if version.returncode != 0 or match is None:
        raise ExternalFFmpegError(f"The selected program does not identify as FFmpeg: {selected}")
    names = frozenset(
        parts[1]
        for line in encoders.stdout.splitlines()
        if len(parts := line.split()) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS"
    )
    return ExternalFFmpeg(str(executable), match.group(1), names)


def _run(
    ffmpeg: ExternalFFmpeg,
    arguments: list[str],
    *,
    cancel_event: threading.Event | None,
    progress: Callable[[int], None] | None = None,
    stage: str,
) -> None:
    command = [
        ffmpeg.path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        *arguments,
    ]
    with tempfile.TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            creationflags=_creation_flags(),
        )
        stdout = process.stdout
        assert stdout is not None

        def watch_cancel():
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return
                time.sleep(0.1)

        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        for line in stdout:
            if progress is not None and line.startswith("frame="):
                try:
                    progress(int(line.split("=", 1)[1]))
                except ValueError:
                    pass
        code = process.wait()
        watcher.join(timeout=1)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("video job cancelled")
        if code != 0:
            errors.seek(0)
            detail = errors.read().strip().splitlines()[-6:]
            raise ExternalFFmpegError(
                f"External FFmpeg failed during {stage}: " + (" ".join(detail) or f"exit {code}")
            )


def _run_with_fallbacks(
    ffmpeg: ExternalFFmpeg,
    attempts: list[list[str]],
    *,
    cancel_event: threading.Event | None,
    progress: Callable[[int], None] | None,
    stage: str,
    output: Path,
) -> int:
    """Run each argument list until one succeeds; return the successful attempt's index."""
    last_error: ExternalFFmpegError | None = None
    for index, arguments in enumerate(attempts):
        try:
            _run(ffmpeg, arguments, cancel_event=cancel_event, progress=progress, stage=stage)
            return index
        except ExternalFFmpegError as error:
            last_error = error
            try:
                output.unlink()
            except FileNotFoundError:
                pass
    assert last_error is not None
    raise last_error


@dataclass(frozen=True)
class SourcePlan:
    video_decodable: bool
    audio_needs_conversion: bool


def plan_source(path: str, container: str = "mkv") -> SourcePlan:
    """Inspect which parts of a source LocalSR's bundled runtime can handle.

    A container LocalSR cannot even read (for example FLV) needs full conversion.
    """
    try:
        source = av.open(path)
    except av.FFmpegError:
        if Path(path).is_file():
            return SourcePlan(False, True)
        raise
    with source:
        video = next((stream for stream in source.streams if stream.type == "video"), None)
        if video is None:
            raise ValueError(f"No video stream found in {path}")
        audio_help = any(
            stream.codec_context is None and not stream_copy_supported(stream, container)
            for stream in source.streams
            if stream.type == "audio"
        )
        return SourcePlan(video_stream_decodable(video), audio_help)


def _intermediate_arguments(source: str, destination: Path, *, video: bool, frames: int = 0):
    base = ["-i", source, "-map_metadata", "0", "-map_chapters", "-1", "-dn"]
    if video:
        base += [
            "-map",
            "0:v:0",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-g",
            "1",
            "-fps_mode",
            "passthrough",
        ]
    else:
        base += ["-vn"]
    if frames:
        return [*base, "-frames:v", str(frames), "-an", "-sn", "-f", "matroska", str(destination)]
    with_subtitles = [
        *base,
        "-map",
        "0:a?",
        "-c:a",
        "flac",
        "-map",
        "0:s?",
        "-c:s",
        "copy",
        "-copyts",
        "-f",
        "matroska",
        str(destination),
    ]
    without_subtitles = [
        *base,
        "-map",
        "0:a?",
        "-c:a",
        "flac",
        "-sn",
        "-copyts",
        "-f",
        "matroska",
        str(destination),
    ]
    return [with_subtitles, without_subtitles]


def ensure_free_space(directory: str | Path, required_bytes: int, purpose: str) -> None:
    free = shutil.disk_usage(directory).free
    if free < required_bytes:
        raise ExternalFFmpegError(
            f"Not enough free disk space for {purpose}: about {required_bytes / 1e9:.1f} GB "
            f"is needed in {directory}, {free / 1e9:.1f} GB is available."
        )


def lossless_size_estimate(width: int, height: int, frames: int, *, ten_bit: bool = False) -> int:
    """Conservative FFV1 size estimate: about 60% of raw 4:2:0 samples."""
    bytes_per_pixel = 1.5 * (2 if ten_bit else 1)
    return int(max(1, width) * max(1, height) * max(1, frames) * bytes_per_pixel * 0.6)


@contextmanager
def decodable_source(
    path: str,
    *,
    ffmpeg: ExternalFFmpeg | None,
    temporary_directory: str | None = None,
    cancel_event: threading.Event | None = None,
    progress: Callable[[int], None] | None = None,
    output_container: str = "mkv",
    on_convert: Callable[[], None] | None = None,
) -> Iterator[tuple[str, str]]:
    """Yield ``(frame_source, audio_source)`` paths that the bundled runtime can read.

    The original file is used whenever possible. Otherwise the user's FFmpeg
    creates a lossless FFV1/FLAC intermediate that is deleted afterwards.
    """
    plan = plan_source(path, output_container)
    if plan.video_decodable and not plan.audio_needs_conversion:
        yield path, path
        return
    if ffmpeg is None:
        if not plan.video_decodable:
            raise ValueError(UNDECODABLE_VIDEO_MESSAGE)
        # Keep the enhanced video; the remux step reports the omitted track.
        yield path, path
        return
    if on_convert is not None:
        on_convert()
    directory = tempfile.mkdtemp(prefix="localsr-external-", dir=temporary_directory)
    try:
        intermediate = Path(directory) / "source.mkv"
        if not plan.video_decodable:
            # Undecodable streams expose no dimensions; FFmpeg itself reports
            # an exhausted disk. Refuse obviously insufficient space up front.
            ensure_free_space(directory, 2_000_000_000, "a lossless copy of the source video")
        attempts = _intermediate_arguments(path, intermediate, video=not plan.video_decodable)
        _run_with_fallbacks(
            ffmpeg,
            attempts,
            cancel_event=cancel_event,
            progress=progress,
            stage="source conversion",
            output=intermediate,
        )
        if plan.video_decodable:
            yield path, str(intermediate)
        else:
            yield str(intermediate), str(intermediate)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@contextmanager
def first_frame_source(
    path: str, *, ffmpeg: ExternalFFmpeg, temporary_directory: str | None = None
) -> Iterator[str]:
    """Create a one-frame lossless intermediate for probing an undecodable video."""
    directory = tempfile.mkdtemp(prefix="localsr-probe-", dir=temporary_directory)
    try:
        intermediate = Path(directory) / "frame.mkv"
        _run(
            ffmpeg,
            _intermediate_arguments(path, intermediate, video=True, frames=1),
            cancel_event=None,
            stage="video probe",
        )
        yield str(intermediate)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_FPS = re.compile(r"(\d+(?:\.\d+)?) fps")


def external_timing(ffmpeg: ExternalFFmpeg, path: str) -> tuple[float, float]:
    """Read ``(fps, duration_seconds)`` from FFmpeg's input summary."""
    result = subprocess.run(
        [ffmpeg.path, "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=_creation_flags(),
    )
    duration = _DURATION.search(result.stderr)
    fps = _FPS.search(result.stderr)
    seconds = (
        int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3))
        if duration
        else 0.0
    )
    return (float(fps.group(1)) if fps else 0.0), seconds


_HDR_COLOR = {
    "HLG": ("bt2020", "arib-std-b67", "bt2020nc"),
    "PQ": ("bt2020", "smpte2084", "bt2020nc"),
}


def transcode_output(
    ffmpeg: ExternalFFmpeg,
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
    """Encode a LocalSR FFV1 master into H.264/HEVC with the user's FFmpeg."""
    encoder = ffmpeg.encoder_for(video_codec, hdr=bool(hdr_format))
    video = ["-map", "0:v:0", "-c:v", encoder]
    quality = str(max(0, min(51, int(crf))))
    if encoder in {"libx264", "libx265"}:
        video += ["-crf", quality, "-preset", "medium"]
    else:
        # Hardware/OS encoders use a bitrate: ~0.12 bits per pixel per frame.
        bitrate = max(500_000, int(width * height * max(1.0, fps) * 0.12))
        video += ["-b:v", str(bitrate)]
    if hdr_format:
        primaries, transfer, matrix = _HDR_COLOR[hdr_format]
        video += [
            "-pix_fmt",
            "yuv420p10le",
            "-profile:v",
            "main10",
            "-color_primaries",
            primaries,
            "-color_trc",
            transfer,
            "-colorspace",
            matrix,
            "-x265-params",
            f"log-level=error:repeat-headers=1:colorprim={primaries}:transfer={transfer}"
            f":colormatrix={matrix}",
        ]
    else:
        video += ["-pix_fmt", "yuv420p"]
        if encoder == "libx265":
            video += ["-x265-params", "log-level=error"]
    if container == "mp4" and video_codec == "hevc":
        video += ["-tag:v", "hvc1"]
    muxer = ["-f", "mp4", "-movflags", "+faststart"] if container == "mp4" else ["-f", "matroska"]
    subtitles_copy = ["-map", "0:s?", "-c:s", "mov_text" if container == "mp4" else "copy"]
    audio = ["-map", "0:a?"]
    head = ["-i", str(source), *video, "-fps_mode", "passthrough"]
    # The master carries lossless FLAC or copied audio. MP4 players expect AAC,
    # which the user's FFmpeg (not LocalSR) encodes; MKV keeps the audio as is.
    aac = ["-c:a", "aac", "-b:a", "192k"]
    preferred = aac if container == "mp4" else ["-c:a", "copy"]
    fallback = ["-c:a", "copy"] if container == "mp4" else aac
    attempts = [
        [*head, *audio, *preferred, *subtitles_copy, *muxer, str(destination)],
        [*head, *audio, *fallback, *subtitles_copy, *muxer, str(destination)],
        [*head, *audio, *preferred, "-sn", *muxer, str(destination)],
    ]
    _run_with_fallbacks(
        ffmpeg,
        attempts,
        cancel_event=cancel_event,
        progress=progress,
        stage=f"{video_codec.upper()} export",
        output=destination,
    )


__all__ = [
    "ENVIRONMENT_VARIABLE",
    "EXTERNAL_FFMPEG_HINT",
    "ExternalFFmpeg",
    "ExternalFFmpegError",
    "SourcePlan",
    "decodable_source",
    "discover_candidates",
    "ensure_free_space",
    "first_frame_source",
    "load_external_ffmpeg",
    "lossless_size_estimate",
    "plan_source",
    "transcode_output",
]
