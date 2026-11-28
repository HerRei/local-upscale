"""Dust & scratches with the MI-GAN model: the split by mark width, the model's windows, the
16-bit path, progress and cancellation, the job and the preview patch. A stand-in session
replaces ONNX Runtime; one test at the end runs the real model when it is on this computer."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from localsr.core import repair
from localsr.core.model_catalog import CATALOG_BY_ID, INPAINT_MODEL, ModelPurpose
from localsr.core.pipeline import resolve_image_pipeline
from localsr.core.presets import PresetMode, rank_models_for_preset
from localsr.worker import server as worker_server
from localsr.worker.job_progress import ImageJobProgress
from localsr.worker.server import WorkerServer

COLOUR = np.array([12, 200, 34], dtype=np.uint8)
REAL_MODEL = Path(__file__).resolve().parents[1] / ".cache/migan/migan_pipeline_v2.onnx"


class FakeMigan:
    """Stands in for MI-GAN's ONNX session. Like the real pipeline it fills the hole (mask 0;
    255 keeps a pixel), leaves a 2 px ring at the hole's edge unfilled, and returns the other
    pixels a little changed."""

    def __init__(self, colour: np.ndarray = COLOUR, after_run=None) -> None:
        self.colour = colour
        self.after_run = after_run
        self.feeds: list[dict[str, np.ndarray]] = []

    def run(self, outputs, feeds):
        assert outputs is None
        image, mask = feeds["image"], feeds["mask"]
        assert image.dtype == np.uint8 and mask.dtype == np.uint8
        assert image.shape[:2] == (1, 3) and mask.shape[:2] == (1, 1)
        assert image.shape[2:] == mask.shape[2:]
        self.feeds.append({"image": image.copy(), "mask": mask.copy()})
        hole = mask[0, 0] == 0
        inner = cv2.erode(hole.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        result = image ^ np.uint8(1)
        result[0][:, inner] = self.colour[:, None]
        if self.after_run is not None:
            self.after_run(len(self.feeds))
        return [result]


def _stripes(width: int = 320, height: int = 240) -> np.ndarray:
    """An 8-bit picture with texture, so a Telea fill and a model fill are told apart."""
    x = np.arange(width)[None, :, None]
    y = np.arange(height)[:, None, None]
    base = 90 + 40 * np.sin(x / 5.0) + 0.3 * y + np.array([0, 15, 30])
    return np.clip(base, 0, 255).astype(np.uint8)


def _paint(height: int, width: int, *shapes) -> np.ndarray:
    """A mask as the host draws a brush stroke (repair_mask.rs): every pixel whose centre is
    within half the brush of the stroke's segment. A circle is a dab of a brush ``2 r`` wide."""
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64) + 0.5
    mask = np.zeros((height, width), dtype=bool)
    for kind, *args in shapes:
        if kind == "line":
            (x0, y0, x1, y1, size) = args
        else:
            (x0, y0, radius) = args
            x1, y1, size = x0, y0, 2 * radius
        dx, dy = x1 - x0, y1 - y0
        along = np.clip(((xs - x0) * dx + (ys - y0) * dy) / max(dx * dx + dy * dy, 1e-9), 0, 1)
        mask |= (xs - x0 - along * dx) ** 2 + (ys - y0 - along * dy) ** 2 <= (size / 2) ** 2
    return mask


def _chw(picture: np.ndarray) -> np.ndarray:
    return picture.transpose(2, 0, 1).astype(np.float32) / 255.0


def _to8(pixels: np.ndarray) -> np.ndarray:
    return np.rint(np.clip(pixels, 0, 1) * 255).astype(np.uint8).transpose(1, 2, 0)


def _todays_repair(pixels: np.ndarray, painted: np.ndarray) -> np.ndarray:
    """The handler before the model existed, verbatim: Telea per channel on 16-bit levels."""
    mask = painted.astype(np.uint8) * 255
    repaired = np.empty_like(pixels)
    for channel in range(pixels.shape[0]):
        levels = np.rint(np.clip(pixels[channel], 0.0, 1.0) * 65535.0).astype(np.uint16)
        filled = cv2.inpaint(levels, mask, 3, cv2.INPAINT_TELEA)
        repaired[channel] = filled.astype(np.float32) / 65535.0
    return repaired


