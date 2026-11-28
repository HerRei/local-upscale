"""Dust & scratches: fill the painted marks of a photo from the picture around them.

Every mark is filled with OpenCV's Telea method first, each colour channel on its own as 16-bit
integers (OpenCV's 32-bit float inpainting returns garbage in the painted places, and 65535
levels keep a 16-bit picture's depth). Telea does well on hairlines and small specks but smears
larger marks. When the repair stage names the MI-GAN inpainting model (catalog `INPAINT_MODEL`,
model id and path on the stage, sent by the host once the model is installed), the marks wider
than a hairline are filled once more by the model:

- The paint is split into marks (8-connected). A mark's width is twice the median distance from
  its centre line to its edge, so a crossing of two scratches still counts as a scratch.
  Marks up to `HAIRLINE_WIDTH` keep their Telea fill exactly.
- Wide marks closer than `MERGE_DISTANCE` form one group; a group larger than `CELL` is cut into
  cells. Each group or cell gets one window of `SMALL_WINDOW` or `LARGE_WINDOW` pixels around
  it, moved inside the picture, and the windows run one after another, so a later window sees
  the fills of the earlier ones.
- The model sees the 8-bit picture with the Telea fills of the hairlines and the window's
  unfilled wide marks as the hole, grown by `GROW` pixels because it leaves a thin ring at a
  hole's edge unfilled. Only the painted pixels are taken from its result.
- On a picture with more than 8 bits per channel the model's change is added to the 16-bit
  Telea fill: ``telea16 + (model8 - telea8) * 257``.

No pixel outside the paint changes. A stage without a model fills every mark with Telea alone,
exactly as before the model existed.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from localsr.core.model_catalog import INPAINT_MODEL, verify_file_checksum

# Telea reads this many pixels around each painted pixel.
TELEA_RADIUS = 3
# Twice the median distance from a mark's centre line to its edge, above which the model fills
# it. An 8 px stroke measures 8 to 8.25 and stays with Telea, a 9 px one measures 10; a 3 px
# scratch painted over with a 7 px brush measures 6 to 8.5, crossings included. The painted
# width decides, not the scratch's: under the starting brush (16 px at 12 MP, 24 px at 24 MP)
# a hairline is a wide mark, and the model filled it better than Telea on every test photo.
HAIRLINE_WIDTH = 9.0
# Wide marks this close (pixels) share a window.
MERGE_DISTANCE = 24
# A group of marks up to this size (its longer side) gets the small window, a larger one the
# large window; a group longer than `CELL` is cut into cells of that size.
SMALL_GROUP = 150
SMALL_WINDOW = 256
LARGE_WINDOW = 512
CELL = 400
# The model leaves a ring of about 2 px at a hole's edge unfilled, so it is given the hole
# grown by this much and only the painted pixels are taken back.
GROW = 3
# The model's mask: 255 keeps a pixel, 0 is the hole (the other way round it copies the hole).
KEEP, HOLE = 255, 0
# About what one window takes on a CPU (0.23 s measured on an M1 Pro), for the time left
# before the first windows have been timed.
SECONDS_PER_WINDOW = 0.25
# CPU threads for the model; more gained nothing on an M1 Pro under load.
MODEL_THREADS = 4

WindowFill = Callable[[np.ndarray, np.ndarray], np.ndarray]
"""Fills ``hole`` (bool, height x width) in ``window`` (uint8, height x width x 3)."""


@dataclass(frozen=True, slots=True)
class Window:
    """One model run: the box of its marks, and the window around them (pixels, end exclusive)."""

    marks: tuple[int, int, int, int]  # left, top, right, bottom
    box: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class RepairPlan:
    wide: np.ndarray  # bool, height x width: the painted pixels of the wide marks
    windows: tuple[Window, ...]


@dataclass(frozen=True, slots=True)
class RepairResult:
    pixels: np.ndarray  # float32, channels x height x width, 0..1
    windows: int = 0  # how many windows the model filled
    note: str = ""  # why the model was not used although it is installed


def mark_widths(painted: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The paint's marks: their labels (0 is unpainted) and the width of each, index = label."""
    import cv2

    count, labels = cv2.connectedComponents(painted.astype(np.uint8), connectivity=8)
    widths = np.zeros(count, dtype=np.float64)
    if count <= 1:
        return labels, widths
    distance = cv2.distanceTransform(painted.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    # The centre line: pixels no farther from the edge than any neighbour.
    ridge = painted & (distance >= cv2.dilate(distance, np.ones((3, 3), np.uint8)))
    owners = labels[ridge]
    order = np.argsort(owners, kind="stable")
    owners, values = owners[order], distance[ridge][order]
    starts = np.flatnonzero(np.r_[True, owners[1:] != owners[:-1]])
    for start, end in zip(starts, np.r_[starts[1:], len(owners)], strict=True):
        widths[owners[start]] = 2.0 * float(np.median(values[start:end]))
    return labels, widths


def wide_marks(painted: np.ndarray) -> np.ndarray:
    """The painted pixels of the marks wider than a hairline."""
    labels, widths = mark_widths(painted)
    return (widths > HAIRLINE_WIDTH)[labels] & painted


def plan_windows(wide: np.ndarray) -> tuple[Window, ...]:
    """One window per group of nearby wide marks, or per cell of a long group, top to bottom."""
    import cv2

    height, width = wide.shape
    if not wide.any():
        return ()
    near = cv2.dilate(
        wide.astype(np.uint8), np.ones((MERGE_DISTANCE + 1, MERGE_DISTANCE + 1), np.uint8)
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(near, connectivity=8)
    windows: list[Window] = []
    for index in range(1, count):
        left, top, group_width, group_height = (int(value) for value in stats[index][:4])
        group = wide[top : top + group_height, left : left + group_width] & (
            labels[top : top + group_height, left : left + group_width] == index
        )
        for cell_top in range(0, group_height, CELL):
            for cell_left in range(0, group_width, CELL):
                ys, xs = np.nonzero(group[cell_top : cell_top + CELL, cell_left : cell_left + CELL])
                if not len(ys):
                    continue
                marks = (
                    left + cell_left + int(xs.min()),
                    top + cell_top + int(ys.min()),
                    left + cell_left + int(xs.max()) + 1,
                    top + cell_top + int(ys.max()) + 1,
                )
                windows.append(Window(marks, _window_around(marks, width, height)))
    return tuple(windows)


def _window_around(
    marks: tuple[int, int, int, int], width: int, height: int
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = marks
    size = SMALL_WINDOW if max(x1 - x0, y1 - y0) <= SMALL_GROUP else LARGE_WINDOW
    left = int(np.clip((x0 + x1) // 2 - size // 2, 0, max(width - size, 0)))
    top = int(np.clip((y0 + y1) // 2 - size // 2, 0, max(height - size, 0)))
    return left, top, min(left + size, width), min(top + size, height)


def plan_repair(painted: np.ndarray) -> RepairPlan:
    wide = wide_marks(painted)
    return RepairPlan(wide, plan_windows(wide))


def fill_windows(
    picture: np.ndarray,
    plan: RepairPlan,
    fill: WindowFill,
    *,
    check_cancelled: Callable[[], None] = lambda: None,
    report: Callable[[int, int], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, int]:
    """``picture`` (uint8, height x width x 3) with the wide marks filled window by window, the
    pixels that were filled, and how many windows ran. A window whose marks an earlier window
    already filled is skipped; a window fills every unfilled wide pixel inside it."""
    out = picture.copy()
    todo = plan.wide.copy()
    total = len(plan.windows)
    runs = 0
    if report is not None:
        report(0, total)
    for index, window in enumerate(plan.windows):
        check_cancelled()
        x0, y0, x1, y1 = window.marks
        if todo[y0:y1, x0:x1].any():
            left, top, right, bottom = window.box
            hole = todo[top:bottom, left:right].copy()
            result = fill(np.ascontiguousarray(out[top:bottom, left:right]), hole)
            if result.shape != hole.shape + (3,) or result.dtype != np.uint8:
                raise ValueError(
                    f"The inpainting model returned {result.dtype} {result.shape} "
                    f"for a {hole.shape[1]} x {hole.shape[0]} window."
                )
            view = out[top:bottom, left:right]
            view[hole] = result[hole]
            todo[top:bottom, left:right] &= ~hole
            runs += 1
        if report is not None:
            report(index + 1, total)
    return out, plan.wide & ~todo, runs


class MiganFill:
    """MI-GAN's ONNX pipeline (image and mask in uint8, result in uint8) on one window."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def __call__(self, window: np.ndarray, hole: np.ndarray) -> np.ndarray:
        import cv2

        grown = cv2.dilate(hole.astype(np.uint8), np.ones((2 * GROW + 1, 2 * GROW + 1), np.uint8))
        image = np.ascontiguousarray(window.transpose(2, 0, 1)[None])
        mask = np.where(grown > 0, HOLE, KEEP).astype(np.uint8)[None, None]
        (result,) = self.session.run(None, {"image": image, "mask": mask})
        return np.ascontiguousarray(np.asarray(result)[0].transpose(1, 2, 0))


def open_migan(path: str | Path) -> MiganFill:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = MODEL_THREADS
    options.log_severity_level = 3
    # Without the arena a 512 px window peaks at about 0.5 GB instead of 0.9 GB.
    options.enable_cpu_mem_arena = False
    options.enable_mem_pattern = False
    session = ort.InferenceSession(
        os.fspath(path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    return MiganFill(session)


def has_onnxruntime() -> bool:
    return importlib.util.find_spec("onnxruntime") is not None


def stage_model(model_id: str, model_path: str) -> Callable[[], WindowFill] | None:
    """What opens the model a repair stage names, after checking it is the catalog's
    inpainting model, intact; None for a stage without a model (every mark gets Telea)."""
    if not model_path:
        return None
    if model_id != INPAINT_MODEL.model_id:
        raise ValueError(f"The repair stage cannot use the model {model_id or model_path!r}.")
    if not has_onnxruntime():
        raise RuntimeError(
            "This engine cannot run the Dust & scratches model: ONNX Runtime is missing."
        )
    if not verify_file_checksum(
        model_path, INPAINT_MODEL.sha256, expected_size=INPAINT_MODEL.size_bytes
    ):
        raise ValueError(
            f"{INPAINT_MODEL.display_name} failed its integrity check. Download it again."
        )
    return lambda: open_migan(model_path)


def repair_picture(
    pixels: np.ndarray,
    painted: np.ndarray,
    *,
    open_model: Callable[[], WindowFill] | None = None,
    check_cancelled: Callable[[], None] = lambda: None,
    report: Callable[[int, int], None] | None = None,
) -> RepairResult:
    """``pixels`` (float, channels x height x width, 0..1) with the ``painted`` pixels filled.

    ``open_model`` opens the inpainting model; it is called only when there are wide marks.
    """
    import cv2

    channels = int(pixels.shape[0])
    mask = painted.astype(np.uint8) * 255
    # Whether every channel holds exact 8-bit levels; the model's 8-bit fill is then taken as is.
    eight_bit = True
    telea: list[np.ndarray] = []
    for channel in range(channels):
        check_cancelled()
        levels = np.rint(np.clip(pixels[channel], 0.0, 1.0) * 65535.0).astype(np.uint16)
        if open_model is not None and eight_bit:
            eight_bit = not np.any(levels % 257)
        telea.append(cv2.inpaint(levels, mask, TELEA_RADIUS, cv2.INPAINT_TELEA))

    def as_float(levels: list[np.ndarray]) -> np.ndarray:
        repaired = np.empty(pixels.shape, dtype=np.float32)
        for channel, filled in enumerate(levels):
            repaired[channel] = filled.astype(np.float32) / 65535.0
        return repaired

    if open_model is None or channels not in (1, 3):
        return RepairResult(as_float(telea))
    plan = plan_repair(painted)
    if not plan.windows:
        return RepairResult(as_float(telea))
    try:
        fill = open_model()
        telea8 = np.stack([np.rint(levels / 257.0).astype(np.uint8) for levels in telea], axis=-1)
        rgb = telea8 if channels == 3 else np.repeat(telea8, 3, axis=-1)
        filled, done, runs = fill_windows(
            rgb, plan, fill, check_cancelled=check_cancelled, report=report
        )
    except InterruptedError:
        raise
    except Exception as error:  # noqa: BLE001 - the Telea fill stands without the model
        reason = str(error).split("\n", 1)[0][:200] or type(error).__name__
        return RepairResult(
            as_float(telea),
            note=f"The large marks were filled without MI-GAN, which could not run: {reason}",
        )
    if channels == 1:
        filled = np.rint(filled.astype(np.float32).mean(axis=-1, keepdims=True)).astype(np.uint8)
    for channel, levels in enumerate(telea):
        model8 = filled[..., channel][done]
        if eight_bit:
            levels[done] = model8.astype(np.uint16) * 257
        else:
            change = model8.astype(np.int32) - telea8[..., channel][done].astype(np.int32)
            levels[done] = np.clip(levels[done].astype(np.int32) + change * 257, 0, 65535).astype(
                np.uint16
            )
    return RepairResult(as_float(telea), windows=runs)
