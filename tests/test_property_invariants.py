"""Small, reproducible properties for timing, colour and tile geometry."""

from __future__ import annotations

import math
import tempfile
import threading
from fractions import Fraction

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image, ImageCms

from localsr.core.frame_interpolation import InterpolationTarget, interpolate_frames
from localsr.core.image_io import ImageManager, _narrow_wide_grey
from localsr.core.tiling import generate_tiles
from localsr.core.video_io import TimedVideoFrame


@settings(max_examples=45, derandomize=True, deadline=None)
@given(
    intervals=st.lists(
        st.one_of(st.sampled_from((1001, 1251, 1252, 2002, 2502)), st.integers(1001, 5000)),
        min_size=1,
        max_size=8,
    ),
    target=st.sampled_from(("x2", "x4", "fps60", "fps120")),
)
def test_interpolation_property_keeps_a_monotone_grid_and_source_duration(intervals, target):
    """Covers short tails and VFR timestamps around broadcast frame rates."""
    time_base = Fraction(1, 60000)
    stamps = [0]
    for interval in intervals:
        stamps.append(stamps[-1] + interval)
    frames = [
        TimedVideoFrame(
            index,
            np.full((2, 2, 3), 40 + index, dtype=np.uint8),
            Fraction(stamp, 60000),
            Fraction(
                (stamps[index + 1] - stamp) if index + 1 < len(stamps) else intervals[-1], 60000
            ),
            time_base,
        )
        for index, stamp in enumerate(stamps)
    ]

    class PointwiseSynthesizer:
        def synthesize(self, first, second, timesteps, cancel_event=None):
            return [
                np.rint(first * (1 - step) + second * step).astype(np.uint8) for step in timesteps
            ]

    output = list(
        interpolate_frames(
            iter(frames),
            InterpolationTarget.parse(target),
            PointwiseSynthesizer(),
            threading.Event(),
        )
    )
    times = [frame.timestamp for frame in output]
    end = frames[-1].timestamp + frames[-1].duration
    assert all(left < right for left, right in zip(times, times[1:], strict=False))
    assert all(stamp <= end for stamp in times)
    assert output[-1].timestamp + output[-1].duration == end
    if target.startswith("fps"):
        rate = int(target.removeprefix("fps"))
        assert len(output) == math.ceil((end - frames[0].timestamp) * rate)
        assert all(frame.duration == Fraction(1, rate) for frame in output[:-1])
        assert 0 < output[-1].duration <= Fraction(1, rate)
    else:
        multiplier = 2 if target == "x2" else 4
        assert len(output) == (len(frames) - 1) * multiplier + 1


@settings(max_examples=24, derandomize=True, deadline=None)
@given(
    before=st.lists(st.integers(0, 1), min_size=1, max_size=4),
    after=st.lists(st.integers(254, 255), min_size=1, max_size=4),
    intervals=st.lists(st.sampled_from((1001, 1251, 2002, 2502)), min_size=1, max_size=8),
)
def test_duplicate_runs_and_scene_cuts_preserve_the_interpolation_grid(before, after, intervals):
    values = before + after
    stamps = [0]
    durations = (intervals * math.ceil((len(values) - 1) / len(intervals)))[: len(values) - 1]
    for interval in durations:
        stamps.append(stamps[-1] + interval)
    time_base = Fraction(1, 60000)
    frames = [
        TimedVideoFrame(
            index,
            np.full((32, 32, 3), value, dtype=np.uint8),
            Fraction(stamp, 60000),
            Fraction(
                stamps[index + 1] - stamp if index + 1 < len(values) else intervals[-1], 60000
            ),
            time_base,
        )
        for index, (stamp, value) in enumerate(zip(stamps, values, strict=True))
    ]

    class RecordingSynthesizer:
        pairs = []

        def synthesize(self, first, second, timesteps, cancel_event=None):
            self.pairs.extend((int(first[0, 0, 0]), int(second[0, 0, 0])) for _ in timesteps)
            return [
                np.rint(first * (1 - step) + second * step).astype(np.uint8) for step in timesteps
            ]

    synthesizer = RecordingSynthesizer()
    output = list(
        interpolate_frames(
            iter(frames),
            InterpolationTarget.parse("x2"),
            synthesizer,
            threading.Event(),
        )
    )
    assert len(output) == (len(frames) - 1) * 2 + 1
    assert all(
        first.timestamp < second.timestamp
        for first, second in zip(output, output[1:], strict=False)
    )
    assert (before[-1], after[0]) not in synthesizer.pairs


