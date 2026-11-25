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
