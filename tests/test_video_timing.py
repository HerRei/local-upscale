"""Media-contract regressions: timestamps, transforms, motion and temporal routing."""

import shutil
import struct
import subprocess
import threading
from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest

from localsr.core.video_io import decode_timed_frames, encode_video, probe_video
from localsr.core.video_pipeline import VideoJobConfig, _deflicker_frames, run_video_job
from localsr.worker.server import WorkerServer


def make_vfr(path, times=(0, 40, 80, 120, 480, 840, 1200, 1560, 1920, 2280), transfer=2):
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=25)
        stream.width, stream.height, stream.pix_fmt = 48, 32, "yuv420p"
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.codec_context.color_trc = transfer
        stream.options = {"crf": "0", "bf": "0"}
        for index, stamp in enumerate(times):
            rgb = np.full((32, 48, 3), index % 10 * 20, dtype=np.uint8)
            rgb[:12, :16] = [240, 40, 20]
            frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts, frame.time_base = stamp, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


class IdentityEngine:
    def load_model(self, *_args):
        pass

    def process_frame(self, *, img_tensor, **kwargs):
        return (img_tensor * 255).round().byte().numpy()

    def process_frames(self, frames, **kwargs):
        return frames


def run_standard(source, destination, **options):
    return run_video_job(
        VideoJobConfig(
            str(source),
            "test",
            str(destination),
            SimpleNamespace(scale=1),
            "cpu",
            "fp32",
            64,
            4,
            False,
            **options,
        ),
        IdentityEngine(),
        threading.Event(),
    )


def timestamps(path):
    return [float(frame.timestamp) for frame in decode_timed_frames(str(path))]


@pytest.mark.parametrize("trim", [False, True])
@pytest.mark.parametrize("deflicker", [False, True])
def test_standard_preserves_vfr_and_selected_duration(tmp_path, trim, deflicker):
    source = make_vfr(tmp_path / "vfr.mp4")
    output = tmp_path / "output.mp4"
    options = dict(start_frame=2, end_frame=6) if trim else {}
    run_standard(source, output, deflicker=deflicker, **options)
    selected = list(decode_timed_frames(str(source), **options))
    expected = [float(frame.timestamp - selected[0].timestamp) for frame in selected]
    assert timestamps(output) == pytest.approx(expected, abs=0.001)
    assert probe_video(str(output)).duration_seconds == pytest.approx(
        float(selected[-1].timestamp + selected[-1].duration - selected[0].timestamp), abs=0.002
    )


def test_explicit_fps_override_changes_speed(tmp_path):
    source = make_vfr(tmp_path / "vfr.mp4")
    output = tmp_path / "retimed.mp4"
    run_standard(source, output, fps_override=10)
    assert timestamps(output) == pytest.approx([index / 10 for index in range(10)], abs=0.001)


@pytest.mark.parametrize("cancel", [False, True])
def test_early_decode_exit_can_reopen_the_same_video(tmp_path, cancel):
    source = make_vfr(tmp_path / "early-exit.mp4")
    for _ in range(10):
        event = threading.Event()
        decoded = decode_timed_frames(str(source), cancel_event=event)
        try:
            assert next(decoded).index == 0
            if cancel:
                event.set()
                with pytest.raises(InterruptedError, match="cancelled"):
                    next(decoded)
        finally:
            decoded.close()
    assert timestamps(source) == pytest.approx(
        [0, 0.04, 0.08, 0.12, 0.48, 0.84, 1.2, 1.56, 1.92, 2.28], abs=0.001
    )


@pytest.mark.parametrize("transfer", [16, 18])
def test_hdr_is_rejected_before_silent_sdr_export(tmp_path, transfer):
    source = make_vfr(tmp_path / "hdr.mp4", transfer=transfer)
    output = tmp_path / "output.mp4"
    with pytest.raises(ValueError, match="HDR.*SDR"):
        list(decode_timed_frames(str(source)))
    with pytest.raises(RuntimeError, match="HDR.*SDR"):
        run_standard(source, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".output.mp4.localsr-*.tmp"))


def ffmpeg(*args):
    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("FFmpeg needed to create display-matrix/audio fixture")
    subprocess.run([binary, "-hide_banner", "-loglevel", "error", *map(str, args)], check=True)


@pytest.mark.parametrize("translated", [False, True])
@pytest.mark.parametrize(
    "angle,flip", [(90, False), (180, False), (270, False), (0, True), (90, True)]
)
def test_display_transform_matches_ffmpeg_autorotate(tmp_path, angle, flip, translated):
    source = make_vfr(tmp_path / "source.mp4")
    rotated = tmp_path / "rotated.mov"
    args = ["-display_rotation", str(angle)]
    if flip:
        args += ["-display_hflip"]
    ffmpeg(*args, "-i", source, "-c", "copy", rotated)
    if translated:
        # A camera MOV rebases rotated track bounds with a bottom-row x/y
        # translation. FFmpeg's rotation flag alone leaves these fields zero.
        data = bytearray(rotated.read_bytes())
        atom = data.index(b"tkhd")
        assert data[atom + 4] == 0  # version-0 track header
        offset = atom + 44
        matrix = list(struct.unpack_from(">9i", data, offset))
        basis = np.array([[matrix[0], matrix[1]], [matrix[3], matrix[4]]]) / 65536
        corners = np.array([[0, 0], [48, 0], [0, 32], [48, 32]]) @ basis
        translation = -corners.min(axis=0)
        matrix[6:8] = [int(value * 65536) for value in translation]
        struct.pack_into(">9i", data, offset, *matrix)
        rotated.write_bytes(data)
    expected = tmp_path / "expected.png"
    ffmpeg("-i", rotated, "-frames:v", "1", expected)
    from PIL import Image

    reference = np.asarray(Image.open(expected).convert("RGB"))
    decoded = next(decode_timed_frames(str(rotated))).rgb
    assert decoded.shape == reference.shape
    assert np.abs(decoded.astype(int) - reference.astype(int)).mean() < 2
    output = tmp_path / "enhanced.mp4"
    run_standard(rotated, output)
    assert (probe_video(str(output)).height, probe_video(str(output)).width) == reference.shape[:2]


