"""A job needs room on its output's volume before it starts, and a full disk reads clearly."""

import errno
import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from localsr.core import disk_space
from localsr.core.disk_space import (
    DiskFullError,
    ImageNeed,
    Volume,
    disk_full_from,
    encoded_bound,
    image_need,
    require_room,
    reserve_for,
)
from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.pipeline import PipelineStage, PipelineStageError
from localsr.worker.image_job import ImageJobRunner

GB = 1_000_000_000


@pytest.fixture(autouse=True)
def the_real_reserve_rule_on_a_roomy_disk(monkeypatch):
    monkeypatch.delenv("LOCALSR_DISK_RESERVE_BYTES")
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda _path: shutil._ntuple_diskusage(10**15, 0, 10**15)
    )


def volume(free, total=500 * GB, system=True, device=1):
    return Volume(device=device, free=free, total=total, system=system)


# A phone photo at 4x: 12096 x 16128 = 195,084,288 pixels, three bytes each in the output memmap.
PHONE = dict(width=3024, height=4032, scale=4, output_scale=4, fmt="png")


def test_a_phone_photo_at_4x_png_needs_the_memmap_and_the_encoded_file():
    need = image_need(**PHONE)
    assert need == ImageNeed(scratch=585_252_864, encoded=588_603_712)
    assert need.total == 1_173_856_576
    # Two photos on the volume at once; the final name is a link to the encoded file.
    assert need.in_flight(2) == 2_347_713_152


def _batch_total(need, encoders):
    data = {"output_path": "/out/a.png", "scratch_directory": "/out/.localsr-job-x"}
    return sum(nbytes for _folder, nbytes in disk_space.image_claims(data, need, encoders))


def test_photo_batch_lanes_hold_two_more_memmaps_than_encoders():
    need = image_need(**PHONE)
    assert _batch_total(need, 2) == 4 * 585_252_864 + 2 * 588_603_712 == 3_518_218_880
    # The Neural Engine batch of SPAN runs six encoders.
    assert _batch_total(need, 6) == 8_213_645_184


def test_the_memmap_is_native_scale_and_the_encoded_file_is_the_saved_size():
    half = image_need(3024, 4032, scale=4, output_scale=2, fmt="png")
    assert half.scratch == 585_252_864
    assert half.encoded == encoded_bound("png", 6048, 8064)
    assert half.encoded < image_need(**PHONE).encoded


def test_every_format_has_its_own_bound_and_alpha_adds_to_the_ones_that_keep_it():
    width, height = 12096, 16128
    png = encoded_bound("png", width, height)
    assert png > 3 * width * height
    assert encoded_bound("tiff", width, height) == png == encoded_bound("tif", width, height)
    assert encoded_bound("png", width, height, alpha=True) > png
    lossy = {name: encoded_bound(name, width, height) for name in ("jpeg", "jpg", "webp", "heic")}
    assert lossy["jpg"] == lossy["jpeg"]
    assert lossy["heic"] < lossy["jpeg"] < png
    # JPEG has no alpha; the bound ignores it.
    assert encoded_bound("jpeg", width, height, alpha=True) == lossy["jpeg"]


@pytest.mark.parametrize(
    ("facts", "reserve"),
    [
        # The system volume keeps 8 GB free, whatever its size.
        (volume(0, total=500 * GB, system=True), 8 * GB),
        (volume(0, total=60 * GB, system=True), 8 * GB),
        (volume(0, total=100 * GB, system=True), 8 * GB),
        # Any other volume keeps 1 GB.
        (volume(0, total=4000 * GB, system=False), 1 * GB),
    ],
)
def test_the_reserve_protects_the_system_volume_more_than_others(facts, reserve):
    assert reserve_for(facts) == reserve


def test_a_job_fits_when_its_need_stays_inside_free_space_minus_the_reserve(tmp_path):
    need = 1_173_856_576
    free = need + 8 * GB
    require_room([(tmp_path, need)], lookup=lambda _folder: volume(free))
    with pytest.raises(DiskFullError):
        require_room([(tmp_path, need)], lookup=lambda _folder: volume(free - 1))
    # Elsewhere than on the system volume only 1 GB stays free.
    require_room([(tmp_path, need)], lookup=lambda _folder: volume(need + GB, system=False))


def test_the_refusal_is_one_sentence_naming_the_folder_the_need_and_what_is_free(tmp_path):
    folder = tmp_path / "Pictures"
    with pytest.raises(DiskFullError) as caught:
        require_room(
            [(folder / ".localsr-job-abc", 1_173_856_576)],
            lookup=lambda _folder: volume(4_700_000_000),
        )
    text = str(caught.value)
    assert text == (
        f'Not enough free disk space in "{folder}": about 1.2 GB is needed, but only 4.7 GB is '
        "free, and 8.0 GB must stay free so the computer keeps working."
    )
    assert "\n" not in text and "Errno" not in text


