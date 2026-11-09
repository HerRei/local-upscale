import base64
import io
import sys
import threading

import numpy as np
import pytest
from PIL import Image

from localsr.worker.compare_regions import RegionCache, RegionService


def test_regions_use_source_pixels_and_reuse_decode(tmp_path, monkeypatch):
    path = tmp_path / "source.png"
    image = Image.new("RGB", (8, 4), "red")
    for x in range(4, 8):
        for y in range(4):
            image.putpixel((x, y), (0, 0, 255))
    image.save(path)
    cache = RegionCache(1024)
    data = {
        "image_path": str(path),
        "x": 4,
        "y": 0,
        "width": 4,
        "height": 4,
        "target_width": 4,
        "target_height": 4,
        "slot": "slot1",
        "generation": 2,
        "target_id": "job",
        "request_id": 3,
    }
    first = cache.render(data)
    assert first.request_id == 3
    with Image.open(io.BytesIO(base64.b64decode(first.png_base64))) as crop:
        assert crop.getpixel((0, 0)) == (0, 0, 255)
    monkeypatch.setattr(
        Image, "open", lambda *_: (_ for _ in ()).throw(AssertionError("decoded twice"))
    )
    assert cache.render(data).request_id == 3
    cache.clear()
    assert not cache.images


def test_cache_evicts_lru_and_rejects_oversized_or_invalid_regions(tmp_path):
    paths = [tmp_path / f"{i}.png" for i in range(3)]
    for path in paths:
        Image.new("RGB", (4, 4)).save(path)
    cache = RegionCache(128)
    request = {
        "x": 0,
        "y": 0,
        "width": 4,
        "height": 4,
        "target_width": 4,
        "target_height": 4,
        "slot": "slot1",
        "generation": 1,
        "target_id": "a",
        "request_id": 1,
    }
    for path in paths[:2]:
        cache.render({**request, "image_path": str(path)})
    cache.render({**request, "image_path": str(paths[0])})
    cache.render({**request, "image_path": str(paths[2])})
    assert [key[0] for key in cache.images] == [str(paths[0]), str(paths[2])]
    assert cache.render({**request, "image_path": str(paths[0]), "x": 4}).error_message
    assert RegionCache(10).render({**request, "image_path": str(paths[0])}).error_message


def test_clear_discards_inflight_answer_and_cached_image(tmp_path, monkeypatch):
    import queue
    import threading

    from localsr.worker.compare_regions import RegionService

    path = tmp_path / "source.png"
    Image.new("RGB", (4, 4)).save(path)
    emitted = queue.Queue()
    service = RegionService(emitted.put)
    entered, release = threading.Event(), threading.Event()
    original = service.cache.render

    def blocked(data):
        entered.set()
        assert release.wait(5)
        return original(data)

    monkeypatch.setattr(service.cache, "render", blocked)
    try:
        service.submit(
            {
                "image_path": str(path),
                "slot": "slot1",
                "x": 0,
                "y": 0,
                "width": 4,
                "height": 4,
                "target_width": 4,
                "target_height": 4,
            }
        )
        assert entered.wait(5)
        service.clear()
        release.set()
        service.close()
        assert not service.cache.images
        assert emitted.empty()
    finally:
        release.set()
        service.close()


def test_region_coordinates_follow_exif_orientation(tmp_path):
    path = tmp_path / "rotated.png"
    image = Image.new("RGB", (4, 2), "red")
    image.putpixel((0, 1), (0, 0, 255))
    exif = image.getexif()
    exif[274] = 6
    image.save(path, exif=exif)
    result = RegionCache(1024).render(
        {
            "image_path": str(path),
            "x": 0,
            "y": 0,
            "width": 1,
            "height": 1,
            "target_width": 1,
            "target_height": 1,
        }
    )
    with Image.open(io.BytesIO(base64.b64decode(result.png_base64))) as crop:
        assert crop.getpixel((0, 0)) == (0, 0, 255)


def _region(path, slot="slot1"):
    return {
        "image_path": str(path),
        "x": 0,
        "y": 0,
        "width": 2,
        "height": 2,
        "target_width": 2,
        "target_height": 2,
        "slot": slot,
        "generation": 1,
        "target_id": slot,
        "request_id": 1,
    }


def test_an_image_above_the_cache_budget_is_cropped_without_being_kept(tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (20, 20), "red").save(path)  # 1600 bytes in memory
    cache = RegionCache(1000)
    assert cache.render(_region(path)).png_base64
    assert not cache.images and cache.bytes == 0
    path2 = tmp_path / "huge.png"
    Image.new("RGB", (40, 40), "red").save(path2)  # above twice the budget
    assert cache.render(_region(path2)).error_message


def test_a_slot_resubmitted_while_others_wait_does_not_starve_them(tmp_path):
    started, release = threading.Event(), threading.Event()
    order = []
    done = threading.Event()
    service = RegionService(lambda result: None)

    def render(data):
        order.append(data["slot"])
        if len(order) == 1:
            started.set()
            release.wait(5)
        elif len(order) <= 3:
            service.submit(_region("x", "slot4"))
        if len(order) >= 4:
            done.set()
        return object()

    service.cache.render = render
    try:
        service.submit(_region("x", "slot1"))
        assert started.wait(5)
        for slot in ("slot2", "slot3", "slot4"):
            service.submit(_region("x", slot))
        release.set()
        assert done.wait(5)
        assert order[1:3] == ["slot2", "slot3"]
    finally:
        service.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="HEIC is decoded by macOS")
def test_regions_cut_from_a_heic_export(tmp_path):
    from localsr.core.image_io import ImageManager

    path = tmp_path / "finished.heic"
    pixels = np.zeros((3, 32, 32), dtype=np.uint8)
    pixels[0, :, 16:] = 255
    ImageManager().save_from_writer(pixels, str(path), "heic", 95, True, None, {}, 1)
    result = RegionCache(1 << 20).render(
        {"image_path": str(path), "x": 16, "y": 0, "width": 16, "height": 16}
        | {"target_width": 16, "target_height": 16, "slot": "slot1", "request_id": 1}
    )
    with Image.open(io.BytesIO(base64.b64decode(result.png_base64))) as crop:
        assert crop.getpixel((8, 8))[0] > 200