@pytest.mark.parametrize("change", [(0, 2, 1), (1, 2, 1), (2, 2, 0), (0, 0, 2 << 16)])
def test_display_transform_rejects_perspective_and_scaling(change):
    from localsr.core.video_io import _display_transform

    class MatrixData(bytes):
        type = SimpleNamespace(name="DISPLAYMATRIX")

    matrix = np.diag([1 << 16, 1 << 16, 1 << 30]).astype(np.int32)
    row, col, value = change
    matrix[row, col] = value
    frame = SimpleNamespace(side_data=[MatrixData(matrix.tobytes())])
    with pytest.raises(ValueError, match="display transform"):
        _display_transform(frame)


def test_motion_and_hard_cuts_survive_deflicker():
    frames = []
    for index in range(7):
        rgb = np.zeros((20, 50, 3), dtype=np.uint8)
        rgb[8:11, 2 + index * 6 : 5 + index * 6] = 255
        frames.append(rgb)
    for actual, expected in zip(
        _deflicker_frames(iter(frames), 3, threading.Event()), frames, strict=True
    ):
        np.testing.assert_array_equal(actual, expected)
    cut = [np.full((8, 8, 3), value, dtype=np.uint8) for value in (0, 0, 255, 255)]
    for actual, expected in zip(
        _deflicker_frames(iter(cut), 3, threading.Event()), cut, strict=True
    ):
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("start,end", [(2, 4), (None, 3), (3, None)])
def test_temporal_worker_preserves_vfr_trim_and_audio(tmp_path, monkeypatch, start, end):
    video = make_vfr(tmp_path / "vfr.mp4")
    source, output = tmp_path / "audio.mp4", tmp_path / "out.mp4"
    ffmpeg(
        "-i",
        video,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=800:duration=3",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        source,
    )
    messages = []
    monkeypatch.setattr("localsr.worker.server.send_message", messages.append)
    worker = SimpleNamespace(cancel_event=threading.Event(), _emit_live_preview=lambda _: None)
    WorkerServer._run_temporal_video_job(
        worker,
        "test",
        {
            "video_path": str(source),
            "output_video_path": str(output),
            "start_frame": start,
            "end_frame": end,
            "preview_enabled": False,
        },
        lambda *_: IdentityEngine(),
    )
    selected = list(decode_timed_frames(str(source), start, end))
    assert timestamps(output) == pytest.approx(
        [float(frame.timestamp - selected[0].timestamp) for frame in selected], abs=0.001
    )
    with av.open(str(output)) as container:
        assert len(container.streams.audio) == 1
        audio = container.streams.audio[0]
        assert abs(float(audio.start_time * audio.time_base)) < 0.03
    completed = [message for message in messages if type(message).__name__ == "VideoJobCompleted"]
    assert completed[0].frames_processed == len(selected)


def test_temporal_multiple_chunks_and_short_tail(tmp_path, monkeypatch):
    source = make_vfr(tmp_path / "long.mp4", tuple(index * 40 for index in range(72)))
    output = tmp_path / "out.mp4"
    monkeypatch.setattr("localsr.worker.server.send_message", lambda _: None)
    calls = []

    class Engine(IdentityEngine):
        def process_frames(self, frames, **kwargs):
            calls.append(len(frames))
            return frames

    worker = SimpleNamespace(cancel_event=threading.Event(), _emit_live_preview=lambda _: None)
    WorkerServer._run_temporal_video_job(
        worker,
        "test",
        {
            "video_path": str(source),
            "output_video_path": str(output),
            "preview_enabled": False,
        },
        lambda *_: Engine(),
    )
    assert calls == [33, 35, 8]
    assert timestamps(output) == pytest.approx(timestamps(source), abs=0.001)


def test_cancel_during_remux_preserves_existing_destination(tmp_path):
    source = make_vfr(tmp_path / "source.mp4")
    audio = tmp_path / "audio.mp4"
    ffmpeg(
        "-i",
        source,
        "-f",
        "lavfi",
        "-i",
        "sine=duration=3",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        audio,
    )
    output = tmp_path / "out.mp4"
    output.write_bytes(b"existing user output")
    cancel = threading.Event()

    def frames():
        yield from decode_timed_frames(str(source))
        cancel.set()

    with pytest.raises(InterruptedError):
        encode_video(
            frames(),
            str(output),
            fps=25,
            width=48,
            height=32,
            audio_source=str(audio),
            cancel_event=cancel,
        )
    assert output.read_bytes() == b"existing user output"
    assert not list(tmp_path.glob(".out.mp4.localsr-*.tmp"))
