"""Publishing an output never replaces a file that is already at its name."""

from __future__ import annotations

import errno
import os
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import localsr.core.video_io as video_io
from localsr.core.image_io import ImageManager
from localsr.core.output_publish import OutputExistsError, publish_output
from localsr.core.video_io import encode_video

EXISTING = b"a file the user put here"


def _case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe"
    probe.write_bytes(b"")
    try:
        return (directory / "caseprobe").exists()
    finally:
        probe.unlink()


def _save_image(destination: Path, scratch: Path | None = None, cancel_event=None) -> None:
    ImageManager().save_from_writer(
        np.zeros((3, 16, 16), dtype=np.uint8),
        str(destination),
        "png",
        98,
        False,
        None,
        {},
        1,
        temporary_directory=scratch,
        cancel_event=cancel_event,
    )


def _no_temporaries(directory: Path) -> bool:
    return not [path for path in directory.rglob("*") if path.name.endswith(".tmp")]


def test_image_published_over_a_file_that_appeared_during_the_job_is_refused(tmp_path, monkeypatch):
    destination = tmp_path / "result.png"
    real_save = Image.Image.save

    def save_then_the_name_is_taken(image, path, *args, **kwargs):
        real_save(image, path, *args, **kwargs)
        destination.write_bytes(EXISTING)

    monkeypatch.setattr(Image.Image, "save", save_then_the_name_is_taken)
    with pytest.raises(OutputExistsError, match="already exists in the output folder"):
        _save_image(destination)
    assert destination.read_bytes() == EXISTING
    assert _no_temporaries(tmp_path)


def test_image_case_only_name_collision_keeps_the_first_result(tmp_path):
    if not _case_insensitive(tmp_path):
        pytest.skip("this file system tells names apart by letter case")
    _save_image(tmp_path / "A.png")
    first = (tmp_path / "A.png").read_bytes()
    with pytest.raises(OutputExistsError, match="Nothing was replaced"):
        _save_image(tmp_path / "a.png")
    assert (tmp_path / "A.png").read_bytes() == first
    assert len(list(tmp_path.iterdir())) == 1
    assert _no_temporaries(tmp_path)


def test_image_in_a_job_scratch_directory_leaves_no_temporary_on_collision(tmp_path):
    scratch = tmp_path / ".localsr-job"
    scratch.mkdir()
    destination = tmp_path / "result.png"
    destination.write_bytes(EXISTING)
    with pytest.raises(OutputExistsError):
        _save_image(destination, scratch)
    assert destination.read_bytes() == EXISTING
    assert not list(scratch.iterdir())


def _frames(count=3, hook=None):
    for index in range(count):
        yield np.full((64, 64, 3), 40 * index, dtype=np.uint8)
    if hook is not None:
        hook()


def test_video_published_over_a_file_that_appeared_during_the_job_is_refused(tmp_path):
    destination = tmp_path / "result.mp4"
    with pytest.raises(OutputExistsError, match="already exists in the output folder"):
        encode_video(
            _frames(hook=lambda: destination.write_bytes(EXISTING)),
            str(destination),
            fps=5.0,
            width=64,
            height=64,
        )
    assert destination.read_bytes() == EXISTING
    assert _no_temporaries(tmp_path)


def test_video_with_remuxed_audio_is_not_published_over_a_new_file(tmp_path, monkeypatch):
    destination = tmp_path / "result.mp4"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"exists")

    def remux(_encoded, _source, target, **_kwargs):
        Path(target).write_bytes(b"remuxed")
        destination.write_bytes(EXISTING)
        return True

    monkeypatch.setattr(video_io, "_remux_source_streams", remux)
    with pytest.raises(OutputExistsError):
        encode_video(
            _frames(),
            str(destination),
            fps=5.0,
            width=64,
            height=64,
            audio_source=str(source),
        )
    assert destination.read_bytes() == EXISTING
    assert _no_temporaries(tmp_path)


