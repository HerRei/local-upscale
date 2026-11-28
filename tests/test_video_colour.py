"""Temporal colour propagation on small synthetic shots."""

import cv2
import numpy as np

from localsr.core.video_colour import _cut_luma, _luma, colour_frames


def grey(value=100):
    return np.full((64, 80, 3), value, np.uint8)


def test_cut_detection_distinguishes_motion_from_new_shot():
    a = grey()
    a[20:40, 20:40] = 180
    b = np.roll(a, 3, axis=1)
    assert not _cut_luma(_luma(a), _luma(b))
    assert _cut_luma(_luma(a), _luma(grey(220)))
    inverse = np.full((64, 80, 3), 32, dtype=np.uint8)
    inverse[:, :40] = 224
    assert _cut_luma(_luma(inverse), _luma(np.roll(inverse, 40, axis=1)))


def test_keyframes_and_static_flicker():
    def random_colouriser():
        rng = np.random.default_rng(99)
        calls = []

        def colourise(frame):
            calls.append(1)
            result = frame.copy()
            result[:] = rng.integers(70, 181, 3, dtype=np.uint8)
            return result

        return colourise, calls

    frames = [grey() for _ in range(25)]
    colourise, calls = random_colouriser()
    result = list(colour_frames(frames, colourise, interval=12))
    assert len(result) == 25
    assert len(calls) == 3
    independent, _ = random_colouriser()

    def flicker(images):
        labs = [cv2.cvtColor(x, cv2.COLOR_RGB2LAB).astype(np.float32)[:, :, 1:] for x in images]
        return np.mean([np.abs(a - b).mean() for a, b in zip(labs, labs[1:], strict=False)])

    assert flicker(result) < flicker([independent(frame) for frame in frames]) / 5


def test_cut_does_not_carry_colour():
    calls = []

    def colourise(frame):
        calls.append(1)
        result = frame.copy()
        result[:] = (150, 70, 70) if len(calls) == 1 else (70, 150, 70)
        return result

    result = list(colour_frames([grey(60)] * 4 + [grey(200)] * 4, colourise))
    a = cv2.cvtColor(result[3], cv2.COLOR_RGB2LAB)[:, :, 1:].mean(axis=(0, 1))
    b = cv2.cvtColor(result[4], cv2.COLOR_RGB2LAB)[:, :, 1:].mean(axis=(0, 1))
    assert len(calls) == 2
    assert np.linalg.norm(a - b) > 30


def test_flow_tracks_translated_pattern():
    rng = np.random.default_rng(4)
    base = rng.integers(50, 190, (96, 128), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (5, 5), 0)
    frames = [np.repeat(np.roll(base, i * 2, axis=1)[:, :, None], 3, axis=2) for i in range(7)]

    def colourise(frame):
        lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
        lab[:, :, 1] = np.where(lab[:, :, 0] > 128, 155, 105)
        lab[:, :, 2] = 128
        return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    result = list(colour_frames(frames, colourise, interval=12))
    expected = cv2.cvtColor(colourise(frames[3]), cv2.COLOR_RGB2LAB)[15:-15, 20:-20, 1]
    actual = cv2.cvtColor(result[3], cv2.COLOR_RGB2LAB)[15:-15, 20:-20, 1]
    assert np.abs(actual.astype(float) - expected).mean() < 10


def test_inconsistent_flow_marks_occlusions():
    from localsr.core.video_colour import _inconsistent

    forward = np.zeros((40, 40, 2), np.float32)
    backward = np.zeros_like(forward)
    backward[10:30, 10:30, 0] = 8
    mask = _inconsistent(forward, backward)
    assert mask[15:25, 15:25].all()
    assert not mask[:5, :5].any()
    # Opposite flows that leave the image cannot have a valid correspondence.
    forward[:, :, 0] = -8
    backward[:, :, 0] = 8
    edge = _inconsistent(forward, backward)
    assert edge[:, -8:].all()


def test_occlusion_fallback_uses_nearest_keyframe():
    from localsr.core.video_colour import _blend_chroma

    forward = np.full((16, 16, 2), 70, np.float32)
    backward = np.full((16, 16, 2), 170, np.float32)
    invalid = np.ones((16, 16), bool)
    near_first = _blend_chroma(forward, backward, 0.25, invalid)
    near_last = _blend_chroma(forward, backward, 0.75, invalid)
    np.testing.assert_allclose(near_first, forward)
    np.testing.assert_allclose(near_last, backward)


