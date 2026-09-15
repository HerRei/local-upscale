"""Create a disposable, SDR VP9/WebM playback copy without running an AI model.

VP9 is royalty-free and plays in every supported desktop web view. Sources in
formats LocalSR does not include are read through the system codecs or the
user's selected FFmpeg.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .media_bridge import decodable_source, load_media_bridge
from .video_io import decode_timed_frames, encode_video, probe_video


def prepare_playback(
    source, destination, *, progress=None, cancel_event=None, external_ffmpeg=None
):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Playback conversion must not replace the source.")
    report = progress or (lambda _data: None)
    with decodable_source(
        str(source),
        ffmpeg=external_ffmpeg,
        temporary_directory=str(destination.parent),
        cancel_event=cancel_event,
        output_container="webm",
        on_convert=lambda: report(
            {"stage": "Converting the source video", "frame": 0, "total": 0, "elapsed_seconds": 0}
        ),
    ) as (frame_source, audio_source):
        _prepare_playback(
            Path(frame_source),
            Path(audio_source),
            destination,
            report=report,
            cancel_event=cancel_event,
        )


def _prepare_playback(source, audio_source, destination, *, report, cancel_event):
    probe = probe_video(str(source))
    if probe.width * probe.height > 80_000_000:
        raise ValueError("This video's dimensions exceed the playback conversion limit.")
    ratio = min(1, 1280 / max(probe.width, probe.height))
    width, height = (
        max(2, int(dimension * ratio) // 2 * 2) for dimension in (probe.width, probe.height)
    )
    started = time.monotonic()
    last_report = 0

    def frames():
        nonlocal last_report
        for frame in decode_timed_frames(
            str(source), cancel_event=cancel_event, hdr_mode="tone_map", decoder_threads=2
        ):
            rgb = np.asarray(
                Image.fromarray(frame.rgb).resize((width, height), Image.Resampling.LANCZOS)
            )
            now = time.monotonic()
            if now - last_report >= 0.25:
                report(
                    {
                        "stage": "Converting video for playback",
                        "frame": frame.index + 1,
                        "total": probe.frame_count,
                        "elapsed_seconds": now - started,
                    }
                )
                last_report = now
            yield frame.with_pixels(rgb)
        report(
            {
                "stage": "Preparing audio",
                "frame": probe.frame_count,
                "total": probe.frame_count,
                "elapsed_seconds": time.monotonic() - started,
            }
        )

    encode_video(
        frames(),
        str(destination),
        fps=probe.fps,
        width=width,
        height=height,
        container_format="webm",
        video_codec="vp9",
        fast=True,
        audio_source=str(audio_source),
        cancel_event=cancel_event,
        crf=23,
        warning_callback=lambda _message: None,
        sdr_bt709=bool(probe.hdr_format),
        encoder_threads=2,
    )
    report(
        {
            "stage": "Playback copy ready",
            "frame": probe.frame_count,
            "total": probe.frame_count,
            "elapsed_seconds": time.monotonic() - started,
        }
    )


def main(arguments):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    options = parser.parse_args(arguments)
    try:
        prepare_playback(
            options.source,
            options.destination,
            progress=lambda value: print(json.dumps(value), flush=True),
            external_ffmpeg=load_media_bridge(None),
        )
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0
