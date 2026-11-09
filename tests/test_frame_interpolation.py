"""Frame interpolation timing: output grids, scene cuts, duplicates and bounded streaming."""

from __future__ import annotations

import itertools
import threading
from fractions import Fraction

import numpy as np
import pytest
import torch
import torch.nn.functional as F

from localsr.core.frame_interpolation import (
    InterpolationTarget,
    frame_signature,
    interpolate_frames,
    is_duplicate,
    is_scene_cut,
)
from localsr.core.video_io import TimedVideoFrame

HEIGHT, WIDTH = 36, 64


def pattern(seed: int, shift: int = 0) -> np.ndarray:
    """A smooth natural-looking texture; ``shift`` moves it sideways (motion)."""
    generator = np.random.default_rng(seed)
    grid = torch.from_numpy(generator.random((1, 3, 8, 16), dtype=np.float32))
    smooth = F.interpolate(grid, size=(HEIGHT, WIDTH + 32), mode="bicubic", align_corners=False)
    pixels = (smooth.clamp(0, 1)[0].permute(1, 2, 0).numpy() * 255).round().astype(np.uint8)
    return np.ascontiguousarray(pixels[:, shift : shift + WIDTH])


def timed(frames, times, time_base=Fraction(1, 1000), last_duration=None):
    """TimedVideoFrames with exact timestamps in ``time_base`` units."""
    stamps = [Fraction(stamp) * time_base for stamp in times]
    result = []
    for index, (rgb, stamp) in enumerate(zip(frames, stamps, strict=True)):
        if index + 1 < len(stamps):
            duration = stamps[index + 1] - stamp
        else:
            duration = (
                Fraction(last_duration) * time_base
                if last_duration is not None
                else stamps[-1] - stamps[-2]
            )
        result.append(TimedVideoFrame(index, rgb, stamp, duration, time_base))
    return result


class RecordingSynthesizer:
    """Linear blends, with every request recorded for assertions."""

    def __init__(self):
        self.calls: list[tuple[int, int, tuple[float, ...]]] = []

    def synthesize(self, first, second, timesteps, cancel_event=None):
        self.calls.append((int(first[0, 0, 0]), int(second[0, 0, 0]), tuple(timesteps)))
        return [
            np.rint(first * (1 - step) + second.astype(np.float64) * step).astype(np.uint8)
            for step in timesteps
        ]


def run(frames, target, synthesizer=None, **options):
    synthesizer = synthesizer or RecordingSynthesizer()
    output = list(
        interpolate_frames(
            iter(frames),
            InterpolationTarget.parse(target),
            synthesizer,
            threading.Event(),
            **options,
        )
    )
    return output, synthesizer


def flat(values):
    """Frames of one grey level. Steps of 24-40 levels above 100 are neither duplicates nor cuts;
    a jump from black would count as a cut, as in Practical-RIFE."""
    return [np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8) for value in values]


def moving(count, step=2):
    return [pattern(7, shift=index * step) for index in range(count)]


def test_targets_parse_and_reject_unknown_names():
    assert InterpolationTarget.parse("off") is None
    assert InterpolationTarget.parse("") is None
    assert InterpolationTarget.parse(None) is None
    assert InterpolationTarget.parse("x2").multiplier == 2
    assert InterpolationTarget.parse("x4").multiplier == 4
    assert InterpolationTarget.parse("fps60").frame_rate == Fraction(60)
    assert InterpolationTarget.parse("fps120").frame_rate == Fraction(120)
    with pytest.raises(ValueError, match="frame interpolation"):
        InterpolationTarget.parse("x3")


@pytest.mark.parametrize(
    ("target", "source", "expected"),
    [
        ("x2", Fraction(1, 1000), Fraction(1, 2000)),
        ("x4", Fraction(1, 15360), Fraction(1, 61440)),
        ("fps60", Fraction(1, 15360), Fraction(1, 15360)),
        ("fps60", Fraction(1, 1000), Fraction(1, 3000)),
        ("fps120", Fraction(1001, 30000), Fraction(1, 30000)),
    ],
)
def test_output_time_base_represents_every_output_time_exactly(target, source, expected):
    assert InterpolationTarget.parse(target).output_time_base(source) == expected


def test_output_rate_and_expected_frame_counts():
    double = InterpolationTarget.parse("x2")
    sixty = InterpolationTarget.parse("fps60")
    assert double.output_fps(29.97) == pytest.approx(59.94)
    assert sixty.output_fps(23.976) == 60
    assert double.expected_frames(10, 30.0) == 19
    assert InterpolationTarget.parse("x4").expected_frames(10, 30.0) == 37
    assert sixty.expected_frames(24, 24.0) == 60
    assert double.expected_frames(0, 30.0) == 0