def test_low_confidence_schedules_a_keyframe(monkeypatch):
    import localsr.core.video_colour as video_colour

    monkeypatch.setattr(
        video_colour, "_inconsistent", lambda forward, backward: np.ones(forward.shape[:2], bool)
    )
    calls = []

    def colourise(frame):
        calls.append(1)
        return frame

    assert len(list(colour_frames([grey()] * 4, colourise))) == 4
    assert len(calls) == 4


def test_upsampled_flow_warps_keyframe_chroma_at_output_resolution():
    from localsr.core.video_colour import _render_mapped

    frame = np.full((160, 240, 3), 128, np.uint8)
    key = np.full((80, 120, 2), 128, np.float32)
    key[:, 30:60, 0] = 150
    flow = np.zeros((80, 120, 2), np.float32)
    flow[:, :, 0] = -4
    output = _render_mapped(frame, key, flow, None, None, 0.0, None, np.zeros_like(key))
    expected_ab = cv2.resize(key, (240, 160), interpolation=cv2.INTER_LINEAR)
    expected_ab = np.roll(expected_ab, 8, axis=1)
    lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
    lab[:, :, 1:] = expected_ab.astype(np.uint8)
    expected = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    assert np.abs(output[:, 20:].astype(float) - expected[:, 20:]).mean() < 1


def _texture(rng, shape, low, span):
    noise = cv2.GaussianBlur(rng.integers(0, 256, shape).astype(np.float32), (0, 0), 1.2)
    noise = (noise - noise.min()) / (noise.max() - noise.min())
    return (low + span * noise).astype(np.uint8)


def _hue(angle, radius):
    return 128 + radius * np.cos(np.radians(angle)), 128 + radius * np.sin(np.radians(angle))


class _ChangingColouriser:
    """Paints by brightness band and picks a different hue for the shape and the ground per call."""

    shape_hues = (20, 60, -20, 100, 0, 40)
    ground_hues = (120, 170, 140, 100, 160, 130)
    new_colour = _hue(200, 30)

    def __init__(self):
        self.calls = 0

    def __call__(self, frame):
        hue = self.calls % len(self.shape_hues)
        self.calls += 1
        lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
        luma = lab[:, :, 0]
        colours = [
            (luma < 135, _hue(self.ground_hues[hue], 14)),
            ((luma >= 135) & (luma < 190), _hue(self.shape_hues[hue], 40)),
            (luma >= 190, self.new_colour),
        ]
        for mask, (a, b) in colours:
            lab[:, :, 1][mask] = round(a)
            lab[:, :, 2][mask] = round(b)
        return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def _scene(count, *, newcomer=False, seed=7):
    """A textured square moving right over textured ground; optionally a brighter square entering."""
    rng = np.random.default_rng(seed)
    ground = _texture(rng, (96, 128), 30, 70)
    square = _texture(rng, (28, 28), 145, 35)
    bright = _texture(rng, (24, 24), 200, 45)
    frames, boxes = [], []
    for index in range(count):
        image = ground.copy()
        left = 12 + index
        image[34:62, left : left + 28] = square
        if newcomer and index >= 10:
            right = 128 - (index - 10) * 2
            image[8:32, right : min(128, right + 24)] = bright[:, : min(24, 128 - right)]
        frames.append(np.repeat(image[:, :, None], 3, axis=2))
        boxes.append((34, 62, left, left + 28))
    return frames, boxes


def _chroma(image, rows, cols):
    lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB).astype(np.float32)
    return lab[rows, cols, 1:].reshape(-1, 2).mean(axis=0)


def test_tracked_surface_keeps_its_colour_across_keyframes():
    frames, boxes = _scene(40)
    colourise = _ChangingColouriser()
    result = list(colour_frames(frames, colourise, interval=12))
    assert colourise.calls == 4
    shape = [
        _chroma(image, slice(top + 5, bottom - 5), slice(left + 5, right - 5))
        for image, (top, bottom, left, right) in zip(result, boxes, strict=True)
    ]
    ground = [_chroma(image, slice(70, 92), slice(70, 120)) for image in result]
    # The colouriser's later guesses differ by 25 to 50 units; the first decision must hold.
    assert max(np.linalg.norm(value - shape[0]) for value in shape) < 5
    assert max(np.linalg.norm(value - ground[0]) for value in ground) < 4
    assert np.linalg.norm(shape[0] - _hue(20, 40)) < 6