def test_video_case_only_name_collision_keeps_the_first_result(tmp_path):
    if not _case_insensitive(tmp_path):
        pytest.skip("this file system tells names apart by letter case")
    encode_video(_frames(), str(tmp_path / "V.mp4"), fps=5.0, width=64, height=64)
    first = (tmp_path / "V.mp4").read_bytes()
    with pytest.raises(OutputExistsError):
        encode_video(_frames(), str(tmp_path / "v.mp4"), fps=5.0, width=64, height=64)
    assert (tmp_path / "V.mp4").read_bytes() == first
    assert _no_temporaries(tmp_path)


def test_external_export_does_not_replace_a_file_that_appeared(tmp_path, monkeypatch):
    destination = tmp_path / "result.mp4"
    fake = object()

    def transcode(_bridge, _master, final, **_kwargs):
        Path(final).write_bytes(b"encoded")
        destination.write_bytes(EXISTING)

    import localsr.core.media_bridge as media_bridge

    monkeypatch.setattr(media_bridge, "transcode_output", transcode)
    with pytest.raises(OutputExistsError):
        encode_video(
            _frames(),
            str(destination),
            fps=5.0,
            width=64,
            height=64,
            video_codec="h264",
            external_ffmpeg=fake,
        )
    assert destination.read_bytes() == EXISTING
    assert _no_temporaries(tmp_path)


def test_system_stream_export_does_not_replace_a_file_that_appeared(tmp_path):
    destination = tmp_path / "result.mp4"

    class System:
        def encoder_for(self, *_args, **_kwargs):
            return object()

        def source_audio(self, *_args, **_kwargs):
            return None

        def encode_frames(self, frames, final, **_kwargs):
            for _ in frames:
                pass
            Path(final).write_bytes(b"encoded")
            destination.write_bytes(EXISTING)

    with pytest.raises(OutputExistsError):
        encode_video(
            _frames(),
            str(destination),
            fps=5.0,
            width=64,
            height=64,
            video_codec="h264",
            external_ffmpeg=type("Bridge", (), {"system": System()})(),
            stream_to_system=True,
            preserve_timing=True,
        )
    assert destination.read_bytes() == EXISTING
    assert _no_temporaries(tmp_path)


def test_publish_moves_the_temporary_to_a_new_name(tmp_path):
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == b"payload"
    assert not temporary.exists()


def test_publish_refuses_an_existing_destination_and_removes_nothing_of_it(tmp_path):
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    (tmp_path / "x.bin").write_bytes(EXISTING)
    with pytest.raises(OutputExistsError, match="x.bin"):
        publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == EXISTING
    assert temporary.read_bytes() == b"payload"


def test_publish_checks_cancellation_immediately_before_publishing(tmp_path):
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        publish_output(temporary, tmp_path / "x.bin", cancel)
    assert not (tmp_path / "x.bin").exists()


def _without_hard_links(monkeypatch, code=errno.EPERM):
    def refuse(*_args, **_kwargs):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(os, "link", refuse)


@pytest.mark.parametrize("code", [errno.EPERM, errno.ENOTSUP, errno.EINVAL, errno.EXDEV])
def test_publish_without_hard_links_creates_the_file_exclusively(tmp_path, monkeypatch, code):
    _without_hard_links(monkeypatch, code)
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload" * 100_000)
    publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == b"payload" * 100_000
    assert not temporary.exists()


def test_publish_without_hard_links_never_replaces_an_existing_file(tmp_path, monkeypatch):
    _without_hard_links(monkeypatch)
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    (tmp_path / "x.bin").write_bytes(EXISTING)
    with pytest.raises(OutputExistsError):
        publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == EXISTING


def test_failed_copy_without_hard_links_leaves_no_partial_output(tmp_path, monkeypatch):
    _without_hard_links(monkeypatch)
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")

    def disk_full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("localsr.core.output_publish.shutil.copyfileobj", disk_full)
    with pytest.raises(OSError, match="No space"):
        publish_output(temporary, tmp_path / "x.bin")
    assert not (tmp_path / "x.bin").exists()
    assert temporary.read_bytes() == b"payload"
