from localsr.core.tiling import generate_tiles


def test_tiling_exact_coverage():
    w, h = 500, 500
    tile_size = 256
    halo = 32
    scale = 2

    tiles = list(generate_tiles(w, h, tile_size, halo, scale))

    # 500/256 = 2 tiles width, 2 tiles height -> 4 tiles total
    assert len(tiles) == 4

    # Check outputs cover exactly the scaled bounds
    import numpy as np

    canvas = np.zeros((h * scale, w * scale))
    for t in tiles:
        canvas[t.out_y : t.out_y + t.out_h, t.out_x : t.out_x + t.out_w] += 1

    assert np.all(canvas == 1), "Every pixel should be written exactly once."


def test_tiling_dimensions():
    # Odd dimensions
    w, h = 51, 107
    tile_size = 32
    halo = 16
    scale = 4

    tiles = list(generate_tiles(w, h, tile_size, halo, scale))

    import numpy as np

    canvas = np.zeros((h * scale, w * scale))
    for t in tiles:
        canvas[t.out_y : t.out_y + t.out_h, t.out_x : t.out_x + t.out_w] += 1

    assert np.all(canvas == 1), "Every pixel should be written exactly once."