# ----- the split ---------------------------------------------------------------------------


def test_marks_are_split_by_their_painted_width():
    painted = _paint(
        300,
        420,
        ("line", 10, 20, 200, 30, 3),  # a 3 px scratch
        ("line", 10, 60, 200, 70, 7),  # a scratch painted with a 7 px brush
        ("line", 230, 20, 400, 20, 8),  # 8 px: still a hairline
        ("line", 230, 60, 400, 60, 10),  # 10 px: wide
        ("circle", 80, 200, 20),  # a 40 px blotch
    )
    # Two 7 px scratches crossing: wider at the crossing, still a hairline.
    painted |= _paint(300, 420, ("line", 230, 150, 400, 150, 7), ("line", 315, 100, 315, 200, 7))
    labels, widths = repair.mark_widths(painted)
    width_at = {
        name: widths[labels[y, x]]
        for name, (x, y) in {
            "3": (100, 25),
            "7": (100, 65),
            "8": (300, 20),
            "10": (300, 60),
            "blotch": (80, 200),
            "crossing": (315, 150),
        }.items()
    }
    assert width_at["3"] < 5 and width_at["7"] <= 8.5 and width_at["crossing"] <= 8.5
    assert width_at["8"] < repair.HAIRLINE_WIDTH < width_at["10"]
    assert width_at["blotch"] > 35
    wide = repair.wide_marks(painted)
    assert not wide[labels == labels[25, 100]].any()
    assert not wide[labels == labels[150, 315]].any()
    assert wide[labels == labels[60, 300]].all() and wide[labels == labels[200, 80]].all()
    assert not (wide & ~painted).any()


def test_near_marks_share_a_window_far_ones_do_not_and_long_ones_are_cut():
    near = _paint(600, 900, ("circle", 100, 100, 12), ("circle", 140, 100, 12))
    (window,) = repair.plan_windows(near)
    assert window.marks == (88, 88, 152, 112)
    # A group up to 150 px gets the small window, centred on it and inside the picture.
    assert window.box == (0, 0, 256, 256)
    far = _paint(600, 900, ("circle", 100, 100, 12), ("circle", 200, 300, 12))
    assert len(repair.plan_windows(far)) == 2
    middle = _paint(600, 900, ("circle", 450, 300, 90))
    (big,) = repair.plan_windows(middle)
    assert big.box == (194, 44, 706, 556)  # 512 px around a 181 px mark
    long = _paint(600, 900, ("line", 20, 300, 880, 300, 20))
    cells = repair.plan_windows(long)
    assert len(cells) == 3
    assert all(x1 - x0 <= repair.CELL for (x0, _y0, x1, _y1) in (w.marks for w in cells))
    # A picture smaller than the window gets a window of its own size.
    small = _paint(120, 200, ("circle", 60, 60, 15))
    assert repair.plan_windows(small)[0].box == (0, 0, 200, 120)


# ----- the model's windows -----------------------------------------------------------------


