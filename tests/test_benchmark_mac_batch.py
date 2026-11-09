"""The batch benchmark's own logic: photos, accuracy, disk guard and summary."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

spec = importlib.util.spec_from_file_location(
    "benchmark_mac_batch", Path(__file__).parents[1] / "scripts" / "benchmark_mac_batch.py"
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_synthetic_photos_are_deterministic_jpegs_of_the_requested_size(tmp_path):
    first = benchmark.synthetic_photos(tmp_path / "a", 3, (64, 48))
    second = benchmark.synthetic_photos(tmp_path / "b", 3, (64, 48))
    assert [path.name for path in first] == ["00.jpg", "01.jpg", "02.jpg"]
    for left, right in zip(first, second, strict=True):
        assert left.read_bytes() == right.read_bytes()
        with Image.open(left) as photo:
            assert (photo.format, photo.size) == ("JPEG", (64, 48))
    # Each photo differs, so no two outputs can be confused.
    assert first[0].read_bytes() != first[1].read_bytes()


def test_psnr_is_measured_on_exported_pixels():
    reference = np.full((3, 4, 5), 100, dtype=np.uint8)
    assert benchmark.psnr(reference, reference.copy()) == float("inf")
    changed = reference.copy()
    changed[...] = 101
    assert benchmark.psnr(reference, changed) == pytest.approx(48.13, abs=0.01)
    with pytest.raises(ValueError, match="shape"):
        benchmark.psnr(reference, reference[:, :2])


def test_the_disk_guard_stops_below_the_limit(tmp_path, monkeypatch):
    free = [5 * 2**30]
    monkeypatch.setattr(benchmark.shutil, "disk_usage", lambda _path: SimpleNamespace(free=free[0]))
    guard = benchmark.DiskGuard(tmp_path, minimum_bytes=2 * 2**30)
    assert guard.ok()
    free[0] = 2**30
    assert not guard.ok()
    assert guard.lowest == 2**30


def test_summary_reports_the_median_speed_up_and_the_worst_accuracy():
    runs = [
        {"mode": "serial", "seconds_per_photo": 3.0, "psnr_vs_reference_db": [60.0, 58.0]},
        {"mode": "batch", "seconds_per_photo": 1.0, "psnr_vs_reference_db": [52.0, 51.0]},
        {"mode": "serial", "seconds_per_photo": 3.3, "psnr_vs_reference_db": [59.0, 58.5]},
        {"mode": "batch", "seconds_per_photo": 1.2, "psnr_vs_reference_db": [50.5, 51.5]},
    ]
    summary = benchmark.summarize(runs, gate_db=45.0)
    assert summary["seconds_per_photo"] == pytest.approx({"serial": 3.15, "batch": 1.1})
    assert summary["speed_up"] == pytest.approx(3.15 / 1.1)
    assert summary["min_psnr_vs_reference_db"] == {"serial": 58.0, "batch": 50.5}
    assert summary["accuracy_passes_gate"] is True
    runs[1]["psnr_vs_reference_db"] = [44.0]
    assert benchmark.summarize(runs, gate_db=45.0)["accuracy_passes_gate"] is False


def test_the_scratch_folder_must_be_new(tmp_path):
    with pytest.raises(SystemExit):
        benchmark.parse_arguments(["--model", str(tmp_path), "--scratch", str(tmp_path)])
