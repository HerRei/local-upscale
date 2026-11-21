import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "gpu_route_report", ROOT / "scripts/gpu_route_report.py"
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def test_metrics_measure_the_clamped_output_in_8_bit_steps():
    reference = np.zeros((1, 3, 4, 4), np.float32)
    candidate = reference.copy()
    candidate[0, 0, 0, 0] = 2 / 255
    result = report.metrics(candidate, reference)
    assert result["max_255"] == 2.0 and result["finite"]
    assert 55 < result["psnr"] < 62  # one pixel of 48 off by 2/255
    assert report.metrics(reference, reference)["psnr"] is None  # identical
    candidate[0, 0, 0, 1] = np.nan
    assert not report.metrics(candidate, reference)["finite"]


def test_tiles_stay_inside_small_images():
    for height, width, side in ((100, 80, 320), (4000, 3000, 320), (320, 320, 320)):
        for top, left in report._tiles(height, width, side):
            used = min(side, height, width)
            assert 0 <= top <= height - used and 0 <= left <= width - used