@settings(max_examples=55, derandomize=True, deadline=None)
@given(
    bits=st.sampled_from((8, 12, 16)),
    samples=st.lists(st.integers(0, 65535), min_size=2, max_size=30),
)
def test_wide_grey_scaling_is_monotone_and_uses_the_full_range(bits, samples):
    maximum = (1 << bits) - 1
    values = np.sort(np.asarray([value % (maximum + 1) for value in samples], dtype=np.uint16))
    if bits == 8:
        image = Image.fromarray(values.astype(np.uint8).reshape(1, -1))
        scaled = np.asarray(image)
    else:
        image = Image.fromarray(values.reshape(1, -1))
        scaled = np.asarray(_narrow_wide_grey(image, bits))
    assert np.all(np.diff(scaled[0].astype(np.int16)) >= 0)
    endpoints = Image.fromarray(
        np.array([[0, maximum]], dtype=np.uint8 if bits == 8 else np.uint16)
    )
    endpoint_values = np.asarray(endpoints if bits == 8 else _narrow_wide_grey(endpoints, bits))
    assert int(endpoint_values[0, 0]) == 0
    assert int(endpoint_values[0, 1]) == 255


@pytest.mark.parametrize("mode", ("1", "L", "LA", "I;16", "I"))
def test_every_supported_grey_mode_loads_as_neutral_rgb(tmp_path, mode):
    if mode == "1":
        image = Image.new(mode, (4, 2))
    elif mode == "L":
        image = Image.fromarray(np.array([[0, 32, 96, 255]] * 2, dtype=np.uint8), mode)
    elif mode == "LA":
        pixels = np.zeros((2, 4, 2), dtype=np.uint8)
        pixels[..., 0] = [0, 32, 96, 255]
        pixels[..., 1] = 255
        image = Image.fromarray(pixels, mode)
    elif mode == "I;16":
        image = Image.fromarray(np.array([[0, 8192, 32768, 65535]] * 2, dtype=np.uint16))
    else:
        image = Image.fromarray(np.array([[0, 32, 96, 255]] * 2, dtype=np.int32), mode)
    path = tmp_path / f"grey-{mode.replace(';', '-')}.tiff"
    image.save(path)
    loaded = ImageManager().load(str(path))
    pixels = loaded["tensor"].numpy()
    np.testing.assert_array_equal(pixels[0], pixels[1])
    np.testing.assert_array_equal(pixels[1], pixels[2])


@settings(max_examples=25, derandomize=True, deadline=None)
@given(levels=st.lists(st.integers(0, 255), min_size=1, max_size=30))
def test_srgb_icc_conversion_keeps_neutral_grey_neutral(levels):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    pixels = np.asarray(levels, dtype=np.uint8).reshape(1, -1)
    image = Image.fromarray(np.repeat(pixels[..., None], 3, axis=2), "RGB")
    image.info["icc_profile"] = profile
    with tempfile.TemporaryDirectory() as directory:
        path = f"{directory}/neutral-with-profile.png"
        image.save(path, icc_profile=profile)
        loaded = ImageManager().load(path)["tensor"].numpy()
    np.testing.assert_allclose(loaded[0], loaded[1], atol=1 / 255)
    np.testing.assert_allclose(loaded[1], loaded[2], atol=1 / 255)


@settings(max_examples=45, derandomize=True, deadline=None)
@given(
    width=st.integers(1, 37),
    height=st.integers(1, 31),
    tile_size=st.integers(1, 13),
    halo=st.integers(0, 6),
    scale=st.integers(1, 4),
)
def test_tiled_identity_and_pointwise_models_match_untiled(width, height, tile_size, halo, scale):
    source = np.arange(width * height, dtype=np.int32).reshape(height, width)
    reference = np.repeat(np.repeat(source, scale, axis=0), scale, axis=1) * 3 + 7
    stitched = np.empty_like(reference)
    for tile in generate_tiles(width, height, tile_size, halo, scale):
        crop = source[
            tile.halo_y : tile.halo_y + tile.halo_h, tile.halo_x : tile.halo_x + tile.halo_w
        ]
        padded = np.pad(
            crop, ((tile.pad_top, tile.pad_bottom), (tile.pad_left, tile.pad_right)), mode="edge"
        )
        predicted = np.repeat(np.repeat(padded, scale, axis=0), scale, axis=1) * 3 + 7
        core_x = (tile.core_x - tile.halo_x + tile.pad_left) * scale
        core_y = (tile.core_y - tile.halo_y + tile.pad_top) * scale
        core = predicted[core_y : core_y + tile.out_h, core_x : core_x + tile.out_w]
        stitched[tile.out_y : tile.out_y + tile.out_h, tile.out_x : tile.out_x + tile.out_w] = core
    np.testing.assert_array_equal(stitched, reference)