def test_newly_visible_object_takes_the_new_guess():
    frames, _ = _scene(40, newcomer=True)
    result = list(colour_frames(frames, _ChangingColouriser(), interval=12))
    # The bright square enters at frame 10, moving left, and is fully visible from frame 12 + 10.
    for index in range(24, 40):
        right = 128 - (index - 10) * 2
        newcomer = _chroma(result[index], slice(12, 28), slice(right + 4, right + 20))
        assert np.linalg.norm(newcomer - _ChangingColouriser.new_colour) < 8, index


def test_revealed_ground_takes_ground_colour_not_a_trail():
    rng = np.random.default_rng(3)
    ground = _texture(rng, (96, 128), 30, 70)
    square = _texture(rng, (28, 28), 145, 35)
    frames = []
    for index in range(25):
        image = ground.copy()
        image[34:62, 10 + 2 * index : 38 + 2 * index] = square
        frames.append(np.repeat(image[:, :, None], 3, axis=2))

    def colourise(frame):
        lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
        bright = lab[:, :, 0] >= 135
        lab[:, :, 1] = np.where(bright, 170, 110)
        lab[:, :, 2] = np.where(bright, 160, 150)
        return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    result = list(colour_frames(frames, colourise, interval=12))
    # Smooth flow drags the square's colour onto the static ground around it, worst halfway
    # between key frames. Allow a fringe of about two pixels along the square's 28-pixel edges.
    for frame, image in zip(frames, result, strict=True):
        ground = cv2.erode((frame[:, :, 0] < 128).astype(np.uint8), np.ones((3, 3), np.uint8))
        painted = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)[:, :, 1] > 130
        assert (painted & (ground > 0)).sum() < 100


def test_cut_inside_a_moving_shot_takes_a_fresh_guess():
    frames, _ = _scene(18)
    rng = np.random.default_rng(11)
    other = np.repeat(_texture(rng, (96, 128), 150, 40)[:, :, None], 3, axis=2)
    colourise = _ChangingColouriser()
    result = list(colour_frames(frames + [other] * 6, colourise, interval=12))
    # Keys: frame 0, frame 12, then the cut at frame 18 takes the third guess.
    assert colourise.calls == 3
    after_cut = _chroma(result[19], slice(10, 86), slice(10, 118))
    assert np.linalg.norm(after_cut - _hue(-20, 40)) < 6


def test_carried_colour_error_stays_bounded_in_a_long_shot():
    # The same textured view sways left and right for 360 frames (30 key frames). Every key
    # frame resamples the carried colour, and flow is slightly off at sub-pixel steps; without
    # the share of each new guess the error against an always-right colouriser keeps growing.
    rng = np.random.default_rng(5)
    layers = []
    for sigma in (2.5, 8):
        layer = cv2.GaussianBlur(rng.integers(0, 256, (120, 220)).astype(np.float32), (0, 0), sigma)
        layers.append((layer - layer.min()) / (layer.max() - layer.min()))
    base = ((0.6 * layers[0] + 0.4 * layers[1]) * 200 + 25).astype(np.uint8)
    frames = []
    for index in range(361):
        shift = 20 + 18 * np.sin(index / 9.0)
        moved = cv2.warpAffine(
            base,
            np.float32([[1, 0, -shift], [0, 1, -0.4 * shift]]),
            (220, 120),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
        frames.append(np.repeat(moved[10:106, 40:168, None], 3, axis=2))

    def colourise(frame):
        lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
        smooth = cv2.GaussianBlur(lab[:, :, 0].astype(np.float32), (0, 0), 6)
        share = np.clip((smooth - 100) / 60, 0, 1)
        lab[:, :, 1] = (100 + 60 * share).astype(np.uint8)
        lab[:, :, 2] = (160 - 10 * share).astype(np.uint8)
        return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    result = list(colour_frames(frames, colourise, interval=12))

    def error(index):
        actual = cv2.cvtColor(result[index], cv2.COLOR_RGB2LAB)[10:-10, 10:-10, 1:]
        expected = cv2.cvtColor(colourise(frames[index]), cv2.COLOR_RGB2LAB)[10:-10, 10:-10, 1:]
        return np.abs(actual.astype(float) - expected).mean()

    assert error(360) < 1.6