def test_files_on_one_volume_add_up_and_files_on_two_volumes_do_not(tmp_path):
    one = lambda _folder: volume(10 * GB + 30, total=100 * GB, device=1)  # noqa: E731
    require_room([(tmp_path, 10), (tmp_path / "a", 20)], lookup=one)
    # 8 GB reserve + 10 GB: the two claims together exceed what one volume has.
    with pytest.raises(DiskFullError):
        require_room([(tmp_path, 10 * GB), (tmp_path / "a", 10 * GB)], lookup=one)
    by_folder = lambda folder: (  # noqa: E731
        volume(30 * GB, total=100 * GB, device=1)
        if Path(folder) == tmp_path
        else volume(30 * GB, total=100 * GB, device=2)
    )
    require_room([(tmp_path, 10 * GB), (tmp_path / "other", 10 * GB)], lookup=by_folder)


def test_a_volume_is_the_system_volume_when_it_is_the_one_of_the_home_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda _path: shutil._ntuple_diskusage(100 * GB, 40 * GB, 60 * GB)
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    here = disk_space.volume_of(tmp_path / "does" / "not" / "exist" / "yet")
    assert (here.free, here.total, here.system) == (60 * GB, 100 * GB, True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "somewhere-else"))
    monkeypatch.setattr(disk_space, "_system_devices", lambda: {-1})
    assert disk_space.volume_of(elsewhere).system is False


def test_a_full_disk_error_from_any_write_reads_as_the_clear_sentence(tmp_path):
    stage = PipelineStage("upscale", "span", "span.pth")
    write = OSError(errno.ENOSPC, "No space left on device", str(tmp_path / ".localsr-job-x" / "a"))
    try:
        raise PipelineStageError(stage, str(write)) from write
    except PipelineStageError as wrapped:
        found = disk_full_from(wrapped, tmp_path)
    assert isinstance(found, DiskFullError)
    assert str(found).startswith(f'No space was left on the disk to write in "{tmp_path}"')
    assert "Errno" not in str(found)
    quota = OSError(errno.EDQUOT, "Disc quota exceeded")
    assert disk_full_from(quota, None) is not None
    assert disk_full_from(OSError(errno.EACCES, "Permission denied"), tmp_path) is None
    assert disk_full_from(ValueError("no"), tmp_path) is None
    mapped = DiskFullError("already clear")
    assert disk_full_from(RuntimeError("x"), None) is None
    assert disk_full_from(mapped, tmp_path) is mapped


# --- through the image runner --------------------------------------------------


def _runner(tmp_path, emitted, process_image=None):
    info = NormalizedModelInfo("Test", 4, 3, 3, True, False, 1, 1, "model.pth", [])
    engine = SimpleNamespace(
        process_image=process_image or (lambda **_: pytest.fail("the engine must not start")),
        release_models_except=lambda *_: None,
        release_model=lambda *_: None,
    )
    adapter = SimpleNamespace(inspect=lambda path: info, release=lambda: None)
    return ImageJobRunner(
        engine=engine,
        model_adapter=adapter,
        cancel_event=threading.Event(),
        emit=emitted.append,
        emit_preview=lambda _packet: None,
        preview_factory=lambda *a, **k: SimpleNamespace(close=lambda: None),
    )


def _job(tmp_path, scratch):
    source = tmp_path / "in.png"
    Image.new("RGB", (64, 48), (10, 20, 30)).save(source)
    return {
        "job_id": "job",
        "image_path": str(source),
        "model_path": "model.pth",
        "output_path": str(tmp_path / "out" / "result.png"),
        "scratch_directory": str(scratch),
        "output_temporary_directory": str(scratch),
        "output_format": "png",
        "device": "cpu",
        "precision": "fp32",
        "tile_size": 64,
        "halo": 8,
        "jpeg_quality": 98,
        "preserve_metadata": True,
        "safe_memory": True,
        "preview_enabled": False,
    }


def test_a_job_that_does_not_fit_fails_before_inference_and_leaves_nothing(tmp_path, monkeypatch):
    (tmp_path / "out").mkdir()
    scratch = tmp_path / "out" / ".localsr-job-0b8b2b39-6c1f-4d54-8f0e-5a5f2e2b8c11"
    scratch.mkdir()
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda _path: shutil._ntuple_diskusage(100 * GB, 99 * GB, GB)
    )
    emitted = []
    with pytest.raises(DiskFullError) as caught:
        _runner(tmp_path, emitted).run("job", _job(tmp_path, scratch))
    assert f'in "{tmp_path / "out"}"' in str(caught.value)
    assert list(scratch.iterdir()) == []
    assert list((tmp_path / "out").iterdir()) == [scratch]
    assert not any(type(message).__name__ == "JobCompleted" for message in emitted)


