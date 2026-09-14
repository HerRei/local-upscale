"""Normalize the display geometry and interlaced fields of older recordings."""

import av


def square_pixel_width(stream, width):
    aspect = getattr(stream, "sample_aspect_ratio", None)
    if not aspect or aspect == 1:
        return width
    display_width = round(width * aspect)
    if display_width < 1 or display_width > width * 8:
        raise ValueError("Video has an invalid or unsupported pixel aspect ratio.")
    return display_width


def display_frames(container, stream, cancel_event=None):
    """Deinterlace flagged frames at the original frame rate with bounded delay.

    Progressive sources bypass the filter. BWDIF emits one progressive frame
    per source frame so trim indices and source timestamps remain meaningful.
    """
    graph = None

    def drain():
        while True:
            try:
                yield graph.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                return

    for frame in container.decode(stream):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("video job cancelled")
        if graph is None and frame.interlaced_frame:
            graph = av.filter.Graph()
            graph.threads = 2
            source = graph.add_buffer(template=frame)
            deinterlace = graph.add("bwdif", "mode=send_frame:parity=auto:deint=interlaced")
            sink = graph.add("buffersink")
            source.link_to(deinterlace)
            deinterlace.link_to(sink)
            graph.configure()
        if graph is None:
            yield frame
        else:
            graph.push(frame)
            yield from drain()
    if graph is not None:
        graph.push(None)
        yield from drain()
