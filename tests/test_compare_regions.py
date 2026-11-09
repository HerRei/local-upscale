import base64
import io

from PIL import Image

from localsr.worker.compare_regions import RegionCache


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
