"""Bounded, shot-aware chroma propagation for colourising video.

A shot's colour is decided once. The first key frame takes the colouriser's guess; every later key
frame keeps the colour carried along the motion wherever tracking held, so a surface keeps its
colour when the colouriser guesses differently. The new guess fills only what could not be tracked
(newly revealed background, new objects), shifted toward the carried colour of the surfaces it paints
alike. The brightness each colour was decided on travels with it, so drift that adds up over many
key frames is still caught, and a small share of each guess enters tracked areas, so resampling
blur fades instead of piling up. A cut starts over with a fresh guess.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

import cv2
import numpy as np

# Share of the carried colour a key frame keeps where tracking held. The rest comes from the new
# guess, shifted onto the carried colours, which only renews detail and lets old errors fade.
CARRIED_SHARE = 0.8
# Brightness difference (Lab L, 0-255, after a 3 px blur) between a carried colour's origin and
# where it lands. Within a moving surface shading changes it by up to about the first value; colour
# that slid onto another surface usually differs by more than the second. In between, the carried
# colour and the new guess are mixed.
_LUMA_TOLERANCE = (20.0, 45.0)
# Chroma bin width (Lab a/b, 0-255): guesses closer than this are treated as the same surface.
_CHROMA_BIN = 16.0


def _small(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = min(1.0, 512 / max(height, width))
    size = (max(8, round(width * scale)), max(8, round(height * scale)))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


def _luma(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(_small(frame), cv2.COLOR_RGB2LAB)[:, :, 0]


def _cut_luma(a: np.ndarray, b: np.ndarray) -> bool:
    difference = np.abs(a.astype(np.float32) - b.astype(np.float32)).mean()
    hist_a = cv2.calcHist([a], [0], None, [32], [0, 256])
    hist_b = cv2.calcHist([b], [0], None, [32], [0, 256])
    hist_a /= a.size
    hist_b /= b.size
    return bool(difference > 90 or (difference > 35 and np.abs(hist_a - hist_b).sum() > 0.6))


def _flow(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST).calc(a, b, None)


def _warp(image: np.ndarray, backward: np.ndarray) -> np.ndarray:
    height, width = backward.shape[:2]
    x, y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    return cv2.remap(
        image,
        x + backward[:, :, 0],
        y + backward[:, :, 1],
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _round_trip(forward: np.ndarray, backward: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How far each pixel lands from itself following `backward` then `forward`, and whether
    `backward` leads out of the frame."""
    error = np.linalg.norm(backward + _warp(forward, backward), axis=2)
    height, width = backward.shape[:2]
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    target_x, target_y = x + backward[:, :, 0], y + backward[:, :, 1]
    outside = (target_x < 0) | (target_x > width - 1) | (target_y < 0) | (target_y > height - 1)
    return error, outside


def _inconsistent(forward: np.ndarray, backward: np.ndarray) -> np.ndarray:
    error, outside = _round_trip(forward, backward)
    return outside | (error > (1.5 + 0.05 * np.linalg.norm(backward, axis=2)))


def _backward_share(
    weight: float, forward_invalid: np.ndarray, backward_invalid: np.ndarray
) -> np.ndarray:
    """Per-pixel share of the later key frame's chroma between two key frames.

    Where both directions track it is the temporal weight; where one broke (a surface revealed or
    hidden within the segment) the other direction is used; where both broke, the nearer key frame.
    """
    forward_ok = 1 - cv2.GaussianBlur(forward_invalid.astype(np.float32), (5, 5), 0)
    backward_ok = 1 - cv2.GaussianBlur(backward_invalid.astype(np.float32), (5, 5), 0)
    ahead = (1 - weight) * forward_ok
    back = weight * backward_ok
    tracked = np.maximum(forward_ok, backward_ok)
    nearest = 0.0 if weight <= 0.5 else 1.0
    return tracked * (back / np.maximum(ahead + back, 1e-6)) + (1 - tracked) * nearest


