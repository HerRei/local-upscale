"""Normalize the display geometry and interlaced fields of older recordings."""

from collections.abc import Iterable
from dataclasses import dataclass

import av
import numpy as np

# codec_context.field_order (AV_FIELD_PROGRESSIVE, TT, BB, TB, BT), named by the field
# FFmpeg documents as shown first.
_STREAM_FIELD_ORDERS = {1: "progressive", 2: "tff", 3: "bff", 4: "bff", 5: "tff"}


def square_pixel_width(stream, width):
    aspect = getattr(stream, "sample_aspect_ratio", None)
    if not aspect or aspect == 1:
        return width
    display_width = round(width * aspect)
    if display_width < 1 or display_width > width * 8:
        raise ValueError("Video has an invalid or unsupported pixel aspect ratio.")
    return display_width


def stream_field_order(stream) -> str:
    """What the stream header says: ``progressive``, ``tff``, ``bff`` or ``unknown``."""
    return _STREAM_FIELD_ORDERS.get(int(getattr(stream.codec_context, "field_order", 0)), "unknown")


def _pull_all(graph):
    while True:
        try:
            yield graph.pull()
        except (av.error.BlockingIOError, av.error.EOFError):
            return


def _single_filter_graph(template: av.VideoFrame, name: str, options: str):
    graph = av.filter.Graph()
    # For two pictures, starting a thread pool costs more than it saves.
    graph.threads = 1
    source = graph.add_buffer(template=template)
    step = graph.add(name, options)
    sink = graph.add("buffersink")
    source.link_to(step)
    step.link_to(sink)
    graph.configure()
    return graph


def flagged_field_order(frame: av.VideoFrame) -> str:
    """``tff`` or ``bff`` for a frame flagged interlaced, ``""`` for one that is not.

    PyAV shows the interlaced flag but not which field comes first, and the bundled FFmpeg
    has no filter that reports it. bwdif reads it, so two grey copies that keep the flags
    are split: black, then white on the even rows only. The first half-picture of the
    second copy keeps either its white even rows (top first) or its black odd rows (bottom
    first). One copy would not tell: with nothing moving, bwdif weaves both fields back.
    """
    if not frame.interlaced_frame:
        return ""
    copies = _single_filter_graph(frame, "format", "pix_fmts=gray")
    pictures = []
    for pts, even_rows in enumerate((0, 255)):
        copies.push(frame)
        picture = copies.pull()
        picture.make_writable()
        plane = picture.planes[0]
        rows = np.zeros((picture.height, plane.line_size), np.uint8)
        rows[0::2] = even_rows
        plane.update(rows.tobytes())
        picture.pts = pts
        pictures.append(picture)
    split = _single_filter_graph(pictures[0], "bwdif", "mode=send_field:parity=auto:deint=all")
    fields = []
    for picture in (*pictures, None):
        split.push(picture)
        fields.extend(_pull_all(split))
    return "tff" if fields[2].to_ndarray(format="gray")[0::2].min() == 255 else "bff"


@dataclass(frozen=True)
class Deinterlace:
    """A job's deinterlace step, run on the decoder's own frames, where the field flags are.

    ``field`` mode gives every field its own frame at twice the rate. Parity ``auto`` splits
    a flagged frame in its own order and an unflagged stream in the order its header gives.
    """

    mode: str = "frame"
    parity: str = "auto"

    @property
    def frames_per_source_frame(self) -> int:
        return 2 if self.mode == "field" else 1

    def bwdif_options(self, stream, first: av.VideoFrame) -> str:
        send = "send_field" if self.mode == "field" else "send_frame"
        parity, deint = self.parity, "all"
        if parity == "auto" and first.interlaced_frame:
            # bwdif follows each frame's own flag. In field mode an unflagged frame must
            # still give two frames, or the doubled rate loses time.
            if self.mode == "frame":
                deint = "interlaced"
        elif parity == "auto":
            header = stream_field_order(stream)
            parity = header if header in {"tff", "bff"} else "auto"
        return f"mode={send}:parity={parity}:deint={deint}"


def display_frames(
    container,
    stream,
    cancel_event=None,
    deinterlace: Deinterlace | None = None,
    decoded: Iterable[av.VideoFrame] | None = None,
):
    """Deinterlace flagged frames at the original frame rate with bounded delay.

    Progressive sources bypass the filter. BWDIF emits one progressive frame
    per source frame so trim indices and source timestamps remain meaningful.
    A job's ``deinterlace`` step replaces that filter and runs from the first
    frame; in field mode it emits two frames per source frame. ``decoded``
    stands in for the container's frames when the caller has read some already.
    """
    graph = None

    for frame in container.decode(stream) if decoded is None else decoded:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("video job cancelled")
        if graph is None and (deinterlace is not None or frame.interlaced_frame):
            options = (
                deinterlace.bwdif_options(stream, frame)
                if deinterlace is not None
                else "mode=send_frame:parity=auto:deint=interlaced"
            )
            graph = av.filter.Graph()
            graph.threads = 2
            source = graph.add_buffer(template=frame)
            bwdif = graph.add("bwdif", options)
            sink = graph.add("buffersink")
            source.link_to(bwdif)
            bwdif.link_to(sink)
            graph.configure()
        if graph is None:
            yield frame
        else:
            graph.push(frame)
            yield from _pull_all(graph)
    if graph is not None:
        graph.push(None)
        yield from _pull_all(graph)
