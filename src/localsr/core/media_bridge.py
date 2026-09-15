"""Which program opens or writes the formats LocalSR's own runtime omits.

LocalSR bundles only royalty-free codecs. For H.264, HEVC, AAC and the other
patent-licensed formats it can use, in this order:

1. the codecs that ship with the operating system (macOS today), reached
   through the ``localsr-media`` helper, and
2. an FFmpeg the user installed and selected.

Both hand frames through lossless FFV1/FLAC intermediates, so the pipeline
treats them alike. A :class:`MediaBridge` holds whichever are available and
picks one per file and per export.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import external_ffmpeg as user_ffmpeg
from .external_ffmpeg import ExternalFFmpeg, ExternalFFmpegError, ensure_free_space, plan_source
from .media_codecs import (
    EXTERNAL_FFMPEG_HINT,
    EXTERNAL_OUTPUT_CODECS,
    UNDECODABLE_VIDEO_MESSAGE,
    UndecodableVideoError,
    normalize_container,
)
from .system_codecs import SystemCodecs, SystemCodecsError, detect_system_codecs

Provider = SystemCodecs | ExternalFFmpeg


@dataclass(frozen=True)
class MediaBridge:
    system: SystemCodecs | None = None
    user: ExternalFFmpeg | None = None

    def describe(self) -> str:
        parts = []
        if self.system is not None:
            parts.append(self.system.name)
        if self.user is not None:
            parts.append(f"FFmpeg {self.user.version} at {self.user.path}")
        return ", ".join(parts) or "none"

    def source_provider(self, path: str, *, audio_only: bool = False) -> Provider | None:
        """The program that can decode this file: the system first, then the user's FFmpeg."""
        if self.system is not None and self.system.readable(path, audio_only=audio_only):
            return self.system
        return self.user

    def export_provider(
        self, video_codec: str, *, hdr: bool = False, container: str = "mp4"
    ) -> Provider:
        """The program that writes this export, or raise with the reason none can."""
        reasons: list[str] = []
        if self.system is not None:
            try:
                self.system.encoder_for(video_codec, hdr=hdr, container=container)
                return self.system
            except SystemCodecsError as error:
                reasons.append(str(error))
        if self.user is not None:
            self.user.encoder_for(video_codec, hdr=hdr)
            return self.user
        raise ExternalFFmpegError(reasons[0] if reasons else EXTERNAL_FFMPEG_HINT)

    def encoder_for(self, video_codec: str, *, hdr: bool = False, container: str = "mp4") -> str:
        provider = self.export_provider(video_codec, hdr=hdr, container=container)
        if isinstance(provider, SystemCodecs):
            return provider.encoder_for(video_codec, hdr=hdr, container=container)
        return provider.encoder_for(video_codec, hdr=hdr)

    def converter_label(self, path: str, *, audio_only: bool = False) -> str:
        """How to refer to the program that will convert ``path`` in a user-facing note."""
        provider = self.source_provider(path, audio_only=audio_only)
        if isinstance(provider, SystemCodecs):
            return provider.label
        if provider is not None:
            return "your FFmpeg"
        return ""


def as_bridge(value: MediaBridge | Provider | None) -> MediaBridge | None:
    if value is None or isinstance(value, MediaBridge):
        return value
    if isinstance(value, SystemCodecs):
        return MediaBridge(system=value)
    return MediaBridge(user=value)


_implicit: dict[str, ExternalFFmpeg | None] = {}
_implicit_lock = threading.Lock()


def implicit_user_ffmpeg(*, refresh: bool = False) -> ExternalFFmpeg | None:
    """An FFmpeg found on this computer without the user selecting it (cached)."""
    with _implicit_lock:
        if not refresh and "ffmpeg" in _implicit:
            return _implicit["ffmpeg"]
        found = None
        for candidate in user_ffmpeg.discover_candidates():
            try:
                found = user_ffmpeg.load_external_ffmpeg(candidate)
            except ExternalFFmpegError:
                continue
            if found is not None:
                break
        _implicit["ffmpeg"] = found
        return found


def load_media_bridge(user_path: str | None) -> MediaBridge | None:
    """The available converters: the system codecs, then the user's or a found FFmpeg."""
    user = user_ffmpeg.load_external_ffmpeg(user_path) or implicit_user_ffmpeg()
    system = detect_system_codecs()
    if user is None and system is None:
        return None
    return MediaBridge(system=system, user=user)


def media_capabilities() -> dict:
    """What the desktop app needs to know about the formats LocalSR's runtime omits."""
    system = detect_system_codecs()
    found = implicit_user_ffmpeg()
    return {
        "system_codecs": (
            {
                "label": system.label,
                "decode": ["h264", "hevc", "aac"],
                "encode": ["h264", "hevc", "aac"],
                "containers": ["mp4", "mov"],
            }
            if system is not None
            else None
        ),
        "external_ffmpeg": {
            "detected": [found.path] if found is not None else [],
            "candidates": user_ffmpeg.discover_candidates(),
        },
    }


def undecodable_message(bridge: MediaBridge | None) -> str:
    """Why a video cannot be opened, naming the codecs that were tried."""
    if bridge is not None and bridge.system is not None:
        return (
            f"This video uses a format that neither LocalSR nor {bridge.system.label} can decode "
            "(for example WMV, DivX, FLV or H.263). " + EXTERNAL_FFMPEG_HINT
        )
    return UNDECODABLE_VIDEO_MESSAGE


def system_codecs_available() -> bool:
    return detect_system_codecs() is not None


