"""Real legacy containers through decode, playback conversion and MP4 export."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import av
import numpy as np
import pytest

from localsr.core.external_ffmpeg import decodable_source, load_external_ffmpeg
from localsr.core.image_formats import VIDEO_INPUT_EXTENSIONS, is_video_input
from localsr.core.video_io import decode_timed_frames, encode_video, probe_video_preview
from localsr.core.video_playback import prepare_playback


def user_ffmpeg():
    """The FFmpeg a user installed; LocalSR itself bundles no patent-licensed codecs."""
    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("needs a user-installed ffmpeg")
    return load_external_ffmpeg(binary)


def make_clip(path, video="mjpeg", audio="pcm_s16le", container="avi", duration=1.2):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("fixture generation needs ffmpeg; production uses PyAV")
    if (
        video == "libtheora"
        and "libtheora"
        not in subprocess.run(
            [ffmpeg, "-encoders"], capture_output=True, text=True, check=True, timeout=10
        ).stdout
    ):
        pytest.skip("fixture encoder libtheora is unavailable")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=176x144:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            str(duration),
            "-c:v",
            video,
            "-threads",
            "1",
            "-c:a",
            audio,
            "-f",
            container,
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    return path


@pytest.mark.parametrize(
    "extension,container,video,audio,needs_user_ffmpeg",
    [
        # Royalty-free or patent-expired formats open with LocalSR alone.
        ("avi", "avi", "mjpeg", "adpcm_ima_wav", False),
        ("mpg", "mpeg", "mpeg2video", "mp2", False),
        ("vob", "vob", "mpeg2video", "ac3", False),
        ("ogv", "ogg", "libtheora", "libvorbis", False),
        # Patent-licensed formats need the FFmpeg the user installed.
        ("avi", "avi", "mpeg4", "pcm_s16le", True),
        ("divx", "avi", "mpeg4", "mp3", True),
        ("wmv", "asf", "wmv2", "wmav2", True),
        ("mts", "mpegts", "libx264", "aac", True),
        ("flv", "flv", "flv", "mp3", True),
        ("3gp", "3gp", "h263", "aac", True),
    ],
)
def test_legacy_picture_audio_and_timing(
    tmp_path, extension, container, video, audio, needs_user_ffmpeg
):
    source = make_clip(tmp_path / f"source.{extension}", video, audio, container)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    assert is_video_input(source)
    external = None
    if needs_user_ffmpeg:
        with pytest.raises(ValueError, match="patent-licensed format"):
            probe_video_preview(str(source))
        external = user_ffmpeg()
    probe, jpeg = probe_video_preview(str(source), external_ffmpeg=external)
    assert (probe.width, probe.height) == (176, 144)
    assert jpeg.startswith(b"\xff\xd8")
    output = tmp_path / "output.mp4"
    messages = []
    with decodable_source(
        str(source), ffmpeg=external, temporary_directory=str(tmp_path), output_container="mp4"
    ) as (frame_source, audio_source):
        frames = list(decode_timed_frames(frame_source))
        assert len(frames) == 30
        encode_video(
            iter(frames),
            str(output),
            fps=25,
            width=176,
            height=144,
            audio_source=audio_source,
            warning_callback=messages.append,
        )
    assert not list(tmp_path.glob("localsr-external-*"))
    decoded = list(decode_timed_frames(str(output)))
    assert len(decoded) == 30
    np.testing.assert_allclose(
        [float(f.timestamp) for f in decoded],
        [float(f.timestamp - frames[0].timestamp) for f in frames],
        atol=0.001,
    )
    assert np.mean(np.abs(decoded[10].rgb.astype(float) - frames[10].rgb)) < 8
    with av.open(str(output)) as result:
        assert result.streams.video[0].codec_context.name == "libdav1d"
        assert result.streams.audio[0].codec_context.name in {"opus", "flac", "ac3"}
        samples = list(result.decode(audio=0))
        assert samples and np.max(np.abs(samples[5].to_ndarray())) > 0.02
        assert abs(sum(f.samples / f.sample_rate for f in samples) - 1.2) < 0.09
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_playback_copy_trim_and_cancellation_preserve_files(tmp_path):
    source = make_clip(tmp_path / "source.avi", duration=3)
    output = tmp_path / "playback.webm"
    events = []
    prepare_playback(source, output, progress=events.append)
    assert events[0]["frame"] > 0 and events[-1]["stage"] == "Playback copy ready"
    assert len(list(decode_timed_frames(str(output)))) == 75
    original_output = output.read_bytes()
    cancel = threading.Event()

    def stop(_event):
        cancel.set()

    with pytest.raises(InterruptedError):
        prepare_playback(source, output, progress=stop, cancel_event=cancel)
    assert output.read_bytes() == original_output
    assert not list(tmp_path.glob("*.tmp"))

    trimmed = tmp_path / "trimmed.mp4"
    encode_video(
        decode_timed_frames(str(source), 25, 49),
        str(trimmed),
        fps=25,
        width=176,
        height=144,
        audio_source=str(source),
        warning_callback=lambda _: None,
    )
    with av.open(str(trimmed)) as result:
        audio = list(result.decode(audio=0))
        assert abs(sum(f.samples / f.sample_rate for f in audio) - 1) < 0.03
        assert abs(float(audio[0].pts * audio[0].time_base)) < 0.025
    assert len(list(decode_timed_frames(str(trimmed)))) == 25


def test_video_extension_registration_agrees_across_desktop_entry_points():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text())
    registered = {
        "." + ext
        for entry in config["bundle"]["fileAssociations"]
        if entry.get("ext", [""])[0] == "mp4"
        for ext in entry["ext"]
    }
    assert registered == VIDEO_INPUT_EXTENSIONS
    rust = (root / "desktop/src-tauri/src/commands.rs").read_text()
    values = re.search(r"const VIDEO_EXTENSIONS:.*?=\s*&\[(.*?)\];", rust, re.S).group(1)
    assert {"." + value for value in re.findall(r'"([a-z0-9]+)"', values)} == registered
    dialog = (
        (root / "desktop/src/lib/api.ts")
        .read_text()
        .split("export async function chooseMediaFolder")[0]
    )
    explorer = (root / "desktop/src-tauri/src/integrations.rs").read_text()
    for ext in registered:
        assert f"'{ext[1:]}'" in dialog
        assert f"System.FileExtension:={ext}" in explorer
        assert is_video_input("holiday" + ext.upper())


def test_interlaced_anamorphic_recording_matches_progressive_reference(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("fixture generation needs ffmpeg")
    source = tmp_path / "tape.mpg"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=352x288:rate=50",
            "-t",
            "1.2",
            "-vf",
            "tinterlace=interleave_top,setsar=12/11",
            "-c:v",
            "mpeg2video",
            "-flags",
            "+ildct+ilme",
            "-threads",
            "1",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    with av.open(str(source)) as container:
        original = list(container.decode(video=0))
        assert all(f.interlaced_frame for f in original)
    probe, _ = probe_video_preview(str(source))
    assert (probe.width, probe.height) == (384, 288)
    frames = list(decode_timed_frames(str(source)))
    assert len(frames) == len(original) == 30
    np.testing.assert_allclose(
        [float(f.timestamp) for f in frames],
        [float(f.pts * f.time_base) for f in original],
        atol=0.00001,
    )
    reference = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            "bwdif=mode=send_frame:parity=auto:deint=interlaced,scale=384:288:flags=bicubic,format=rgb24",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "-",
        ],
        check=True,
        capture_output=True,
        timeout=20,
    ).stdout
    expected = np.frombuffer(reference, dtype=np.uint8).reshape(30, 288, 384, 3)
    assert np.mean(np.abs(np.stack([f.rgb for f in frames]).astype(float) - expected)) < 2
    selected = list(decode_timed_frames(str(source), 10, 14))
    assert len(selected) == 5
    np.testing.assert_array_equal(selected[0].rgb, frames[10].rgb)


def test_playback_helper_runs_without_torch_and_reports_failure_then_recovery(tmp_path):
    source = make_clip(tmp_path / "tape with spaces.avi")
    output = tmp_path / "preview.webm"
    root = Path(__file__).resolve().parents[1]
    launcher = (
        "import runpy,sys; sys.modules['torch']=None; "
        "sys.argv=['localsr.worker']+sys.argv[1:]; "
        "runpy.run_module('localsr.worker',run_name='__main__')"
    )
    environment = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    args = [sys.executable, "-c", launcher, "--video-playback-preview"]
    broken = tmp_path / "broken.avi"
    broken.write_bytes(b"not a video")
    output.write_bytes(b"existing output")
    failure = subprocess.run(
        args + [str(broken), str(output)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert failure.returncode == 1 and failure.stderr.strip()
    assert output.read_bytes() == b"existing output"
    result = subprocess.run(
        args + [str(source), str(output)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["frame"] > 0
    assert events[-1]["stage"] == "Playback copy ready"
    assert len(list(decode_timed_frames(str(output)))) == 30
    assert not list(tmp_path.glob("*.tmp"))