def _blend_chroma(
    forward: np.ndarray,
    backward: np.ndarray,
    weight: float,
    invalid: np.ndarray,
    backward_invalid: np.ndarray | None = None,
) -> np.ndarray:
    """Blend chroma carried from both key frames, trusting only the directions that tracked.

    `invalid` marks broken forward tracking, and backward tracking too unless `backward_invalid`
    is given.
    """
    backward_invalid = invalid if backward_invalid is None else backward_invalid
    share = _backward_share(weight, invalid, backward_invalid)[:, :, None]
    return forward + (backward - forward) * share


def _blur_luma(luma: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(luma.astype(np.float32), (0, 0), 3)


def _track(
    steps: Iterable[tuple[np.ndarray, np.ndarray]], lumas: list[np.ndarray]
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Map every frame of a chain back to its first frame and mark where tracking broke.

    Each step holds the flow from the previous frame to the next and the flow back. Tracking breaks
    where the two disagree, and where the brightness carried from the first frame no longer matches
    the frame: smooth flow can agree with itself and still drag a surface's colour onto its
    surroundings.
    """
    first = _blur_luma(lumas[0])
    maps = [np.zeros((*first.shape, 2), dtype=np.float32)]
    broken = [np.zeros(first.shape, dtype=bool)]
    for (ahead, back), luma in zip(steps, lumas[1:], strict=True):
        maps.append(back + _warp(maps[-1], back))
        carried = _warp(broken[-1].astype(np.float32), back) > 0.01
        slid = np.abs(_warp(first, maps[-1]) - _blur_luma(luma)) > _LUMA_TOLERANCE[0]
        broken.append(carried | slid | _inconsistent(ahead, back))
    return maps, broken


def _lowpass(image: np.ndarray, sigma: float) -> np.ndarray:
    """A wide Gaussian blur, computed at a quarter of the size."""
    height, width = image.shape[:2]
    small = cv2.resize(
        image, (max(1, round(width / 4)), max(1, round(height / 4))), interpolation=cv2.INTER_AREA
    )
    small = cv2.GaussianBlur(small, (0, 0), max(0.5, sigma / 4))
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR).reshape(image.shape)


def _harmonise(guess: np.ndarray, difference: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """Spread `difference` from weighted pixels to pixels the guess colours alike.

    Chroma bins with tent weights make this a coarse bilateral filter: the parts of a surface the
    guess paints in one colour get that surface's correction, other surfaces their own. Nearby
    evidence counts most; without it a pixel takes its colour's average over the frame, and a colour
    with no weighted pixel at all (a new kind of object) keeps the guess.
    """
    sigma = max(weight.shape) / 8
    bins = guess / _CHROMA_BIN
    corners = np.unique(np.floor(bins).astype(np.int32).reshape(-1, 2), axis=0)
    cells = {(int(a) + da, int(b) + db) for a, b in corners for da in (0, 1) for db in (0, 1)}
    offset = np.zeros_like(difference)
    for cell_a, cell_b in sorted(cells):
        member = np.maximum(0, 1 - np.abs(bins[:, :, 0] - cell_a)) * np.maximum(
            0, 1 - np.abs(bins[:, :, 1] - cell_b)
        )
        support = weight * member
        total = float(support.sum())
        if total < 1:
            continue
        average = (support[:, :, None] * difference).sum(axis=(0, 1)) / total
        local = _lowpass(support[:, :, None] * difference, sigma)
        density = _lowpass(support, sigma)[:, :, None]
        offset += member[:, :, None] * (local + 0.05 * average) / (density + 0.05)
    return offset


def _anchor(
    first: np.ndarray,
    first_luma: np.ndarray,
    back_map: np.ndarray,
    ahead_map: np.ndarray,
    luma: np.ndarray,
    guess: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Chroma of a key frame inside a shot: the carried colour where tracking held, else the guess.

    `first` is the previous key frame's chroma and `first_luma` the brightness that chroma was
    decided on, carried along with it. `back_map` maps the key frame back to the previous key
    frame and `ahead_map` the other way. The carried colour counts where its origin is inside the
    frame, the two maps round-trip (loosely: composed steps drift on a moving, deforming surface;
    occlusions fail by far more) and the brightness it was decided on still matches (see
    `_LUMA_TOLERANCE`), which also catches slow drift summed over many key frames. Elsewhere the
    guess is shifted toward the carried colour of what it paints alike, so revealed parts of a
    known surface join it without a seam. Returns the chroma and the brightness it now stands for.
    """
    carried = _warp(first, back_map)
    carried_luma = _warp(first_luma, back_map)
    low, high = _LUMA_TOLERANCE
    difference = np.abs(_blur_luma(carried_luma) - _blur_luma(luma))
    trust = np.clip((high - difference) / (high - low), 0, 1)
    error, outside = _round_trip(ahead_map, back_map)
    trust[outside | (error > 3 + 0.15 * np.linalg.norm(back_map, axis=2))] = 0
    fresh = guess + _harmonise(guess, carried - guess, trust)
    keep = CARRIED_SHARE * cv2.GaussianBlur(trust, (5, 5), 0)
    chroma = np.clip(fresh + keep[:, :, None] * (carried - fresh), 0, 255).astype(np.float32)
    luma = luma.astype(np.float32)
    return chroma, luma + keep * (carried_luma - luma)


def _render_mapped(
    frame: np.ndarray,
    first: np.ndarray,
    forward_map: np.ndarray,
    last: np.ndarray | None,
    backward_map: np.ndarray | None,
    weight: float,
    invalid: np.ndarray | None,
    smoothing: np.ndarray,
    backward_invalid: np.ndarray | None = None,
) -> np.ndarray:
    """Upsample flow and sample keyframe chroma in strips to bound working memory.

    `invalid` and `backward_invalid` mark broken tracking as in `_blend_chroma`.
    """
    lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB)
    height, width = frame.shape[:2]
    small_height, small_width = first.shape[:2]
    x = (np.arange(width, dtype=np.float32) + 0.5) * small_width / width - 0.5
    share = None
    if last is not None and backward_map is not None and invalid is not None:
        share = _backward_share(
            weight, invalid, invalid if backward_invalid is None else backward_invalid
        )
    for top in range(0, height, 128):
        bottom = min(height, top + 128)
        y = (np.arange(top, bottom, dtype=np.float32) + 0.5) * small_height / height - 0.5
        grid_x, grid_y = np.meshgrid(x, y)

        def sample(
            image: np.ndarray,
            displacement: np.ndarray | None = None,
            grid_x: np.ndarray = grid_x,
            grid_y: np.ndarray = grid_y,
        ) -> np.ndarray:
            if displacement is not None:
                # Flow is interpolated at each full-resolution output pixel. Scaling
                # it to output pixels and then back to the keyframe grid cancels out.
                flow = cv2.remap(
                    displacement, grid_x, grid_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
                )
                sample_x, sample_y = grid_x + flow[:, :, 0], grid_y + flow[:, :, 1]
            else:
                sample_x, sample_y = grid_x, grid_y
            return cv2.remap(
                image, sample_x, sample_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
            )

        ab = sample(first, forward_map)
        if share is not None and last is not None and backward_map is not None:
            ab += (sample(last, backward_map) - ab) * sample(share)[:, :, None]
        ab += sample(smoothing)
        lab[top:bottom, :, 1:] = np.clip(ab, 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def colour_frames(
    frames: Iterable[np.ndarray],
    colourise: Callable[[np.ndarray], np.ndarray],
    interval: int = 12,
    inconsistent_threshold: float = 0.35,
) -> Iterator[np.ndarray]:
    """Colour frames using keyframes and bidirectional flow, buffering at most interval + 1.

    Within a shot each key frame keeps the colour carried from the previous one where tracking
    held and uses the colouriser's guess only for what could not be tracked (see the module notes).
    """
    if interval < 1:
        raise ValueError("Keyframe interval must be positive")
    buffered: list[np.ndarray] = []
    lumas: list[np.ndarray] = []
    forwards: list[np.ndarray] = []
    backwards: list[np.ndarray] = []
    first_ab: np.ndarray | None = None
    first_luma: np.ndarray | None = None
    smoothed_ab: np.ndarray | None = None
    smoothed_luma: np.ndarray | None = None

    def key_ab(frame: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(colourise(_small(frame)), cv2.COLOR_RGB2LAB)[:, :, 1:].astype(
            np.float32
        )

    def track() -> tuple[list[np.ndarray], list[np.ndarray]]:
        return _track(zip(forwards, backwards, strict=True), lumas)

    def track_back() -> tuple[list[np.ndarray], list[np.ndarray]]:
        maps, broken = _track(
            zip(reversed(backwards), reversed(forwards), strict=True), lumas[::-1]
        )
        return maps[::-1], broken[::-1]

    def flush(
        last_ab: np.ndarray | None,
        forward_track: tuple[list[np.ndarray], list[np.ndarray]],
        backward_track: tuple[list[np.ndarray], list[np.ndarray]] = ([], []),
    ) -> Iterator[np.ndarray]:
        nonlocal smoothed_ab, smoothed_luma
        assert first_ab is not None
        forward_maps, forward_invalid = forward_track
        backward_maps, backward_invalid = backward_track
        end = len(buffered) - 1 if last_ab is not None else len(buffered)
        for index in range(end):
            ab = _warp(first_ab, forward_maps[index])
            weight = 0.0
            blending = last_ab is not None and index > 0
            if blending:
                assert last_ab is not None
                weight = index / (len(buffered) - 1)
                ab = _blend_chroma(
                    ab,
                    _warp(last_ab, backward_maps[index]),
                    weight,
                    forward_invalid[index],
                    backward_invalid[index],
                )
            unsmoothed = ab
            if smoothed_ab is not None and smoothed_luma is not None:
                backward_flow = (
                    backwards[index - 1] if index else _flow(lumas[index], smoothed_luma)
                )
                forward_flow = forwards[index - 1] if index else _flow(smoothed_luma, lumas[index])
                valid = ~_inconsistent(forward_flow, backward_flow)
                previous = _warp(smoothed_ab, backward_flow)
                ab = np.where(valid[:, :, None], 0.85 * ab + 0.15 * previous, ab)
            smoothed_ab = ab
            smoothed_luma = lumas[index]
            yield _render_mapped(
                buffered[index],
                first_ab,
                forward_maps[index],
                last_ab if blending else None,
                backward_maps[index] if blending else None,
                weight,
                forward_invalid[index] if blending else None,
                ab - unsmoothed,
                backward_invalid[index] if blending else None,
            )

    for frame in frames:
        lum = _luma(frame)
        if not buffered:
            buffered.append(frame)
            lumas.append(lum)
            first_ab, first_luma = key_ab(frame), lum.astype(np.float32)
            continue
        if _cut_luma(lumas[-1], lum):
            yield from flush(None, track())
            smoothed_ab = None
            smoothed_luma = None
            buffered = [frame]
            lumas = [lum]
            forwards, backwards = [], []
            first_ab, first_luma = key_ab(frame), lum.astype(np.float32)
            continue
        forward = _flow(lumas[-1], lum)
        backward = _flow(lum, lumas[-1])
        forwards.append(forward)
        backwards.append(backward)
        buffered.append(frame)
        lumas.append(lum)
        if (
            len(buffered) - 1 >= interval
            or _inconsistent(forward, backward).mean() > inconsistent_threshold
        ):
            forward_track, backward_track = track(), track_back()
            assert first_ab is not None and first_luma is not None
            next_ab, next_luma = _anchor(
                first_ab,
                first_luma,
                forward_track[0][-1],
                backward_track[0][0],
                lum,
                key_ab(frame),
            )
            yield from flush(next_ab, forward_track, backward_track)
            buffered = [frame]
            lumas = [lum]
            forwards, backwards = [], []
            first_ab, first_luma = next_ab, next_luma
    if buffered:
        yield from flush(None, track())
