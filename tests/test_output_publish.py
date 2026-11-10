"""Publishing an output never replaces a file that is already at its name."""

from __future__ import annotations

import errno
import os
import sys
import threading
from pathlib import Path, PureWindowsPath

import numpy as np
import pytest
from PIL import Image

import localsr.core.output_publish as output_publish
import localsr.core.video_io as video_io
from localsr.core.image_io import ImageManager
from localsr.core.output_publish import (
    OutputExistsError,
    output_name_length,
    output_name_limit,
    owned_temporary,
    publish_output,
)
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


def _names(directory: Path) -> list[str]:
    # exFAT volumes mounted on macOS get "._" companion files for extended attributes.
    return sorted(path.name for path in directory.iterdir() if not path.name.startswith("._"))


def _no_temporaries(directory: Path) -> bool:
    return not [
        path
        for path in directory.rglob("*")
        if path.name.endswith(".tmp") and not path.name.startswith("._")
    ]


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
    assert len(_names(tmp_path)) == 1
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


def _without_no_replace_rename(monkeypatch):
    monkeypatch.setattr(output_publish, "_rename_without_replacing", lambda *_args: False)


def _force_step(monkeypatch, step):
    if step >= 2:
        _without_hard_links(monkeypatch)
    if step >= 3:
        _without_no_replace_rename(monkeypatch)


def _native_rename_works(tmp_path) -> bool:
    source, target = tmp_path / ".probe-a", tmp_path / ".probe-b"
    source.write_bytes(b"")
    try:
        return output_publish._native_rename_noreplace(source, target)
    except OSError:
        return False
    finally:
        for path in (source, target):
            path.unlink(missing_ok=True)


@pytest.mark.parametrize("step", [1, 2, 3])
def test_each_publish_step_moves_the_file_to_a_new_name(tmp_path, monkeypatch, step):
    _force_step(monkeypatch, step)
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload" * 100_000)
    publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == b"payload" * 100_000
    assert not temporary.exists()
    assert _names(tmp_path) == ["x.bin"]


@pytest.mark.parametrize("code", [errno.EPERM, errno.ENOTSUP, errno.EINVAL, errno.EXDEV])
def test_publish_without_hard_links_still_publishes(tmp_path, monkeypatch, code):
    _without_hard_links(monkeypatch, code)
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == b"payload"
    assert not temporary.exists()


@pytest.mark.parametrize("step", [1, 2, 3])
def test_each_publish_step_refuses_an_existing_name_and_keeps_both_files(
    tmp_path, monkeypatch, step
):
    _force_step(monkeypatch, step)
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    (tmp_path / "x.bin").write_bytes(EXISTING)
    with pytest.raises(OutputExistsError, match="x.bin"):
        publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == EXISTING
    assert temporary.read_bytes() == b"payload"
    assert _names(tmp_path) == [".x.tmp", "x.bin"]


