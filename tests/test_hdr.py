"""HDR import: reference transfer values and the encoded SDR media contract."""

import base64
import io
import json
import re
import threading
from types import SimpleNamespace

import av
import numpy as np
import pytest
from PIL import Image
from test_video_timing import IdentityEngine, ffmpeg, make_vfr, run_standard, timestamps

from localsr.core.hdr import hdr_to_linear_nits, tone_map_to_sdr
from localsr.core.video_io import decode_timed_frames, probe_video, probe_video_preview
from localsr.worker.server import WorkerServer


@pytest.mark.parametrize(
    "transfer,signal,nits",
    [(18, 0.75, 203.152), (18, 1, 1000), (16, 0.5080784, 100), (16, 1, 10000)],
)
def test_reference_neutral_transfer_values(transfer, signal, nits):
    decoded = hdr_to_linear_nits(np.full((1, 1, 3), signal), transfer)
    np.testing.assert_allclose(decoded, nits, rtol=0.0002)


@pytest.mark.parametrize("transfer", [16, 18])
def test_tone_mapping_preserves_gray_order_and_is_independent_of_frame_context(transfer):
    ramp = np.repeat(np.linspace(0, 1, 1024, dtype=np.float32)[None, :, None], 3, axis=2)
    result = tone_map_to_sdr(ramp, transfer, 9)
    assert result.dtype == np.uint8
    assert result[0, 0].tolist() == [0, 0, 0]
    assert result[0, -1].tolist() == [255, 255, 255]
    assert (np.diff(result[0, :, 0].astype(int)) >= 0).all()
    assert np.ptp(result.astype(int), axis=-1).max() <= 1
    np.testing.assert_array_equal(
        tone_map_to_sdr(ramp[:, 400:500], transfer, 9), result[:, 400:500]
    )
    colors = np.eye(3, dtype=np.float32)[None, :, :]
    mapped = tone_map_to_sdr(colors, transfer, 9)
    assert np.argmax(mapped, axis=-1).tolist() == [[0, 1, 2]]
    assert np.isfinite(mapped).all()


def test_unsupported_hdr_colorimetry_is_actionable():
    with pytest.raises(ValueError, match="BT.2020 HLG/PQ"):
        tone_map_to_sdr(np.zeros((1, 1, 3)), 18, 2)
    with pytest.raises(ValueError, match="HLG and PQ"):
        tone_map_to_sdr(np.zeros((1, 1, 3)), 1, 9)


@pytest.mark.parametrize("transfer", [16, 18])
def test_planar_and_rotated_decoder_frames_match_interleaved_color_conversion(transfer):
    planar = np.random.default_rng(42).random((3, 48, 64), dtype=np.float32)
    strided = np.rot90(planar.transpose(1, 2, 0))
    assert not strided.flags.c_contiguous
    interleaved = np.ascontiguousarray(strided)
    np.testing.assert_allclose(
        hdr_to_linear_nits(strided, transfer), hdr_to_linear_nits(interleaved, transfer), rtol=1e-6
    )
    np.testing.assert_array_equal(
        tone_map_to_sdr(strided, transfer, 9), tone_map_to_sdr(interleaved, transfer, 9)
    )


def make_hdr(path, transfer, codec="libsvtav1", audio="libopus"):
    # Encode a real ten-bit source with explicit BT.2020 matrix/range tags. AV1 is
    # bundled; HEVC/AAC sources exercise the user-installed FFmpeg route.
    untagged = make_vfr(path.with_suffix(".source.mp4"))
    video_options = (
        ["-x265-params", "log-level=error:pools=1:frame-threads=1"]
        if codec == "libx265"
        else ["-preset", "12", "-crf", "20"]
    )
    ffmpeg(
        "-i",
        untagged,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-vf",
        f"scale=out_color_matrix=bt2020:out_range=tv,format=yuv420p10le,setparams=color_primaries=bt2020:color_trc={'smpte2084' if transfer == 16 else 'arib-std-b67'}:colorspace=bt2020nc:range=limited",
        "-c:v",
        codec,
        *video_options,
        "-color_trc",
        "smpte2084" if transfer == 16 else "arib-std-b67",
        "-color_primaries",
        "bt2020",
        "-colorspace",
        "bt2020nc",
        "-color_range",
        "tv",
        "-c:a",
        audio,
        "-shortest",
        "-fps_mode",
        "passthrough",
        path,
    )
    return path


