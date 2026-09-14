"""Create a disposable, SDR H.264 playback copy without running an AI model."""

import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .video_io import decode_timed_frames, encode_video, probe_video


def prepare_playback(source, destination, *, progress=None, cancel_event=None):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Playback conversion must not replace the source.")
    probe = probe_video(str(source))
    if probe.width * probe.height > 80_000_000:
        raise ValueError("This video's dimensions exceed the playback conversion limit.")
    ratio = min(1, 1280 / max(probe.width, probe.height))
    width, height = (
        max(2, int(dimension * ratio) // 2 * 2) for dimension in (probe.width, probe.height)
    )
    report = progress or (lambda _data: None)
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
        audio_source=str(source),
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
        )
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0
