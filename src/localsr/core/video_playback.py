"""Create a disposable, SDR VP9/WebM playback copy without running an AI model.

VP9 is royalty-free and plays in every supported desktop web view. Sources in
formats LocalSR does not include are read through the system codecs or the
user's selected FFmpeg.
"""

import json
import os
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

from .media_bridge import decodable_source, load_media_bridge
from .video_audio import add_opus_stream, opus_packets
from .video_io import (
    TimedVideoFrame,
    _audio_stream_usable,
    decode_timed_frames,
    encode_video,
    probe_video,
)

PLAYBACK_MAX_DIMENSION = 1280
COMPARE_GUTTER = 4
COMPARE_TIME_BASE = Fraction(1, 1000)


@dataclass(frozen=True)
class CompareSource:
    path: Path
    kept_frames: int | None = None
    audio_path: Path | None = None


@dataclass(frozen=True)
class CompareSlotRect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class CompareLayout:
    width: int
    height: int
    slots: tuple[CompareSlotRect, ...]


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
        # The disposable playback copy is app-owned; a retry may overwrite it.
        replace_existing=True,
    )
    report(
        {
            "stage": "Playback copy ready",
            "frame": probe.frame_count,
            "total": probe.frame_count,
            "elapsed_seconds": time.monotonic() - started,
        }
    )