@pytest.mark.parametrize("transfer", [16, 18])
@pytest.mark.parametrize("temporal", [False, True])
def test_hdr_preview_and_export_keep_timing_audio_and_explicit_sdr_tags(
    tmp_path, monkeypatch, transfer, temporal
):
    source = make_hdr(tmp_path / "hdr.mkv", transfer)
    stages = []
    probe, jpeg = probe_video_preview(str(source), 128, stages.append)
    assert probe.hdr_format == ("PQ" if transfer == 16 else "HLG")
    assert stages == ["opening", "decoding_video", "converting_hdr", "preparing_preview"]
    assert Image.open(io.BytesIO(jpeg)).size == (48, 32)
    with pytest.raises(ValueError, match="HDR.*SDR"):
        list(decode_timed_frames(str(source)))
    decoded = list(decode_timed_frames(str(source), hdr_mode="tone_map"))
    output = tmp_path / "sdr.mp4"
    if temporal:
        monkeypatch.setattr("localsr.worker.server.send_message", lambda _: None)
        worker = SimpleNamespace(cancel_event=threading.Event(), _emit_live_preview=lambda _: None)
        WorkerServer._run_temporal_video_job(
            worker,
            "test",
            {
                "video_path": str(source),
                "output_video_path": str(output),
                "hdr_mode": "tone_map",
                "start_frame": 1,
                "end_frame": 6,
                "preview_enabled": False,
            },
            lambda *_: IdentityEngine(),
        )
    else:
        run_standard(source, output, hdr_mode="tone_map", start_frame=1, end_frame=6)
    assert timestamps(output) == pytest.approx(
        [float(frame.timestamp - decoded[1].timestamp) for frame in decoded[1:7]], abs=0.001
    )
    assert probe_video(str(output)).hdr_format == ""
    with av.open(str(output)) as container:
        assert len(container.streams.audio) == 1
        stream = container.streams.video[0]
        assert stream.codec_context.format.name == "yuv420p"
        assert (
            stream.codec_context.color_trc,
            stream.codec_context.color_primaries,
            stream.codec_context.colorspace,
            stream.codec_context.color_range,
        ) == (1, 1, 1, 1)
        frame = next(container.decode(stream))
        assert not any(
            data.type.name in {"DOVI_METADATA", "MASTERING_DISPLAY_METADATA"}
            for data in frame.side_data
        )
        # Compare the round trip in BT.709, allowing AV1/chroma quantization.
        rgb = frame.reformat(format="rgb24", src_colorspace="ITU709").to_ndarray()
        assert np.abs(rgb.astype(float) - decoded[1].rgb).mean() < 8


def run_probe(monkeypatch, source):
    events = []
    monkeypatch.setattr(
        "localsr.worker.server.send_message",
        lambda event: events.append(json.loads(event.to_json())),
    )
    server = WorkerServer()
    monkeypatch.setattr(server, "reader_thread_func", lambda: None)
    server.message_queue.put(
        {"type": "media_probe_request", "data": {"media_path": str(source), "max_dimension": 128}}
    )
    server.message_queue.put({"type": "shutdown_request", "data": {}})
    server.run()
    return [event for event in events if event["type"].startswith("media_")]


def test_image_worker_reports_real_phases_then_preview(monkeypatch, tmp_path):
    source = tmp_path / "large.png"
    Image.new("RGB", (4096, 3072), (50, 100, 150)).save(source)
    events = run_probe(monkeypatch, source)
    assert [event["data"].get("stage", event["type"]) for event in events] == [
        "decoding_image",
        "preparing_preview",
        "media_info",
    ]
    info = events[-1]["data"]
    assert (info["width"], info["height"]) == (4096, 3072)
    assert Image.open(io.BytesIO(base64.b64decode(info["jpeg_base64"]))).size == (128, 96)


def test_video_thumbnail_failure_is_terminal_not_blank_ready(monkeypatch, tmp_path):
    source = make_vfr(tmp_path / "source.mp4")

    def fail(*_args, **_kwargs):
        raise ValueError("Thumbnail encoder failed")

    monkeypatch.setattr("localsr.core.video_io.thumbnail_jpeg", fail)
    events = run_probe(monkeypatch, source)
    assert events[0]["data"]["stage"] == "opening"
    assert events[-1] == {
        "type": "media_probe_failed",
        "data": {"media_path": str(source), "error_message": "Thumbnail encoder failed"},
    }
    assert not any(event["type"] == "media_info" for event in events)


@pytest.mark.parametrize("has_standard_audio", [False, True])
def test_unknown_mov_audio_keeps_standard_track_or_warns_actionably(tmp_path, has_standard_audio):
    video = make_vfr(tmp_path / "video.mp4")
    source = tmp_path / "extra-audio.mp4"
    mappings = ["-map", "0:v", "-map", "1:a"]
    if has_standard_audio:
        mappings += ["-map", "1:a"]
    ffmpeg(
        "-i",
        video,
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        *mappings,
        "-c:v",
        "copy",
        "-c:a",
        "libopus",
        "-shortest",
        source,
    )
    # Unknown audio sample entry, as with Apple APAC on a decoder build that
    # lacks that codec. Leave a real, decodable Opus fallback when requested.
    content = bytearray(source.read_bytes())
    sample_entry = [
        match.start()
        for match in re.finditer(b"Opus", content)
        if 36 <= int.from_bytes(content[match.start() - 4 : match.start()], "big") <= 512
    ][-1]
    entry_end = sample_entry - 4 + int.from_bytes(content[sample_entry - 4 : sample_entry], "big")
    descriptor = content.index(b"dOps", sample_entry, entry_end)
    content[descriptor : descriptor + 4] = b"free"
    content[sample_entry : sample_entry + 4] = b"zzzz"
    source.write_bytes(content)
    with av.open(str(source)) as container:
        assert container.streams.audio[-1].codec_context is None
    output = tmp_path / "output.mp4"
    probe, _ = probe_video_preview(str(source))
    if has_standard_audio:
        assert "additional audio track" in probe.audio_warning
        with pytest.warns(UserWarning, match="additional audio track"):
            run_standard(source, output, end_frame=2)
        with av.open(str(output)) as container:
            assert len(container.streams.audio) == 1
            assert container.streams.audio[0].codec_context.name == "opus"
            assert len(list(container.decode(audio=0))) > 0
    else:
        assert "no sound" in probe.audio_warning
        with pytest.warns(UserWarning, match="no sound"):
            run_standard(source, output, end_frame=2)
        with av.open(str(output)) as container:
            assert not container.streams.audio
            assert len(list(container.decode(video=0))) == 3
