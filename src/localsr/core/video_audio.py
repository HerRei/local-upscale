"""Streaming AAC conversion for audio that cannot travel in a playback MP4."""

from fractions import Fraction
from math import ceil

import av


def needs_aac(stream, container_format: str) -> bool:
    return (
        stream.type == "audio"
        and stream.codec_context is not None
        and container_format == "mp4"
        and stream.codec_context.name not in {"aac", "mp3", "mp3float"}
    )


def aac_packets(source, stream_index, output, start_seconds, end_seconds, cancel_event=None):
    """Trim decoded samples, preserving their source times, and encode one track.

    Each iterator owns its decoder. The muxer holds one packet per track, so a
    long recording does not require buffering its soundtrack in memory.
    """
    rate = 48000
    start = Fraction(str(start_seconds))
    end = Fraction(str(end_seconds)) if end_seconds != float("inf") else None
    with av.open(str(source)) as container:
        stream = container.streams[stream_index]
        resampler = av.AudioResampler(format="fltp", layout=output.layout.name, rate=rate)
        next_input_time = None

        def encode(frame):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("Audio has no presentation timestamps; repair the source first.")
            timestamp = frame.pts * frame.time_base
            left = max(0, ceil((start - timestamp) * rate))
            right = min(frame.samples, ceil((end - timestamp) * rate)) if end else frame.samples
            if right <= left:
                return
            samples = frame.to_ndarray()[:, left:right].copy()
            trimmed = av.AudioFrame.from_ndarray(samples, format="fltp", layout=output.layout.name)
            trimmed.sample_rate = rate
            trimmed.time_base = Fraction(1, rate)
            trimmed.pts = round((timestamp - start) * rate) + left
            yield from output.encode(trimmed)

        for frame in container.decode(stream):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            # WMA decoders can flush a final frame without PTS. Continue the
            # established sample clock; never invent the initial audio offset.
            if frame.pts is None and next_input_time is not None:
                frame.time_base = Fraction(1, frame.sample_rate)
                frame.pts = round(next_input_time * frame.sample_rate)
            if frame.pts is not None and frame.time_base is not None:
                next_input_time = frame.pts * frame.time_base + Fraction(
                    frame.samples, frame.sample_rate
                )
            if end is not None and frame.pts is not None and frame.time_base is not None:
                if frame.pts * frame.time_base >= end:
                    break
            for converted in resampler.resample(frame):
                yield from encode(converted)
        for converted in resampler.resample(None):
            yield from encode(converted)
        yield from output.encode(None)