def validate_export(
    ffmpeg: MediaBridge | Provider | None, video_codec: str, container: str, *, hdr: bool = False
) -> None:
    """Fail before inference when nothing can write an H.264/HEVC export."""
    if video_codec not in EXTERNAL_OUTPUT_CODECS:
        return
    bridge = as_bridge(ffmpeg)
    if bridge is None:
        raise ValueError(
            f"{video_codec.upper()} export uses the FFmpeg installed on this computer, "
            "which is not selected. " + EXTERNAL_FFMPEG_HINT
        )
    try:
        bridge.export_provider(video_codec, hdr=hdr, container=normalize_container(container))
    except ExternalFFmpegError as error:
        raise ValueError(str(error)) from error


def platform_label() -> str:
    return {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")


@contextmanager
def decodable_source(
    path: str,
    *,
    ffmpeg: MediaBridge | Provider | None,
    temporary_directory: str | None = None,
    cancel_event: threading.Event | None = None,
    progress: Callable[[int], None] | None = None,
    output_container: str = "mkv",
    on_convert: Callable[[], None] | None = None,
) -> Iterator[tuple[str, str]]:
    """Yield ``(frame_source, audio_source)`` paths the bundled runtime can read.

    The original file is used whenever possible. Otherwise the system codecs or
    the user's FFmpeg write a lossless intermediate that is deleted afterwards.
    """
    bridge = as_bridge(ffmpeg)
    plan = plan_source(path, output_container)
    if plan.video_decodable and not plan.audio_needs_conversion:
        yield path, path
        return
    provider = (
        bridge.source_provider(path, audio_only=plan.video_decodable)
        if bridge is not None
        else None
    )
    if provider is None:
        if not plan.video_decodable:
            raise UndecodableVideoError(undecodable_message(bridge))
        # Keep the enhanced video; the remux step reports the omitted track.
        yield path, path
        return
    if isinstance(provider, ExternalFFmpeg):
        with user_ffmpeg.decodable_source(
            path,
            ffmpeg=provider,
            temporary_directory=temporary_directory,
            cancel_event=cancel_event,
            progress=progress,
            output_container=output_container,
            on_convert=on_convert,
        ) as sources:
            yield sources
        return
    if on_convert is not None:
        on_convert()
    directory = tempfile.mkdtemp(prefix="localsr-media-", dir=temporary_directory)
    try:
        intermediate = Path(directory) / "source.mkv"
        if not plan.video_decodable:
            ensure_free_space(directory, 2_000_000_000, "a lossless copy of the source video")
        provider.convert_source(
            path,
            intermediate,
            video=not plan.video_decodable,
            cancel_event=cancel_event,
            progress=progress,
        )
        if plan.video_decodable:
            yield path, str(intermediate)
        else:
            yield str(intermediate), str(intermediate)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@contextmanager
def first_frame_source(
    path: str, *, ffmpeg: MediaBridge | Provider, temporary_directory: str | None = None
) -> Iterator[str]:
    """A one-frame lossless intermediate for probing a video LocalSR cannot decode."""
    bridge = as_bridge(ffmpeg)
    provider = bridge.source_provider(path) if bridge is not None else None
    if provider is None:
        raise UndecodableVideoError(undecodable_message(bridge))
    if isinstance(provider, ExternalFFmpeg):
        with user_ffmpeg.first_frame_source(
            path, ffmpeg=provider, temporary_directory=temporary_directory
        ) as frame_path:
            yield frame_path
        return
    directory = tempfile.mkdtemp(prefix="localsr-probe-", dir=temporary_directory)
    try:
        intermediate = Path(directory) / "frame.mkv"
        provider.convert_source(path, intermediate, frames=1)
        yield str(intermediate)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def external_timing(ffmpeg: MediaBridge | Provider, path: str) -> tuple[float, float]:
    """``(fps, duration_seconds)`` of a file LocalSR's runtime cannot read."""
    bridge = as_bridge(ffmpeg)
    provider = bridge.source_provider(path) if bridge is not None else None
    if provider is None:
        return 0.0, 0.0
    if isinstance(provider, ExternalFFmpeg):
        return user_ffmpeg.external_timing(provider, path)
    return provider.timing(path)


def transcode_output(
    ffmpeg: MediaBridge | Provider,
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
    """Encode a LocalSR FFV1 master into H.264/HEVC with whichever program can."""
    bridge = as_bridge(ffmpeg)
    if bridge is None:
        raise ExternalFFmpegError(EXTERNAL_FFMPEG_HINT)
    provider = bridge.export_provider(video_codec, hdr=bool(hdr_format), container=container)
    if isinstance(provider, ExternalFFmpeg):
        user_ffmpeg.transcode_output(
            provider,
            source,
            destination,
            video_codec=video_codec,
            container=container,
            crf=crf,
            hdr_format=hdr_format,
            width=width,
            height=height,
            fps=fps,
            cancel_event=cancel_event,
            progress=progress,
        )
        return
    provider.transcode_output(
        source,
        destination,
        video_codec=video_codec,
        container=container,
        crf=crf,
        hdr_format=hdr_format,
        width=width,
        height=height,
        fps=fps,
        cancel_event=cancel_event,
        progress=progress,
    )


__all__ = [
    "MediaBridge",
    "Provider",
    "as_bridge",
    "decodable_source",
    "external_timing",
    "first_frame_source",
    "implicit_user_ffmpeg",
    "load_media_bridge",
    "media_capabilities",
    "platform_label",
    "system_codecs_available",
    "transcode_output",
    "undecodable_message",
    "validate_export",
]
