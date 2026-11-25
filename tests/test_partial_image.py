import numpy as np
import pytest
import torch

from localsr.core.partial_image import (
    fill_unfinished,
    partial_output_path,
    saved_tiles,
)


def test_fill_keeps_finished_tiles_and_matches_a_whole_resize_elsewhere():
    torch.manual_seed(0)
    source = torch.rand(3, 150, 70)
    scale = 4
    output = np.full((3, 600, 280), 7, dtype=np.uint8)
    finished = [(0, 0, 128, 128), (128, 256, 64, 64)]
    fill_unfinished(output, finished, source)
    for x, y, w, h in finished:
        assert (output[:, y : y + h, x : x + w] == 7).all()
    whole = (
        torch.nn.functional.interpolate(
            source.unsqueeze(0), scale_factor=(scale, scale), mode="bilinear", align_corners=False
        )[0]
        .clamp(0, 1)
        .mul(255)
        .round()
        .byte()
        .numpy()
    )
    mask = np.zeros((600, 280), dtype=bool)
    for x, y, w, h in finished:
        mask[y : y + h, x : x + w] = True
    # Banded and whole resizes agree everywhere outside the finished tiles: no seams.
    assert np.array_equal(output[:, ~mask], whole[:, ~mask])


def test_fill_refuses_an_output_that_is_not_a_whole_multiple():
    with pytest.raises(ValueError):
        fill_unfinished(np.zeros((3, 10, 10), dtype=np.uint8), [], torch.rand(3, 4, 4))


def test_saved_tiles_follow_a_smaller_output_scale():
    assert saved_tiles([(0, 0, 256, 256), (256, 0, 128, 256)], 4, 2) == [
        [0, 0, 128, 128],
        [128, 0, 64, 128],
    ]


def test_partial_names_never_replace_a_file(tmp_path):
    requested = tmp_path / "photo_upscaled_4x.png"
    first = partial_output_path(str(requested))
    assert first.endswith("photo_upscaled_4x_partial.png")
    (tmp_path / "photo_upscaled_4x_partial.png").write_bytes(b"x")
    assert partial_output_path(str(requested)).endswith("photo_upscaled_4x_partial-2.png")
