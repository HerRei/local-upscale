"""Licensing-aware codec selection for video export and import.

LocalSR distributes only royalty-free or patent-expired media formats, built
from LGPL-2.1-or-later and permissively licensed code (see
``packaging/ffmpeg/codec-policy.json``). Patent-licensed formats such as
H.264, HEVC and AAC are never implemented by LocalSR's own binaries. A user can
opt into an FFmpeg executable they installed themselves; that separate program
then performs those conversions (see ``external_ffmpeg``).
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import av

BUNDLED_OUTPUT_CODECS = ("av1", "vp9", "ffv1")
EXTERNAL_OUTPUT_CODECS = ("h264", "hevc")
OUTPUT_CODECS = BUNDLED_OUTPUT_CODECS + EXTERNAL_OUTPUT_CODECS
OUTPUT_CONTAINERS = ("mp4", "mkv")

# Container names as PyAV/FFmpeg muxers know them.
MUXER_FORMATS = {"mp4": "mp4", "mkv": "matroska", "webm": "webm"}

# Audio that is carried unchanged (demux/mux only, never decoded or encoded).
MP4_COPY_AUDIO = frozenset({"aac", "mp3", "mp3float", "opus", "flac", "alac", "ac3"})
WEBM_COPY_AUDIO = frozenset({"opus", "vorbis"})

EXTERNAL_FFMPEG_HINT = (
    "Install FFmpeg on this computer — LocalSR then uses it automatically — or convert "
    "the video to AV1, VP9 or FFV1 first."
)
UNDECODABLE_VIDEO_MESSAGE = (
    "This video uses a patent-licensed format (for example H.264, HEVC, WMV or MPEG-4 "
    "Part 2) that LocalSR does not include. " + EXTERNAL_FFMPEG_HINT
)
# Sent with a failed media probe so the desktop app can show its FFmpeg notice
# instead of a bare error. Stable protocol value; do not rename.
EXTERNAL_FFMPEG_REQUIRED = "external_ffmpeg_required"


class UndecodableVideoError(ValueError):
    """The source uses a format LocalSR omits; a user-installed FFmpeg can open it."""

    reason = EXTERNAL_FFMPEG_REQUIRED

    def __init__(self, message: str = UNDECODABLE_VIDEO_MESSAGE) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class VideoEncodeSpec:
    """How the bundled encoder writes one video stream."""

    output_codec: str
    encoder: str
    pixel_format: str
    options: dict[str, str] = field(default_factory=dict)


def normalize_container(container: str) -> str:
    """Accept FFmpeg's ``matroska`` muxer name for LocalSR's ``mkv`` container."""
    return "mkv" if container == "matroska" else container


def muxer_format(container: str) -> str:
    try:
        return MUXER_FORMATS[normalize_container(container)]
    except KeyError as error:
        raise ValueError(f"Unsupported video container: {container}") from error


def validate_output(video_codec: str, container: str, *, hdr: bool = False) -> None:
    """Reject codec/container combinations before any inference starts."""
    container = normalize_container(container)
    if video_codec not in OUTPUT_CODECS:
        raise ValueError(
            f"Unsupported video codec {video_codec!r}; choose AV1, VP9, FFV1, H.264 or HEVC."
        )
    if container not in (*OUTPUT_CONTAINERS, "webm"):
        raise ValueError(f"Unsupported video container: {container}")
    if video_codec == "ffv1" and container != "mkv":
        raise ValueError("Lossless FFV1 video requires the MKV container.")
    if container == "webm" and video_codec not in {"av1", "vp9"}:
        raise ValueError("WebM holds only AV1 or VP9 video.")
    if hdr and video_codec == "h264":
        raise ValueError(
            "H.264 cannot preserve HDR. Choose AV1, VP9, FFV1 or HEVC for HDR preservation."
        )


def encoder_available(name: str) -> bool:
    try:
        av.Codec(name, "w")
    except (av.FFmpegError, ValueError):
        return False
    return True


def _svt_crf(crf: int) -> int:
    """Map LocalSR's historical 0-51 quality scale onto SVT-AV1's 0-63 scale."""
    return max(1, min(63, round(int(crf) * 1.2 + 8)))


def _vpx_crf(crf: int) -> int:
    return max(0, min(63, round(int(crf) * 1.25 + 8)))


def encode_spec(
    video_codec: str,
    *,
    crf: int = 18,
    hdr: bool = False,
    threads: int = 0,
    fast: bool = False,
) -> VideoEncodeSpec:
    """Return the bundled encoder settings for an AV1, VP9 or FFV1 stream.

    ``fast`` selects realtime-oriented settings for disposable playback copies.
    """
    pixel_format = "yuv420p10le" if hdr else "yuv420p"
    if video_codec == "av1":
        # SVT-AV1 otherwise prints its configuration banner for every encode.
        os.environ.setdefault("SVT_LOG", "1")
        preset = "10" if fast else "8"
        options = {"crf": str(_svt_crf(crf)), "preset": preset}
        if threads > 0:
            options["svtav1-params"] = f"lp={max(1, min(8, int(threads)))}"
        return VideoEncodeSpec("av1", "libsvtav1", pixel_format, options)
    if video_codec == "vp9":
        options = {
            "crf": str(_vpx_crf(crf)),
            "b": "0",
            "row-mt": "1",
            "deadline": "realtime" if fast else "good",
            "cpu-used": "8" if fast else "4",
        }
        if hdr:
            options["profile"] = "2"
        return VideoEncodeSpec("vp9", "libvpx-vp9", pixel_format, options)
    if video_codec == "ffv1":
        return VideoEncodeSpec(
            "ffv1", "ffv1", pixel_format, {"level": "3", "slicecrc": "1", "g": "1"}
        )
    raise ValueError(f"{video_codec} is not encoded by LocalSR's bundled media runtime.")


def require_bundled_encoder(spec: VideoEncodeSpec) -> None:
    if not encoder_available(spec.encoder):
        raise ValueError(
            f"This LocalSR build cannot encode {spec.output_codec.upper()} "
            f"({spec.encoder} is unavailable)."
        )
    if spec.pixel_format.endswith("10le"):
        formats = {fmt.name for fmt in av.Codec(spec.encoder, "w").video_formats or ()}
        if spec.pixel_format not in formats:
            raise ValueError(
                f"This LocalSR build cannot encode 10-bit {spec.output_codec.upper()} for HDR."
            )


def video_stream_decodable(stream) -> bool:
    return stream is not None and stream.codec_context is not None


def audio_copy_allowed(codec_name: str | None, container: str) -> bool:
    """Whether a decodable audio stream may travel unchanged into ``container``."""
    container = normalize_container(container)
    if container == "mkv":
        return True
    if container == "webm":
        return codec_name in WEBM_COPY_AUDIO
    return codec_name in MP4_COPY_AUDIO


def stream_copy_supported(stream, container: str) -> bool:
    """Probe whether ``container``'s muxer accepts ``stream`` without decoding it.

    Streams whose decoder is not bundled have no codec context. PyAV cannot copy
    such audio/video streams at all, so they need a user-installed FFmpeg. For
    decodable streams a tiny in-memory header write asks the muxer directly.
    """
    if stream.codec_context is None:
        return False
    buffer = io.BytesIO()
    try:
        with av.open(buffer, mode="w", format=muxer_format(container)) as output:
            output.add_stream_from_template(stream)
            output.start_encoding()
    except (av.FFmpegError, ValueError, OSError, NotImplementedError):
        return False
    return True