def test_frame_rate_targets_apply_only_above_the_source_rate():
    sixty = InterpolationTarget.parse("fps60")
    assert sixty.applies_to(29.97)
    assert sixty.applies_to(50.0)
    assert not sixty.applies_to(59.94)
    assert not sixty.applies_to(60.0)
    assert not sixty.applies_to(120.0)
    assert InterpolationTarget.parse("x2").applies_to(240.0)


def test_doubling_keeps_every_source_frame_and_halves_each_interval():
    frames = timed(flat([100, 140, 180, 220]), [0, 100, 200, 300])
    output, synthesizer = run(frames, "x2")
    times = [frame.timestamp for frame in output]
    assert times == [Fraction(stamp, 1000) for stamp in (0, 50, 100, 150, 200, 250, 300)]
    assert [frame.duration for frame in output] == [Fraction(1, 20)] * 6 + [Fraction(1, 10)]
    assert {frame.time_base for frame in output} == {Fraction(1, 2000)}
    assert [int(frame.rgb[0, 0, 0]) for frame in output] == [100, 120, 140, 160, 180, 200, 220]
    for source in frames:
        match = next(frame for frame in output if frame.timestamp == source.timestamp)
        np.testing.assert_array_equal(match.rgb, source.rgb)
    assert synthesizer.calls == [(100, 140, (0.5,)), (140, 180, (0.5,)), (180, 220, (0.5,))]
    assert [frame.index for frame in output] == list(range(7))


def test_variable_frame_rate_intervals_are_subdivided_where_they_are():
    frames = timed(flat([100, 140, 180, 220]), [0, 40, 400, 440])
    output, _ = run(frames, "x4")
    times = [int(frame.timestamp * 1000) for frame in output]
    assert times == [0, 10, 20, 30, 40, 130, 220, 310, 400, 410, 420, 430, 440]
    assert output[-1].duration == Fraction(40, 1000)
    end = output[-1].timestamp + output[-1].duration
    assert end == frames[-1].timestamp + frames[-1].duration


def test_frame_rate_conversion_resamples_onto_a_constant_grid():
    # 24 fps to 60 fps: every output time is an exact multiple of 1/60.
    frames = timed(flat([100, 124, 148, 172]), [0, 1, 2, 3], time_base=Fraction(1, 24))
    output, synthesizer = run(frames, "fps60")
    assert [frame.timestamp for frame in output] == [Fraction(index, 60) for index in range(10)]
    assert all(frame.duration == Fraction(1, 60) for frame in output)
    assert output[-1].timestamp + output[-1].duration == Fraction(4, 24)
    # 1/60 s lies 0.4 of the way from frame 0 (0 s) to frame 1 (1/24 s).
    assert synthesizer.calls[0] == (100, 124, pytest.approx((0.4, 0.8)))
    assert [int(frame.rgb[0, 0, 0]) for frame in output[:3]] == [100, 110, 119]
    # Beyond the last source frame the picture holds; nothing is extrapolated.
    assert [int(frame.rgb[0, 0, 0]) for frame in output[-2:]] == [172, 172]


def test_frame_rate_conversion_offsets_follow_the_first_source_timestamp():
    frames = timed(flat([100, 130, 160]), [1001, 2002, 3003], time_base=Fraction(1, 30000))
    output, _ = run(frames, "fps60")
    origin = Fraction(1001, 30000)
    assert [frame.timestamp - origin for frame in output] == [
        Fraction(index, 60) for index in range(len(output))
    ]
    assert output[-1].timestamp + output[-1].duration == Fraction(4004, 30000)


def test_no_frame_is_blended_across_a_scene_cut():
    first, second = pattern(1), pattern(2)
    frames = timed([first, first.copy(), second, second.copy()], [0, 100, 200, 300])
    # Make the repeated frames distinct enough not to count as duplicates.
    frames = [
        frame.with_pixels(np.clip(frame.rgb.astype(int) + index * 12, 0, 255).astype(np.uint8))
        for index, frame in enumerate(frames)
    ]
    output, synthesizer = run(frames, "x2")
    at_cut = next(frame for frame in output if frame.timestamp == Fraction(150, 1000))
    np.testing.assert_array_equal(at_cut.rgb, frames[1].rgb)
    blended_pairs = [(call[0], call[1]) for call in synthesizer.calls]
    assert (int(frames[1].rgb[0, 0, 0]), int(frames[2].rgb[0, 0, 0])) not in blended_pairs
    assert len(output) == 7


def test_animation_on_twos_interpolates_across_the_repeated_drawing():
    a, b, c = moving(3, step=3)
    frames = timed([a, a.copy(), b, b.copy(), c], [0, 100, 200, 300, 400])
    output, synthesizer = run(frames, "x2")
    assert [int(frame.timestamp * 1000) for frame in output] == list(range(0, 401, 50))
    # The repeat at 100 ms is replaced by the in-between drawing, so motion is even.
    assert [call[2] for call in synthesizer.calls] == [(0.25, 0.5, 0.75)] * 2
    np.testing.assert_array_equal(output[0].rgb, a)
    np.testing.assert_array_equal(output[4].rgb, b)
    np.testing.assert_array_equal(output[-1].rgb, c)