def test_a_write_that_runs_out_of_space_is_named_and_removes_its_temporary_file(
    tmp_path, monkeypatch
):
    (tmp_path / "out").mkdir()
    scratch = tmp_path / "out" / ".localsr-job-0b8b2b39-6c1f-4d54-8f0e-5a5f2e2b8c11"
    scratch.mkdir()
    from localsr.core.output_writer import OutputWriter

    def process_image(**kwargs):
        writer = OutputWriter((3, 192, 256), temporary_directory=kwargs["temporary_directory"])
        writer.mmap[:] = 7
        return writer

    real_save = Image.Image.save

    def full_disk(self, fp, *args, **kwargs):
        if str(fp).endswith(".tmp"):
            Path(fp).write_bytes(b"partial")
            raise OSError(errno.ENOSPC, "No space left on device", str(fp))
        return real_save(self, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", full_disk)
    emitted = []
    with pytest.raises(OSError) as caught:
        _runner(tmp_path, emitted, process_image).run("job", _job(tmp_path, scratch))
    folder = tmp_path / "out"
    found = disk_full_from(caught.value, folder)
    assert found is not None and f'"{folder}"' in str(found)
    assert list(scratch.iterdir()) == []
    assert not (tmp_path / "out" / "result.png").exists()


def test_the_worker_reports_a_full_disk_in_words_and_flags_it(tmp_path, monkeypatch):
    import localsr.worker.server as server_module

    (tmp_path / "out").mkdir()
    scratch = tmp_path / "out" / ".localsr-job-0b8b2b39-6c1f-4d54-8f0e-5a5f2e2b8c11"
    scratch.mkdir()
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda _path: shutil._ntuple_diskusage(100 * GB, 99 * GB, GB)
    )
    server = server_module.WorkerServer()
    info = NormalizedModelInfo("Test", 4, 3, 3, True, False, 1, 1, "model.pth", [])
    server.model_adapter = SimpleNamespace(inspect=lambda path: info, release=lambda: None)
    server.engine = SimpleNamespace(
        process_image=lambda **_: pytest.fail("the engine must not start"),
        release_model=lambda *_: None,
        release_models_except=lambda *_: None,
    )
    monkeypatch.setattr(server, "reader_thread_func", lambda: None)
    sent = []
    monkeypatch.setattr(server_module, "send_message", lambda message: sent.append(message))
    server.message_queue.put({"type": "job_request", "data": _job(tmp_path, scratch)})
    server.message_queue.put({"type": "shutdown_request"})
    server.run()
    failed = [json.loads(m.to_json())["data"] for m in sent if type(m).__name__ == "JobFailed"]
    assert len(failed) == 1
    assert failed[0]["disk_full"] is True
    assert failed[0]["error_message"].startswith("Not enough free disk space in ")
    assert list(scratch.iterdir()) == []


# --- photo batches, video and edit ----------------------------------------------


def _batch_jobs(tmp_path, count=3, size=(3024, 4032)):
    jobs = []
    for index in range(count):
        path = tmp_path / f"p{index}.jpg"
        Image.new("RGB", size).save(path, quality=1)
        jobs.append(
            {
                **_job(tmp_path, tmp_path / "scratch"),
                "job_id": f"j{index}",
                "image_path": str(path),
                "output_path": str(tmp_path / f"o{index}.png"),
            }
        )
    return jobs


def _batch_runner(tmp_path):
    from localsr.worker.image_batch import ImageBatchRunner

    emitted = []
    runner = ImageBatchRunner(
        engine=SimpleNamespace(),
        model_adapter=SimpleNamespace(),
        cancel_event=threading.Event(),
        emit=emitted.append,
        emit_preview=lambda _packet: None,
    )
    return runner, emitted


def test_a_photo_batch_runs_with_fewer_encoders_when_the_usual_ones_do_not_fit(
    tmp_path, monkeypatch
):
    from localsr.worker.image_batch import DEFAULT_ENCODERS

    info = NormalizedModelInfo("Test", 4, 3, 3, True, False, 1, 1, "m.pth", [])
    jobs = _batch_jobs(tmp_path)
    runner, emitted = _batch_runner(tmp_path)
    need = image_need(3024, 4032, 4, None, "png", False)
    # Room, above the reserve, for the memmaps and files of two encoders but not of three.
    free = _batch_total(need, 2) + 8 * GB
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda _path: shutil._ntuple_diskusage(500 * GB, 0, free)
    )
    assert runner._encoders_that_fit(jobs, info) == 2 < DEFAULT_ENCODERS
    assert any("2 encoders" in getattr(message, "message", "") for message in emitted)