def test_the_native_no_replace_rename_works_and_refuses_an_existing_name(tmp_path):
    if not _native_rename_works(tmp_path):
        pytest.skip("renameat2/renamex_np is not available on this system or file system")
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    assert output_publish._native_rename_noreplace(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == b"payload" and not temporary.exists()
    temporary.write_bytes(b"second")
    with pytest.raises(FileExistsError):
        output_publish._native_rename_noreplace(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == b"payload"
    assert temporary.read_bytes() == b"second"


def test_publish_through_the_native_rename_when_hard_links_are_refused(tmp_path, monkeypatch):
    if sys.platform == "win32" or not _native_rename_works(tmp_path):
        pytest.skip("renameat2/renamex_np is not available on this system or file system")
    _without_hard_links(monkeypatch)

    def no_claim(*_args, **_kwargs):
        raise AssertionError("step 3 must not run when step 2 works")

    monkeypatch.setattr(output_publish, "_claim_then_replace", no_claim)
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == b"payload"


@pytest.mark.parametrize("code", [errno.ENOSYS, errno.EINVAL, errno.ENOTSUP])
def test_a_native_rename_that_is_not_supported_falls_through_to_the_claim(
    tmp_path, monkeypatch, code
):
    _without_hard_links(monkeypatch)

    def unsupported(*_args):
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(output_publish, "_native_rename_noreplace", unsupported)
    monkeypatch.setattr(output_publish.sys, "platform", "linux")
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    publish_output(temporary, tmp_path / "x.bin")
    assert (tmp_path / "x.bin").read_bytes() == b"payload"


def test_a_missing_native_rename_symbol_falls_through(tmp_path, monkeypatch):
    class Library:
        def __getattr__(self, name):
            raise AttributeError(name)

    monkeypatch.setattr(output_publish.ctypes, "CDLL", lambda *_args, **_kwargs: Library())
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    assert output_publish._native_rename_noreplace(temporary, tmp_path / "x.bin") is False


def test_a_real_rename_error_is_not_mistaken_for_missing_support(tmp_path, monkeypatch):
    _without_hard_links(monkeypatch)

    def denied(*_args):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(output_publish, "_native_rename_noreplace", denied)
    monkeypatch.setattr(output_publish.sys, "platform", "linux")
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    with pytest.raises(PermissionError):
        publish_output(temporary, tmp_path / "x.bin")
    assert not (tmp_path / "x.bin").exists()


def test_windows_uses_a_plain_rename_that_refuses_an_existing_name(tmp_path, monkeypatch):
    _without_hard_links(monkeypatch)
    monkeypatch.setattr(output_publish.sys, "platform", "win32")
    calls = []

    def rename(source, target):
        calls.append((source, target))
        raise FileExistsError(errno.EEXIST, "exists")

    monkeypatch.setattr(os, "rename", rename)
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")
    with pytest.raises(OutputExistsError):
        publish_output(temporary, tmp_path / "x.bin")
    assert calls == [(temporary, tmp_path / "x.bin")]


def _complete_empty_or_absent(destination: Path, payload: bytes) -> bool:
    return not destination.exists() or destination.read_bytes() in (b"", payload)


@pytest.mark.parametrize("step", [2, 3])
def test_no_publish_step_ever_shows_partial_content_under_the_final_name(
    tmp_path, monkeypatch, step
):
    _force_step(monkeypatch, step)
    destination, payload = tmp_path / "x.bin", b"payload" * 1000
    temporary = tmp_path / ".x.tmp"
    temporary.write_bytes(payload)
    seen = []
    real_replace = os.replace

    def replace(source, target):
        seen.append(_complete_empty_or_absent(destination, payload))
        real_replace(source, target)
        seen.append(_complete_empty_or_absent(destination, payload))

    monkeypatch.setattr(os, "replace", replace)
    publish_output(temporary, destination)
    assert all(seen)
    assert destination.read_bytes() == payload


def test_a_failure_between_claim_and_replace_removes_only_the_placeholder(tmp_path, monkeypatch):
    _force_step(monkeypatch, 3)
    destination, temporary = tmp_path / "x.bin", tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")

    def fail(_source, _target):
        assert destination.read_bytes() == b""
        raise OSError(errno.EIO, "disk error")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk error"):
        publish_output(temporary, destination)
    assert not destination.exists()
    assert temporary.read_bytes() == b"payload"


def test_a_failure_after_someone_else_took_the_placeholder_removes_nothing(tmp_path, monkeypatch):
    _force_step(monkeypatch, 3)
    destination, temporary = tmp_path / "x.bin", tmp_path / ".x.tmp"
    temporary.write_bytes(b"payload")

    def fail_after_a_foreign_file_took_the_name(_source, _target):
        destination.unlink()
        destination.write_bytes(EXISTING)
        raise OSError(errno.EIO, "disk error")

    monkeypatch.setattr(os, "replace", fail_after_a_foreign_file_took_the_name)
    with pytest.raises(OSError, match="disk error"):
        publish_output(temporary, destination)
    assert destination.read_bytes() == EXISTING
    assert temporary.read_bytes() == b"payload"


def _longest_name(directory: Path, extension: str) -> str:
    name = "y" * (output_name_limit(directory) - len(extension) - 1) + "." + extension
    try:
        (directory / name).touch()
    except OSError:
        if sys.platform == "win32" and not _fits_windows(directory / name):
            return name  # the callers expect the early refusal instead
        pytest.skip("this file system does not allow a name of that length")
    (directory / name).unlink()
    return name


def _fits_windows(path: Path) -> bool:
    try:
        output_publish.require_output_path_fits(
            os.path.abspath(path), "win32", output_publish.windows_path_limit()
        )
    except ValueError:
        return False
    return True


def _refused_early_on_windows(path: Path) -> bool:
    """On Windows a name whose full path is too long is refused before any work is done."""
    if sys.platform != "win32" or _fits_windows(path):
        return False
    with pytest.raises(ValueError, match="shorter folder or file name"):
        output_publish.require_output_name(path)
    return True


@pytest.mark.parametrize("shorter_by", [0, 1, 27, 60])
def test_an_image_output_name_at_the_file_system_limit_is_written(tmp_path, shorter_by):
    name = _longest_name(tmp_path, "png")[shorter_by:]
    if _refused_early_on_windows(tmp_path / name):
        return
    _save_image(tmp_path / name)
    assert _names(tmp_path) == [name]


def test_a_video_output_name_at_the_file_system_limit_is_written(tmp_path):
    name = _longest_name(tmp_path, "mp4")
    if _refused_early_on_windows(tmp_path / name):
        return
    encode_video(_frames(), str(tmp_path / name), fps=5.0, width=64, height=64)
    assert _names(tmp_path) == [name]


FOLDER = "C:\\Users\\someone\\Pictures\\Upscaled"  # 31 characters


def _windows_path(name_length: int) -> str:
    return FOLDER + "\\" + "n" * (name_length - 4) + ".png"


def test_a_windows_path_that_fits_is_accepted_and_one_unit_more_is_refused():
    # 259 characters plus the terminating NUL is the most the classic limit allows.
    fits = _windows_path(259 - len(FOLDER) - 1)
    assert len(fits) == 259
    output_publish.require_output_path_fits(fits, "win32", 260)
    with pytest.raises(ValueError, match="allows 259 characters"):
        output_publish.require_output_path_fits(_windows_path(260 - len(FOLDER)), "win32", 260)


def test_the_temporary_name_beside_the_result_counts_against_the_windows_limit():
    # The result's own path fits (250), but the hidden temporary in the same folder does not.
    folder = "C:\\" + "f" * 230
    path = folder + "\\" + "a.png"
    output_publish.require_output_path_fits(path, "win32", 280)
    with pytest.raises(ValueError, match="shorter folder or file name"):
        output_publish.require_output_path_fits(path, "win32", 260)


def test_the_early_windows_refusal_names_the_limit_and_the_remedy():
    with pytest.raises(ValueError) as caught:
        output_publish.require_output_path_fits(_windows_path(240), "win32", 260)
    assert "259 characters" in str(caught.value)
    assert "Choose a shorter folder or file name." in str(caught.value)


def test_other_platforms_and_long_path_prefixes_are_not_limited_by_the_windows_rule():
    long = _windows_path(240)
    output_publish.require_output_path_fits(long, "linux", 260)
    output_publish.require_output_path_fits("\\\\?\\" + long, "win32", 260)
    output_publish.require_output_path_fits(long, "win32", output_publish.WINDOWS_LONG_PATH_LIMIT)


def test_utf16_units_count_for_the_windows_path_limit():
    folder = "C:\\" + "\U0001f600" * 70  # 140 units
    with pytest.raises(ValueError):
        output_publish.require_output_path_fits(folder + "\\" + "a" * 130 + ".png", "win32", 260)
    output_publish.require_output_path_fits(folder + "\\" + "a" * 60 + ".png", "win32", 260)


def test_require_output_name_applies_the_windows_path_rule_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(output_publish.sys, "platform", "win32")
    monkeypatch.setattr(output_publish, "windows_path_limit", lambda: 260)
    monkeypatch.setattr(output_publish.os.path, "abspath", lambda path: str(path))
    monkeypatch.setattr(output_publish, "Path", PureWindowsPath)
    monkeypatch.setattr(output_publish, "output_name_limit", lambda _directory: 255)
    with pytest.raises(ValueError, match="shorter folder or file name"):
        output_publish.require_output_name(_windows_path(240))
    output_publish.require_output_name(_windows_path(100))


def test_the_windows_path_limit_is_the_classic_one_when_long_paths_cannot_be_confirmed():
    assert output_publish.windows_path_limit() in (260, output_publish.WINDOWS_LONG_PATH_LIMIT)
    if sys.platform != "win32":
        assert output_publish.windows_path_limit() == 260


def test_a_temporary_name_is_short_and_does_not_contain_the_destination_name(tmp_path):
    first, second = owned_temporary(tmp_path, "image"), owned_temporary(tmp_path, "image")
    assert first != second and first.parent == tmp_path
    assert first.name.startswith(".localsr-image-") and len(first.name) < 40


def test_name_length_counts_what_the_file_system_counts(monkeypatch):
    monkeypatch.setattr("localsr.core.output_publish.sys.platform", "darwin")
    assert output_name_length("a\U0001f600") == 3
    monkeypatch.setattr("localsr.core.output_publish.sys.platform", "linux")
    assert output_name_length("a\U0001f600") == 5


def test_the_name_limit_falls_back_when_the_folder_cannot_be_asked(tmp_path):
    assert output_name_limit(tmp_path / "missing") == 255
    assert output_name_limit(tmp_path) >= 143


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_a_system_encoded_export_through_the_master_leaves_only_its_output(
    tmp_path, monkeypatch, outcome
):
    import localsr.core.media_bridge as media_bridge
    from localsr.core.external_ffmpeg import ExternalFFmpegError

    destination = tmp_path / "out.mp4"

    def master(_frames, target, **_options):
        Path(target).write_bytes(b"master")
        return target

    def transcode(_bridge, _master, final, **_kwargs):
        # macOS keeps a hidden copy of a file the system encoder wrote beside it.
        Path(final).write_bytes(b"video")
        Path(final).with_name(Path(final).name + ".sb-df5fe11b-AbCdEf").write_bytes(b"x" * 64)
        if outcome == "failure":
            raise ExternalFFmpegError("the encoder stopped")
        if outcome == "cancel":
            raise InterruptedError("video job cancelled")

    monkeypatch.setattr(video_io, "encode_video", master)
    monkeypatch.setattr(media_bridge, "transcode_output", transcode)
    arguments = dict(
        video_codec="hevc",
        container_format="mp4",
        crf=18,
        external_ffmpeg=object(),
        stage_callback=None,
        hdr_format="",
        width=64,
        height=64,
        fps=10.0,
        cancel_event=None,
        temporary_directory=None,
    )
    if outcome == "success":
        video_io._external_export(iter(()), destination, **arguments)
        assert _names(tmp_path) == ["out.mp4"]
    else:
        with pytest.raises((video_io.VideoStageError, InterruptedError)):
            video_io._external_export(iter(()), destination, **arguments)
        assert _names(tmp_path) == []


def test_an_output_path_that_is_the_source_leaves_the_source_unchanged(tmp_path):
    source = tmp_path / "photo.png"
    Image.new("RGB", (8, 8), (10, 200, 30)).save(source)
    before = source.read_bytes()
    with pytest.raises(OutputExistsError):
        _save_image(source)
    assert source.read_bytes() == before
    assert _no_temporaries(tmp_path)