def _slot_playback_size(
    width: int, height: int, max_dimension: int = PLAYBACK_MAX_DIMENSION
) -> tuple[int, int]:
    ratio = min(1, max_dimension / max(width, height))
    return (
        max(2, int(width * ratio) // 2 * 2),
        max(2, int(height * ratio) // 2 * 2),
    )


def plan_compare_layout(
    sizes: Sequence[tuple[int, int]],
    *,
    max_dimension: int = PLAYBACK_MAX_DIMENSION,
    gutter: int = COMPARE_GUTTER,
) -> CompareLayout:
    if not 1 <= len(sizes) <= 4:
        raise ValueError("Compare playback requires 1 to 4 video sources.")
    if gutter < 0 or gutter % 2:
        raise ValueError("Compare gutter must be a non-negative even integer.")
    scaled = [_slot_playback_size(w, h, max_dimension) for w, h in sizes]
    count = len(scaled)
    if count == 1:
        cols, rows = 1, 1
    elif count == 2:
        portrait = all(h > w for w, h in scaled)
        cols, rows = (2, 1) if portrait else (1, 2)
    else:
        cols, rows = 2, 2
    col_widths = [
        max(scaled[r * cols + c][0] for r in range(rows) if r * cols + c < count)
        for c in range(cols)
    ]
    row_heights = [
        max(scaled[r * cols + c][1] for c in range(cols) if r * cols + c < count)
        for r in range(rows)
    ]
    rects = []
    for index, (w, h) in enumerate(scaled):
        r, c = divmod(index, cols)
        x = sum(col_widths[:c]) + gutter * c
        y = sum(row_heights[:r]) + gutter * r
        rects.append(CompareSlotRect(x=x, y=y, width=w, height=h))
    total_w = sum(col_widths) + gutter * (cols - 1)
    total_h = sum(row_heights) + gutter * (rows - 1)
    return CompareLayout(width=total_w, height=total_h, slots=tuple(rects))


def pair_compare_frames(
    streams: Sequence[Iterator[TimedVideoFrame]],
    *,
    kept_frames: Sequence[int | None] | None = None,
    time_base: Fraction = COMPARE_TIME_BASE,
) -> Iterator[tuple[Fraction, Fraction, tuple[TimedVideoFrame, ...]]]:
    """Yield combined-frame intervals at every millisecond tick where any side changes."""
    if not streams:
        return
    limits = list(kept_frames) if kept_frames is not None else [None] * len(streams)
    iters = [iter(stream) for stream in streams]
    cur: list[TimedVideoFrame] = []
    origins: list[Fraction] = []
    counts: list[int] = []
    limit_tick: int | None = None

    def note_kept_end(index: int, last_frame: TimedVideoFrame) -> None:
        nonlocal limit_tick
        end_t = (last_frame.timestamp - origins[index]) + last_frame.duration
        ticks = max(1, round(end_t / time_base))
        limit_tick = ticks if limit_tick is None else min(limit_tick, ticks)

    for index, iterator in enumerate(iters):
        first = next(iterator, None)
        if first is None:
            raise ValueError(f"Compare source {index + 1} contains no video frames.")
        cur.append(first)
        origins.append(first.timestamp)
        counts.append(1)

    def pull_next(index: int) -> TimedVideoFrame | None:
        kept = limits[index]
        if kept is not None and counts[index] >= kept:
            note_kept_end(index, cur[index])
            return None
        nxt_frame = next(iters[index], None)
        if nxt_frame is None:
            if kept is not None:
                note_kept_end(index, cur[index])
            return None
        counts[index] += 1
        return nxt_frame

    nxt = [pull_next(i) for i in range(len(iters))]
    cur_tick = 0

    while True:
        for i in range(len(iters)):
            while (
                nxt[i] is not None
                and round((nxt[i].timestamp - origins[i]) / time_base) <= cur_tick
            ):
                cur[i] = nxt[i]
                nxt[i] = pull_next(i)
        if limit_tick is not None and cur_tick >= limit_tick:
            break
        future_ticks = [
            round((nxt[i].timestamp - origins[i]) / time_base)
            for i in range(len(iters))
            if nxt[i] is not None
        ]
        next_tick = min(future_ticks) if future_ticks else None
        if limit_tick is not None and (next_tick is None or next_tick >= limit_tick):
            yield (cur_tick * time_base, (limit_tick - cur_tick) * time_base, tuple(cur))
            break
        if next_tick is None:
            end_tick = max(
                cur_tick + 1,
                *(
                    round(((cur[i].timestamp - origins[i]) + cur[i].duration) / time_base)
                    for i in range(len(iters))
                ),
            )
            yield (cur_tick * time_base, (end_tick - cur_tick) * time_base, tuple(cur))
            break
        yield (cur_tick * time_base, (next_tick - cur_tick) * time_base, tuple(cur))
        cur_tick = next_tick


def _paint_slot(
    canvas: np.ndarray, rgb: np.ndarray, rect: CompareSlotRect, pad: int = COMPARE_GUTTER // 2
) -> None:
    x, y, w, h = rect.x, rect.y, rect.width, rect.height
    canvas[y : y + h, x : x + w] = rgb
    if pad <= 0:
        return
    # Replicate edge pixels into the half-gutter so GPU bilinear scaling never bleeds.
    total_h, total_w = canvas.shape[:2]
    right_end = min(total_w, x + w + pad)
    if right_end > x + w:
        canvas[y : y + h, x + w : right_end] = rgb[:, -1:]
    left_start = max(0, x - pad)
    if left_start < x:
        canvas[y : y + h, left_start:x] = rgb[:, :1]
    bottom_end = min(total_h, y + h + pad)
    if bottom_end > y + h:
        canvas[y + h : bottom_end, x : x + w] = rgb[-1:, :]
    top_start = max(0, y - pad)
    if top_start < y:
        canvas[top_start:y, x : x + w] = rgb[:1, :]


def _extract_audio_webm(
    source: Path,
    destination: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    cancel_event=None,
) -> bool:
    with av.open(str(source), options={"protocol_whitelist": "file"}) as container:
        audio_stream = next(
            (s for s in container.streams if s.type == "audio" and _audio_stream_usable(s, "webm")),
            None,
        )
        if audio_stream is None:
            return False
        stream_index = audio_stream.index
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    wrote_packets = False
    try:
        with av.open(str(temporary), mode="w", format="webm") as output:
            with av.open(str(source), options={"protocol_whitelist": "file"}) as probe_container:
                out_stream = add_opus_stream(output, probe_container.streams[stream_index])
            for packet in opus_packets(
                source, stream_index, out_stream, start_seconds, end_seconds, cancel_event
            ):
                output.mux(packet)
                wrote_packets = True
        if not wrote_packets:
            return False
        os.replace(temporary, destination)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def prepare_compare_playback(
    sources: Sequence[CompareSource],
    destination: str | Path,
    *,
    progress=None,
    cancel_event=None,
    external_ffmpeg=None,
) -> dict:
    if not 1 <= len(sources) <= 4:
        raise ValueError("Compare playback requires 1 to 4 video sources.")
    destination = Path(destination)
    for item in sources:
        if Path(item.path).resolve() == destination.resolve():
            raise ValueError("Playback conversion must not replace a source.")
    report = progress or (lambda _data: None)
    started = time.monotonic()

    with ExitStack() as stack:
        opened: list[tuple[Path, Path]] = []
        for item in sources:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            frame_src, audio_src = stack.enter_context(
                decodable_source(
                    str(item.path),
                    ffmpeg=external_ffmpeg,
                    temporary_directory=str(destination.parent),
                    cancel_event=cancel_event,
                    output_container="webm",
                    on_convert=lambda: report(
                        {
                            "stage": "Converting the source video",
                            "frame": 0,
                            "total": 0,
                            "elapsed_seconds": time.monotonic() - started,
                        }
                    ),
                )
            )
            opened.append((Path(frame_src), Path(audio_src)))

        probes = [probe_video(str(frame_src)) for frame_src, _ in opened]
        for probe in probes:
            if probe.width * probe.height > 80_000_000:
                raise ValueError("This video's dimensions exceed the playback conversion limit.")
        layout = plan_compare_layout([(p.width, p.height) for p in probes])
        max_fps = max((p.fps for p in probes if p.fps > 0), default=30.0)
        kept_counts = [
            item.kept_frames if item.kept_frames and item.kept_frames > 0 else None
            for item in sources
        ]
        total_estimate = max(
            1,
            *(
                min(p.frame_count, k) if (p.frame_count and k) else (k or p.frame_count or 1)
                for p, k in zip(probes, kept_counts, strict=True)
            ),
        )
        has_limit = any(k is not None for k in kept_counts)
        origins: list[Fraction | None] = [None] * len(sources)
        total_duration = Fraction(0)
        last_report = 0.0

        def frames():
            nonlocal last_report, total_duration
            streams = [
                decode_timed_frames(
                    str(frame_src),
                    end_frame=(kept - 1) if kept is not None else None,
                    cancel_event=cancel_event,
                    hdr_mode="tone_map",
                    decoder_threads=2,
                )
                for (frame_src, _), kept in zip(opened, kept_counts, strict=True)
            ]
            canvas = np.zeros((layout.height, layout.width, 3), dtype=np.uint8)
            last_indices: list[int | None] = [None] * len(sources)
            out_index = 0
            for ts, dur, slot_frames in pair_compare_frames(streams, kept_frames=kept_counts):
                for idx, (slot_frame, rect) in enumerate(
                    zip(slot_frames, layout.slots, strict=True)
                ):
                    if origins[idx] is None:
                        origins[idx] = slot_frame.timestamp
                    if last_indices[idx] != slot_frame.index:
                        if (
                            slot_frame.rgb.shape[1] != rect.width
                            or slot_frame.rgb.shape[0] != rect.height
                        ):
                            resized = np.asarray(
                                Image.fromarray(slot_frame.rgb).resize(
                                    (rect.width, rect.height), Image.Resampling.LANCZOS
                                )
                            )
                        else:
                            resized = slot_frame.rgb
                        _paint_slot(canvas, resized, rect)
                        last_indices[idx] = slot_frame.index
                total_duration = ts + dur
                now = time.monotonic()
                if now - last_report >= 0.25:
                    report(
                        {
                            "stage": "Converting video for playback",
                            "frame": min(total_estimate, out_index + 1),
                            "total": total_estimate,
                            "elapsed_seconds": now - started,
                        }
                    )
                    last_report = now
                yield TimedVideoFrame(out_index, canvas.copy(), ts, dur, COMPARE_TIME_BASE)
                out_index += 1

        encode_video(
            frames(),
            str(destination),
            fps=max_fps,
            width=layout.width,
            height=layout.height,
            container_format="webm",
            video_codec="vp9",
            fast=True,
            audio_source=None,
            cancel_event=cancel_event,
            crf=23,
            warning_callback=lambda _message: None,
            sdr_bt709=any(bool(p.hdr_format) for p in probes),
            encoder_threads=2,
            replace_existing=True,
            declare_frame_rate=False,
        )

        report(
            {
                "stage": "Preparing audio",
                "frame": total_estimate,
                "total": total_estimate,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        end_limit = float(total_duration) if has_limit else float("inf")
        slots_manifest = []
        for idx, (item, (_, audio_src), probe, rect) in enumerate(
            zip(sources, opened, probes, layout.slots, strict=True)
        ):
            has_audio = False
            audio_file = None
            if item.audio_path is not None and probe.has_audio:
                origin_seconds = float(origins[idx] or 0)
                slot_end = origin_seconds + end_limit if end_limit != float("inf") else float("inf")
                has_audio = _extract_audio_webm(
                    audio_src,
                    Path(item.audio_path),
                    start_seconds=origin_seconds,
                    end_seconds=slot_end,
                    cancel_event=cancel_event,
                )
                if has_audio:
                    audio_file = Path(item.audio_path).name
            slots_manifest.append(
                {
                    "x": rect.x,
                    "y": rect.y,
                    "width": rect.width,
                    "height": rect.height,
                    "fps": probe.fps,
                    "duration": probe.duration_seconds,
                    "frames": probe.frame_count,
                    "has_audio": has_audio,
                    "audio_file": audio_file,
                }
            )
        report(
            {
                "stage": "Playback copy ready",
                "frame": total_estimate,
                "total": total_estimate,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        return {
            "width": layout.width,
            "height": layout.height,
            "duration": float(total_duration),
            "slots": slots_manifest,
        }


def compare_main(arguments):
    import argparse

    parser = argparse.ArgumentParser(
        description="Create a combined Compare VP9/WebM playback copy."
    )
    parser.add_argument("request_json")
    options = parser.parse_args(arguments)
    try:
        request = json.loads(Path(options.request_json).read_text(encoding="utf-8"))
        sources = [
            CompareSource(
                path=Path(entry["path"]),
                kept_frames=entry.get("kept_frames"),
                audio_path=Path(entry["audio_path"]) if entry.get("audio_path") else None,
            )
            for entry in request["sources"]
        ]
        manifest = prepare_compare_playback(
            sources,
            request["output_video"],
            progress=lambda value: print(json.dumps(value), flush=True),
            external_ffmpeg=load_media_bridge(None),
        )
        manifest_path = Path(request["output_manifest"])
        tmp = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(manifest), encoding="utf-8")
        os.replace(tmp, manifest_path)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


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