def test_long_static_runs_hold_and_interpolate_only_the_last_interval():
    still, moved = pattern(5), pattern(5, shift=2)
    frames = timed([still] * 6 + [moved], [0, 100, 200, 300, 400, 500, 600])
    output, synthesizer = run(frames, "x2", max_duplicate_run=2)
    assert len(output) == 13
    assert [call[2] for call in synthesizer.calls] == [(0.5,)]
    for frame in output[:11]:
        np.testing.assert_array_equal(frame.rgb, still)


def test_interpolation_streams_without_reading_ahead():
    consumed = []

    def endless():
        for index in itertools.count():
            consumed.append(index)
            rgb = pattern(9, shift=index % 8)
            stamp = Fraction(index, 25)
            yield TimedVideoFrame(index, rgb, stamp, Fraction(1, 25), Fraction(1, 25))

    stream = interpolate_frames(
        endless(),
        InterpolationTarget.parse("x2"),
        RecordingSynthesizer(),
        threading.Event(),
    )
    first = list(itertools.islice(stream, 5))
    assert len(first) == 5
    assert max(consumed) <= 4
    stream.close()


def test_cancellation_stops_between_frames():
    cancel = threading.Event()
    stream = interpolate_frames(
        iter(timed(flat([100, 140, 180]), [0, 100, 200])),
        InterpolationTarget.parse("x2"),
        RecordingSynthesizer(),
        cancel,
    )
    next(stream)
    cancel.set()
    with pytest.raises(InterruptedError):
        list(stream)


def test_single_frame_and_empty_streams():
    assert run([], "x2")[0] == []
    output, synthesizer = run(timed(flat([7]), [0], last_duration=40), "x4")
    assert [(frame.timestamp, frame.duration) for frame in output] == [
        (Fraction(0), Fraction(40, 1000))
    ]
    assert synthesizer.calls == []


def test_duplicate_detection_tolerates_noise_but_sees_small_motion():
    generator = np.random.default_rng(3)
    base = pattern(11)
    noisy = np.clip(base.astype(int) + generator.integers(-2, 3, base.shape), 0, 255)
    assert is_duplicate(frame_signature(base), frame_signature(noisy.astype(np.uint8)))
    moved = base.copy()
    moved[8:16, 8:16] = 255 - moved[8:16, 8:16]
    assert not is_duplicate(frame_signature(base), frame_signature(moved))
    assert not is_duplicate(frame_signature(base), frame_signature(pattern(11, shift=1)))


def test_scene_cut_detection_separates_cuts_from_motion():
    assert is_scene_cut(frame_signature(pattern(1)), frame_signature(pattern(2)))
    assert not is_scene_cut(frame_signature(pattern(1)), frame_signature(pattern(1, shift=2)))
    assert not is_scene_cut(frame_signature(pattern(1)), frame_signature(pattern(1)))


def test_signatures_are_small_for_large_frames():
    signature = frame_signature(np.zeros((2160, 3840, 3), dtype=np.uint8))
    assert signature.fine.size <= 3 * 160 * 90
    assert signature.coarse.shape == (32, 32)


def test_large_gap_synthesizes_in_bounded_batches():
    frames = timed(flat([100, 140]), [0, 10000], last_duration=1)
    # Even the first output should not require synthesizing the whole gap.
    synth = RecordingSynthesizer()
    stream = interpolate_frames(iter(frames), InterpolationTarget.parse("fps120"), synth, None)
    next(stream)
    assert not synth.calls or len(synth.calls[0][2]) <= 16


def test_equal_luma_colours_are_not_duplicates():
    red = np.full((HEIGHT, WIDTH, 3), (255, 0, 0), dtype=np.uint8)
    green = np.full((HEIGHT, WIDTH, 3), (0, 130, 0), dtype=np.uint8)
    blue = np.full((HEIGHT, WIDTH, 3), (0, 0, 255), dtype=np.uint8)
    output, _ = run(timed([red, green, blue], [0, 100, 200]), "x2")
    np.testing.assert_array_equal(output[2].rgb, green)


def test_short_vfr_tail_ends_at_source_end():
    frames = timed(flat([100, 140]), [0, 20], last_duration=1)
    output, _ = run(frames, "fps60")
    assert output[-1].timestamp + output[-1].duration == Fraction(21, 1000)


def test_large_duplicate_run_has_contiguous_batch_durations():
    a, b = moving(2)
    frames = timed([a] * 6 + [b], list(range(0, 700, 100)))
    output, _ = run(frames, "x4", max_duplicate_run=6)
    assert all(a.timestamp + a.duration == b.timestamp for a, b in itertools.pairwise(output))