def test_the_model_gets_255_to_keep_and_the_hole_grown_by_3_px():
    picture = _stripes()
    painted = _paint(240, 320, ("circle", 160, 120, 20))
    fake = FakeMigan()
    result = repair.repair_picture(
        _chw(picture), painted, open_model=lambda: repair.MiganFill(fake)
    )
    assert result.windows == 1
    (feeds,) = fake.feeds
    left, top, right, bottom = repair.plan_windows(painted)[0].box
    grown = cv2.dilate(painted.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    np.testing.assert_array_equal(
        feeds["mask"][0, 0], np.where(grown, 0, 255)[top:bottom, left:right]
    )
    # The model sees the picture, the hole already holding Telea's fill.
    window = feeds["image"][0].transpose(1, 2, 0)
    np.testing.assert_array_equal(
        window[~grown[top:bottom, left:right]],
        picture[top:bottom, left:right][~grown[top:bottom, left:right]],
    )


def test_only_the_painted_pixels_are_taken_from_the_model():
    picture = _stripes()
    painted = _paint(240, 320, ("circle", 160, 120, 20))
    out = _to8(
        repair.repair_picture(
            _chw(picture), painted, open_model=lambda: repair.MiganFill(FakeMigan())
        ).pixels
    )
    # Every painted pixel is the model's, though it left a ring of the grown hole as it was.
    assert (out[painted] == COLOUR).all()
    np.testing.assert_array_equal(out[~painted], picture[~painted])


def test_a_window_changes_only_the_unfilled_wide_pixels():
    picture = _stripes(900, 300)
    painted = _paint(300, 900, ("circle", 300, 150, 30), ("circle", 430, 150, 30))
    plan = repair.plan_repair(painted)
    out, done, runs = repair.fill_windows(picture, plan, repair.MiganFill(FakeMigan()))
    assert runs == 2
    np.testing.assert_array_equal(done, painted)
    np.testing.assert_array_equal(out[~painted], picture[~painted])
    assert (out[painted] == COLOUR).all()


def test_hairlines_keep_their_telea_fill_beside_a_model_fill():
    picture = _stripes()
    painted = _paint(240, 320, ("circle", 80, 120, 20), ("line", 180, 40, 300, 200, 5))
    pixels = _chw(picture)
    today = _todays_repair(pixels, painted)
    result = repair.repair_picture(
        pixels, painted, open_model=lambda: repair.MiganFill(FakeMigan())
    )
    wide = repair.wide_marks(painted)
    assert wide.any() and (painted & ~wide).any()
    np.testing.assert_array_equal(result.pixels[:, painted & ~wide], today[:, painted & ~wide])
    np.testing.assert_array_equal(result.pixels[:, ~painted], today[:, ~painted])
    assert (_to8(result.pixels)[wide] == COLOUR).all()


def test_later_windows_see_the_fills_of_earlier_ones():
    picture = _stripes(900, 300)
    # Two groups 100 px apart: the second window overlaps the first group's pixels.
    painted = _paint(300, 900, ("circle", 300, 150, 30), ("circle", 430, 150, 30))
    fake = FakeMigan()
    repair.repair_picture(_chw(picture), painted, open_model=lambda: repair.MiganFill(fake))
    assert len(fake.feeds) == 2
    left, top, _right, _bottom = repair.plan_windows(painted)[1].box
    seen = fake.feeds[1]["image"][0].transpose(1, 2, 0)
    first = _paint(300, 900, ("circle", 300, 150, 30))
    inside = first[top : top + seen.shape[0], left : left + seen.shape[1]]
    assert inside.any() and (seen[inside] == COLOUR).all()


def test_a_grey_picture_is_filled_through_three_channels():
    grey = _stripes()[..., :1]
    painted = _paint(240, 320, ("circle", 160, 120, 20))
    fake = FakeMigan(np.array([30, 60, 90], np.uint8))
    result = repair.repair_picture(_chw(grey), painted, open_model=lambda: repair.MiganFill(fake))
    assert result.pixels.shape == (1, 240, 320)
    assert (_to8(result.pixels)[painted] == 60).all()


def test_without_a_model_the_result_is_todays_bit_for_bit():
    rng = np.random.default_rng(3)
    painted = _paint(240, 320, ("circle", 80, 120, 25), ("line", 180, 40, 300, 200, 4))
    eight = _chw(_stripes())
    sixteen = rng.integers(0, 65536, (3, 240, 320)).astype(np.float32) / 65535.0
    for pixels in (eight, sixteen):
        result = repair.repair_picture(pixels, painted)
        assert result.windows == 0 and not result.note
        np.testing.assert_array_equal(result.pixels, _todays_repair(pixels, painted))


def test_only_hairlines_do_not_open_the_model():
    painted = _paint(240, 320, ("line", 20, 40, 300, 200, 5))

    def never():
        raise AssertionError("the model was opened for hairlines")

    result = repair.repair_picture(_chw(_stripes()), painted, open_model=never)
    np.testing.assert_array_equal(result.pixels, _todays_repair(_chw(_stripes()), painted))


def test_a_model_that_fails_leaves_telea_everywhere_and_says_so():
    class Broken:
        def run(self, *_args):
            raise RuntimeError("bad allocation\nmore")

    painted = _paint(240, 320, ("circle", 160, 120, 20))
    result = repair.repair_picture(
        _chw(_stripes()), painted, open_model=lambda: repair.MiganFill(Broken())
    )
    np.testing.assert_array_equal(result.pixels, _todays_repair(_chw(_stripes()), painted))
    assert result.windows == 0
    assert result.note.endswith("could not run: bad allocation")


# ----- 16 bits -----------------------------------------------------------------------------


def test_16_bit_pictures_get_the_models_change_on_the_16_bit_telea_fill():
    rng = np.random.default_rng(8)
    levels = np.clip(
        _stripes().astype(np.float64).transpose(2, 0, 1) * 257
        + rng.integers(-90, 90, (3, 240, 320)),
        0,
        65535,
    ).astype(np.uint16)
    pixels = levels.astype(np.float32) / 65535.0
    painted = _paint(240, 320, ("circle", 160, 120, 20))
    result = repair.repair_picture(
        pixels, painted, open_model=lambda: repair.MiganFill(FakeMigan())
    )
    out = np.rint(result.pixels * 65535).astype(np.int64)
    telea = np.rint(_todays_repair(pixels, painted) * 65535).astype(np.int64)
    telea8 = np.rint(telea / 257.0)
    expected = np.clip(telea + (COLOUR.astype(np.int64)[:, None, None] - telea8) * 257, 0, 65535)
    np.testing.assert_array_equal(out[:, painted], expected[:, painted])
    # Outside the paint every 16-bit value stays.
    np.testing.assert_array_equal(out[:, ~painted], levels.astype(np.int64)[:, ~painted])


def test_an_8_bit_picture_takes_the_models_8_bit_fill_as_it_is():
    pixels = _chw(_stripes())
    painted = _paint(240, 320, ("circle", 160, 120, 20))
    result = repair.repair_picture(
        pixels, painted, open_model=lambda: repair.MiganFill(FakeMigan())
    )
    out = np.rint(result.pixels * 65535).astype(np.int64)
    np.testing.assert_array_equal(
        out[:, painted], np.repeat(COLOUR.astype(np.int64)[:, None] * 257, painted.sum(), axis=1)
    )


# ----- progress and cancel -----------------------------------------------------------------


def test_windows_report_progress_and_cancel_between_them():
    picture = _stripes(900, 300)
    painted = _paint(
        300, 900, ("circle", 100, 150, 20), ("circle", 450, 150, 20), ("circle", 800, 150, 20)
    )
    reports: list[tuple[int, int]] = []
    fake = FakeMigan()
    repair.repair_picture(
        _chw(picture),
        painted,
        open_model=lambda: repair.MiganFill(fake),
        report=lambda *a: reports.append(a),
    )
    assert reports == [(0, 3), (1, 3), (2, 3), (3, 3)]

    stopped = FakeMigan()
    state = {"stop": False}

    def check():
        if state["stop"]:
            raise InterruptedError("Cancelled between pipeline stages.")

    def after(count):
        state["stop"] = count == 2

    stopped.after_run = after
    with pytest.raises(InterruptedError):
        repair.repair_picture(
            _chw(picture),
            painted,
            open_model=lambda: repair.MiganFill(stopped),
            check_cancelled=check,
        )
    assert len(stopped.feeds) == 2


class _Preview:
    def submit(self, **_kwargs) -> None:
        pass


def test_piece_progress_counts_no_tiles_and_expects_a_quarter_second_each(tmp_path):
    mask = tmp_path / "m.png"
    Image.fromarray(np.zeros((8, 8), np.uint8)).save(mask)
    pipeline = resolve_image_pipeline(
        raw_stages=[{"kind": "repair", "step": "repair", "mask_path": str(mask)}],
        model_path="",
        output_scale=1,
    )
    sent = []
    now = [0.0]
    progress = ImageJobProgress(
        "job", pipeline, "cpu", sent.append, _Preview(), clock=lambda: now[0]
    )
    progress.start(0, pipeline.stages[0])
    updates = []
    for done in range(9):
        now[0] = done * 0.5
        progress.pieces(done, 8, seconds_each=0.25, detail=f"{done} of 8")
        (update,) = [m for m in sent if type(m).__name__ == "ProgressUpdate"][-1:]
        updates.append(update)
    assert all(u.completed_tiles == 0 and u.total_tiles == 0 for u in updates)
    assert [u.detail for u in updates][:2] == ["0 of 8", "1 of 8"]
    assert updates[4].percentage == pytest.approx(50.0)
    # Before the windows are timed each one left counts a quarter second...
    assert updates[0].estimated_remaining_seconds == pytest.approx(2.0)
    assert updates[2].estimated_remaining_seconds == pytest.approx(1.5)
    # ...then their measured speed (0.5 s each) takes over.
    assert updates[3].estimated_remaining_seconds == pytest.approx(2.5)
    assert progress.weighted_speed is None


# ----- the stage, the job and the patch ---------------------------------------------------


@pytest.fixture
def model_file(tmp_path, monkeypatch):
    """A stand-in for the downloaded model: the catalog entry is pointed at its bytes, and
    opening it gives the stand-in session."""
    path = tmp_path / "migan_pipeline_v2.onnx"
    path.write_bytes(b"stand-in model")
    entry = dataclasses.replace(
        INPAINT_MODEL,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(repair, "INPAINT_MODEL", entry)
    monkeypatch.setattr(repair, "has_onnxruntime", lambda: True)
    sessions: list[FakeMigan] = []

    def open_fake(_path):
        sessions.append(FakeMigan())
        return repair.MiganFill(sessions[-1])

    monkeypatch.setattr(repair, "open_migan", open_fake)
    return path, sessions


def test_a_stage_without_a_model_uses_telea_and_a_wrong_model_is_refused(model_file, tmp_path):
    path, sessions = model_file
    assert repair.stage_model("", "") is None
    opener = repair.stage_model("migan", str(path))
    assert opener is not None and not sessions
    with pytest.raises(ValueError, match="cannot use the model"):
        repair.stage_model("birefnet_general", str(path))
    other = tmp_path / "other.onnx"
    other.write_bytes(b"stand-in modeL")
    with pytest.raises(ValueError, match="failed its integrity check"):
        repair.stage_model("migan", str(other))


def test_the_engine_lists_the_feature(monkeypatch):
    from localsr.worker.server import _engine_features

    monkeypatch.setattr(repair, "has_onnxruntime", lambda: True)
    assert "image_repair_model" in _engine_features([])
    monkeypatch.setattr(repair, "has_onnxruntime", lambda: False)
    assert "image_repair_model" not in _engine_features([])


def test_the_catalog_model_is_never_a_fix_upscaler_or_preset():
    model = CATALOG_BY_ID["migan"]
    assert model is INPAINT_MODEL
    assert model.purposes == (ModelPurpose.INPAINT,) and model.stage_kind == "inpaint"
    assert model.native_scale == 1 and not model.fixes and not model.preset_eligible
    assert model.license_name == "MIT" and model.commercial_use_status == "allowed"
    assert model.size_bytes == 28_079_181
    assert "/resolve/406830d0fa60666da0071c342ad2fbc8f30c5c64/" in model.download_url
    for mode in (PresetMode.QUICK_UPSCALE, PresetMode.BEST_DENOISE):
        for purpose in ("photo", "denoise", "restoration", "general"):
            assert model not in rank_models_for_preset([model], mode, purpose=purpose)


class _NoUpscaler:
    def release_model(self, path=None) -> None:
        pass

    def release_models_except(self, keep) -> None:
        self.kept = set(keep)


class _Adapter:
    allow_unverified_checkpoints = False

    def inspect(self, path):
        raise AssertionError(f"the repair model was inspected as a checkpoint: {path}")

    def release(self) -> None:
        pass


def _job(tmp_path, monkeypatch, source: Path, stage: dict, *, depth: int = 8, after=None):
    sent: list = []
    monkeypatch.setattr(worker_server, "send_message", sent.append)
    data = {
        "job_id": "job",
        "image_path": str(source),
        "model_path": "",
        "output_path": str(tmp_path / "out.png"),
        "scratch_directory": str(tmp_path / "scratch"),
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 64,
        "halo": 8,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
        "output_scale": 1,
        "preview_enabled": False,
        "stages": [stage],
        **({"output_bit_depth": 16} if depth == 16 else {}),
    }
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _NoUpscaler()
    if after is not None:
        after(server)
    server._run_job("job", data)
    return Path(data["output_path"]), [json.loads(m.to_json()) for m in sent], server


def _repair_stage(mask: Path, model: Path | None) -> dict:
    stage = {"kind": "repair", "step": "repair", "mask_path": str(mask)}
    if model is not None:
        stage.update(model_id="migan", model_path=str(model))
    return stage


def test_a_job_fills_wide_marks_with_the_model_it_names(model_file, tmp_path, monkeypatch):
    path, sessions = model_file
    picture = _stripes(900, 300)
    painted = _paint(
        300, 900, ("circle", 100, 150, 20), ("circle", 450, 150, 20), ("line", 600, 40, 860, 260, 5)
    )
    source = tmp_path / "in.png"
    Image.fromarray(picture).save(source)
    mask = tmp_path / "mask.png"
    Image.fromarray(painted.astype(np.uint8) * 255).save(mask)

    output, messages, server = _job(tmp_path, monkeypatch, source, _repair_stage(mask, path))

    with Image.open(output) as saved:
        out = np.asarray(saved.convert("RGB"))
    wide = repair.wide_marks(painted)
    assert (out[wide] == COLOUR).all()
    np.testing.assert_array_equal(out[~painted], picture[~painted])
    hair = painted & ~wide
    today = _to8(_todays_repair(_chw(picture), painted))
    np.testing.assert_array_equal(out[hair], today[hair])
    assert len(sessions) == 1 and len(sessions[0].feeds) == 2
    pieces = [m["data"] for m in messages if m["type"] == "progress" and m["data"]["detail"]]
    assert [p["detail"] for p in pieces] == [f"Filling large marks: {n} of 2" for n in range(3)]
    assert all(p["completed_tiles"] == 0 for p in pieces)
    # The model runs on ONNX Runtime in the step, not on the inference engine.
    assert server.engine.kept == set()


def test_a_job_without_a_model_is_todays_and_never_opens_one(model_file, tmp_path, monkeypatch):
    _path, sessions = model_file
    picture = _stripes()
    painted = _paint(240, 320, ("circle", 160, 120, 20))
    source = tmp_path / "in.png"
    Image.fromarray(picture).save(source)
    mask = tmp_path / "mask.png"
    Image.fromarray(painted.astype(np.uint8) * 255).save(mask)

    output, _messages, _server = _job(tmp_path, monkeypatch, source, _repair_stage(mask, None))

    with Image.open(output) as saved:
        out = np.asarray(saved.convert("RGB"))
    np.testing.assert_array_equal(out, _to8(_todays_repair(_chw(picture), painted)))
    assert not sessions


def test_a_16_bit_job_keeps_every_16_bit_value_outside_the_paint(model_file, tmp_path, monkeypatch):
    path, _sessions = model_file
    rng = np.random.default_rng(4)
    levels = np.clip(
        _stripes().astype(np.int64) * 257 + rng.integers(-90, 90, (240, 320, 3)), 0, 65535
    ).astype(np.uint16)
    source = tmp_path / "in.png"
    cv2.imwrite(str(source), levels[..., ::-1])
    painted = _paint(240, 320, ("circle", 160, 120, 20))
    mask = tmp_path / "mask.png"
    Image.fromarray(painted.astype(np.uint8) * 255).save(mask)

    output, _messages, _server = _job(
        tmp_path, monkeypatch, source, _repair_stage(mask, path), depth=16
    )

    out = cv2.imread(str(output), cv2.IMREAD_UNCHANGED)[..., ::-1]
    assert out.dtype == np.uint16
    np.testing.assert_array_equal(out[~painted], levels[~painted])
    # The painted place took the model's colour (on top of Telea's 16-bit fill).
    assert np.abs(out[painted].astype(np.int64) // 257 - COLOUR).max() <= 1


def test_a_cancel_stops_the_job_between_windows(model_file, tmp_path, monkeypatch):
    path, sessions = model_file
    picture = _stripes(900, 300)
    painted = _paint(
        300, 900, ("circle", 100, 150, 20), ("circle", 450, 150, 20), ("circle", 800, 150, 20)
    )
    source = tmp_path / "in.png"
    Image.fromarray(picture).save(source)
    mask = tmp_path / "mask.png"
    Image.fromarray(painted.astype(np.uint8) * 255).save(mask)
    holder = {}

    def stop_after_first(count):
        if count == 1:
            holder["server"].cancel_event.set()

    def remember(server):
        holder["server"] = server

    original = repair.open_migan

    def open_stopping(model_path):
        fill = original(model_path)
        fill.session.after_run = stop_after_first
        return fill

    monkeypatch.setattr(repair, "open_migan", open_stopping)
    output, messages, _server = _job(
        tmp_path, monkeypatch, source, _repair_stage(mask, path), after=remember
    )
    assert "job_cancelled" in [m["type"] for m in messages]
    assert not output.exists()
    assert len(sessions[0].feeds) == 1


def test_the_patch_runs_the_same_split_as_the_job(model_file, tmp_path, monkeypatch):
    path, _sessions = model_file
    monkeypatch.setenv("LOCALSR_WORK_DIR", str(tmp_path / "work"))
    picture = _stripes(300, 260)
    source = tmp_path / "photo.png"
    Image.fromarray(picture).save(source)
    painted = _paint(260, 300, ("circle", 130, 120, 18), ("line", 60, 60, 200, 80, 5))
    mask = tmp_path / "mask.png"
    Image.fromarray(painted.astype(np.uint8) * 255).save(mask)
    job_output, _messages, _server = _job(tmp_path, monkeypatch, source, _repair_stage(mask, path))
    with Image.open(job_output) as saved:
        job = np.asarray(saved.convert("RGB"))

    sent: list = []
    monkeypatch.setattr(worker_server, "send_message", sent.append)
    server = WorkerServer()
    server.model_adapter = _Adapter()
    server.engine = _NoUpscaler()
    server._run_patch(
        {
            "patch_id": "p1",
            "slot": "a",
            "image_path": str(source),
            "frame_index": -1,
            "region": {"x": 40, "y": 30, "width": 192, "height": 192},
            "padding": 32,
            "stages": [_repair_stage(mask, path)],
            "output_scale": 1,
            "device": "cpu",
            "precision": "fp32",
            "tile_size": 256,
            "halo": 16,
            "neural_engine": False,
            "safe_memory": True,
        }
    )
    done = [
        json.loads(m.to_json())["data"]
        for m in sent
        if type(m).__name__ == "PatchStage" and m.state == "done"
    ]
    assert len(done) == 1
    assert not [m for m in sent if type(m).__name__ == "PatchStage" and m.state == "skipped"]
    import base64
    import io

    with Image.open(io.BytesIO(base64.b64decode(done[0]["png_base64"]))) as image:
        patch = np.asarray(image.convert("RGB"))
    wide = repair.wide_marks(painted)[30:222, 40:232]
    region = job[30:222, 40:232]
    assert wide.any()
    np.testing.assert_array_equal(patch[wide], region[wide])
    outside = ~painted[30:222, 40:232]
    np.testing.assert_array_equal(patch[outside], region[outside])


# ----- the real model, only where it is on this computer ------------------------------------


@pytest.mark.skipif(
    not REAL_MODEL.is_file(),
    reason="OPTIONAL: MI-GAN not cached at .cache/migan/migan_pipeline_v2.onnx (28 MB)",
)
def test_real_migan_fills_a_wide_mark_better_than_telea():
    assert repair.stage_model("migan", str(REAL_MODEL)) is not None  # the catalog's bytes
    clean = _stripes(512, 384)
    painted = _paint(384, 512, ("circle", 256, 192, 30), ("line", 40, 40, 200, 60, 5))
    marked = clean.copy()
    marked[painted] = 250
    pixels = _chw(marked)
    telea = _to8(repair.repair_picture(pixels, painted).pixels)
    result = repair.repair_picture(
        pixels, painted, open_model=lambda: repair.open_migan(REAL_MODEL)
    )
    filled = _to8(result.pixels)
    wide = repair.wide_marks(painted)
    assert result.windows == 1 and not result.note
    np.testing.assert_array_equal(filled[~painted], marked[~painted])
    np.testing.assert_array_equal(filled[painted & ~wide], telea[painted & ~wide])

    def error(picture):
        return float(np.abs(picture[wide].astype(np.int32) - clean[wide]).mean())

    print(f"mean error in the wide mark: Telea {error(telea):.1f}, MI-GAN {error(filled):.1f}")
    # The right mask polarity fills the hole; the wrong one would copy the white mark through.
    assert (filled[wide] >= 245).mean() < 0.05
    assert error(filled) < error(telea)
