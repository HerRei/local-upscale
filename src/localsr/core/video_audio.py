"""Streaming Opus conversion for audio that cannot travel unchanged.

Compatible audio is always copied without decoding. Otherwise a decodable
track is converted to Opus, a royalty-free codec. LocalSR does not encode AAC.
"""

from fractions import Fraction
from math import ceil

import av

from .media_codecs import audio_copy_allowed

OPUS_RATE = 48000


def needs_audio_transcode(stream, container_format: str) -> bool:
    """A decodable track whose codec the output container cannot carry unchanged."""
    return (
        stream.type == "audio"
        and stream.codec_context is not None
        and not audio_copy_allowed(stream.codec_context.name, container_format)
    )


def add_opus_stream(output_container, source_stream):
    converted = output_container.add_stream("libopus", rate=OPUS_RATE)
    channels = source_stream.codec_context.channels
    converted.layout = {1: "mono", 2: "stereo"}.get(
        channels, source_stream.codec_context.layout.name
    )
    converted.bit_rate = 96000 * max(1, min(channels, 8)) if channels > 2 else 160000
    converted.metadata.update(source_stream.metadata)
    return converted


def opus_packets(source, stream_index, output, start_seconds, end_seconds, cancel_event=None):
    """Trim decoded samples, preserving their source times, and encode one track.

    Each iterator owns its decoder. The muxer holds one packet per track, so a
    long recording does not require buffering its soundtrack in memory.
    """
    rate = OPUS_RATE
    start = Fraction(str(start_seconds))
    end = Fraction(str(end_seconds)) if end_seconds != float("inf") else None
    layout = output.layout.name
    with av.open(str(source)) as container:
        stream = container.streams[stream_index]
        # libopus accepts interleaved samples; PyAV rechunks frames to 20 ms.
        resampler = av.AudioResampler(format="flt", layout=layout, rate=rate)
        next_input_time = None

        def encode(frame):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("Audio has no presentation timestamps; repair the source first.")
            timestamp = frame.pts * frame.time_base
            left = max(0, ceil((start - timestamp) * rate))
            right = min(frame.samples, ceil((end - timestamp) * rate)) if end else frame.samples
            if right <= left:
                return
            channels = len(frame.layout.channels)
            samples = frame.to_ndarray()[:, left * channels : right * channels].copy()
            trimmed = av.AudioFrame.from_ndarray(samples, format="flt", layout=layout)
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