def test_a_photo_batch_gives_its_photos_back_when_not_even_one_encoder_fits(tmp_path, monkeypatch):
    info = NormalizedModelInfo("Test", 4, 3, 3, True, False, 1, 1, "m.pth", [])
    jobs = _batch_jobs(tmp_path)
    runner, emitted = _batch_runner(tmp_path)
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda _path: shutil._ntuple_diskusage(500 * GB, 0, 26 * GB)
    )
    monkeypatch.setattr(
        disk_space,
        "volume_of",
        lambda folder: Volume(device=1, free=3 * GB, total=500 * GB, system=False),
    )
    assert runner._encoders_that_fit(jobs, info) is None
    assert any("one at a time" in getattr(message, "message", "") for message in emitted)


def test_a_batch_of_unreadable_photos_is_left_to_fail_on_its_own(tmp_path):
    info = NormalizedModelInfo("Test", 4, 3, 3, True, False, 1, 1, "m.pth", [])
    jobs = _batch_jobs(tmp_path, count=2)
    for job in jobs:
        Path(job["image_path"]).write_bytes(b"not a picture")
    runner, _ = _batch_runner(tmp_path)
    from localsr.worker.image_batch import DEFAULT_ENCODERS

    assert runner._encoders_that_fit(jobs, info) == DEFAULT_ENCODERS


def test_a_failure_of_one_photo_of_a_batch_is_the_clear_sentence_and_flagged(tmp_path):
    runner, emitted = _batch_runner(tmp_path)
    fields = disk_space.failure_fields(
        OSError(errno.ENOSPC, "No space left on device", str(tmp_path / "x")), tmp_path / "o.png"
    )
    assert fields["disk_full"] is True and "Errno" not in fields["error_message"]
    assert disk_space.failure_fields(ValueError("bad"), None) == {"error_message": "bad"}


def test_a_video_needs_two_encoded_files_and_a_master_when_another_program_encodes_it():
    encoded = 3840 * 2160 * 1800 // 20 + (1 << 20)
    assert disk_space.video_bytes(3840, 2160, 1800) == 2 * encoded == 1_495_089_152
    assert disk_space.video_bytes(3840, 2160, 1800, master=True) == 2 * encoded + 13_436_928_000


def test_the_desktop_host_mirror_agrees_with_the_shared_cases():
    """The Rust mirror (desktop/src-tauri/src/disk_room.rs) is tested against the same file."""
    path = Path(__file__).parent / "fixtures" / "disk_estimate_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    for case in cases["images"]:
        need = image_need(
            case["width"],
            case["height"],
            case["scale"],
            case["output_scale"],
            case["format"],
            case["alpha"],
        )
        assert (need.scratch, need.encoded) == (case["scratch"], case["encoded"])
    for case in cases["reserves"]:
        assert reserve_for(volume(0, case["total"], case["system"])) == case["reserve"]
    for case in cases["videos"]:
        got = disk_space.video_bytes(case["width"], case["height"], case["frames"], case["master"])
        assert got == case["bytes"]
    for case in cases["sizes"]:
        assert disk_space.format_size(case["bytes"]) == case["text"]


def test_saving_a_result_never_copies_it_whole_into_one_array(tmp_path, monkeypatch):
    """A transposed array handed to Pillow is first copied to a contiguous one, whole: a
    second full-size copy of a result that can be 600 MB, beside the picture itself."""
    from localsr.core.image_io import ImageManager
    from localsr.core.output_writer import OutputWriter

    handed = []
    real = Image.fromarray
    monkeypatch.setattr(
        Image,
        "fromarray",
        lambda array, *a, **k: handed.append(array.nbytes) or real(array, *a, **k),
    )
    with OutputWriter((3, 1200, 900), temporary_directory=tmp_path) as writer:
        writer.mmap[0] = 200
        writer.mmap[1] = 100
        writer.mmap[2] = 50
        ImageManager().save(
            writer,
            str(tmp_path / "out.png"),
            format="png",
            scale=4,
            output_scale=4,
            temporary_directory=tmp_path,
        )
        whole = writer.mmap.nbytes
    assert handed and max(handed) <= whole // 4
    with Image.open(tmp_path / "out.png") as saved:
        assert saved.size == (900, 1200)
        assert saved.getpixel((0, 0)) == (200, 100, 50)
        assert saved.getpixel((899, 1199)) == (200, 100, 50)
