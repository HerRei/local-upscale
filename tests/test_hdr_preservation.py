"""HDR preservation verifies samples, precision, timing and metadata together."""

import threading
from dataclasses import replace

import av
import numpy as np
import pytest
import torch
from test_hdr import make_hdr
from test_video_pipeline import DummyAdapter

from localsr.core.face_compositing import blend_tile_outputs
from localsr.core.hdr import anchor_hdr_detail, hdr_to_working_linear, working_linear_to_hdr
from localsr.core.inference import InferenceEngine
from localsr.core.video_io import decode_timed_frames, encode_video, probe_video
from localsr.core.video_pipeline import VideoJobConfig, run_video_job


@pytest.mark.parametrize("transfer", [16, 18])
def test_hdr_transfer_roundtrip_and_source_light_constraint(transfer):
    rng = np.random.default_rng(912)
    source = rng.uniform(0.01, 0.99, (19, 17, 3)).astype(np.float32)
    source[0, :3] = [[0, 0, 0], [1, 1, 1], [0, 0.5, 0.7]]
    enhanced = rng.uniform(0, 1, (76, 68, 3)).astype(np.float32)
    enhanced[:4, 8:12, 0] = 0
    restored = anchor_hdr_detail(source, enhanced, transfer)
    assert restored.dtype == np.float32 and np.isfinite(restored).all()
    assert restored.min() >= 0 and restored.max() <= 1.00001
    linear = hdr_to_working_linear(restored, transfer).reshape(19, 4, 17, 4, 3)
    np.testing.assert_allclose(
        linear.mean(axis=(1, 3)), hdr_to_working_linear(source, transfer), atol=4e-5
    )
    np.testing.assert_allclose(
        working_linear_to_hdr(hdr_to_working_linear(source, transfer), transfer), source, atol=2e-5
    )
    # A zero, constant R channel must not suppress genuine G/B detail.
    assert np.ptp(restored[:4, 8:12, 1]) > 0
    assert np.unique(restored).size > 256


def test_face_blending_preserves_float_precision():
    face = np.full((3, 4, 4), 0.5001, np.float32)
    general = np.full_like(face, 0.5009)
    result = blend_tile_outputs(face, general, np.full((4, 4), 0.5, np.float32))
    assert result.dtype == np.float32
    assert 0.5001 < result.mean() < 0.5009


@pytest.mark.parametrize("hdr_format,transfer", [("HLG", 18), ("PQ", 16)])
def test_ten_bit_export_retains_more_than_8bit_levels_and_bt2020_samples(
    tmp_path, hdr_format, transfer
):
    ramp = np.broadcast_to(
        np.linspace(0.02, 0.98, 1024, dtype=np.float32)[None, :, None], (32, 1024, 3)
    ).copy()
    output = tmp_path / "gradient.mp4"
    encode_video([ramp], str(output), fps=24, crf=0, width=1024, height=32, hdr_format=hdr_format)
    with av.open(str(output)) as container:
        stream = container.streams.video[0]
        ctx = stream.codec_context
        assert ctx.format.name == "yuv420p10le"
        assert (ctx.color_primaries, ctx.color_trc, ctx.colorspace, ctx.color_range) == (
            9,
            transfer,
            9,
            1,
        )
        assert ctx.name == "libdav1d"
        frame = next(container.decode(stream))
        # Verify the encoded Y plane really has >256 levels, independent of
        # the RGB decode/encode helper and any nominal 10-bit container label.
        y = np.frombuffer(frame.planes[0], dtype=np.uint16).reshape(
            32, frame.planes[0].line_size // 2
        )[:, :1024]
        assert np.unique(y).size > 700
        assert not any(sd.type.name == "DOVI_METADATA" for sd in frame.side_data)
    decoded = next(decode_timed_frames(str(output), hdr_mode="preserve")).rgb
    assert decoded.dtype == np.float32
    np.testing.assert_allclose(decoded, ramp, atol=0.003)
    assert probe_video(str(output)).hdr_format == hdr_format


@pytest.mark.parametrize("transfer", [16, 18])
def test_float_h_at_pipeline_preserves_timing_audio_colour_and_reports_first_frame_tiles(
    tmp_path, transfer
):
    source = make_hdr(tmp_path / "source.mkv", transfer)
    adapter = DummyAdapter(2)
    info = replace(adapter.model_info, architecture="HAT")
    engine = InferenceEngine(adapter)
    output = tmp_path / "hdr.mp4"
    events = []
    previews = []
    result = run_video_job(
        VideoJobConfig(
            video_path=str(source),
            model_path="dummy.pth",
            output_video_path=str(output),
            model_info=info,
            device_str="cpu",
            precision_str="fp16",
            tile_size=16,
            halo=2,
            safe_memory=False,
            hdr_mode="preserve",
            start_frame=1,
            end_frame=3,
            crf=0,
        ),
        engine,
        threading.Event(),
        tile_progress_cb=lambda *args: events.append(args),
        enhanced_frame_cb=lambda _i, _n, pixels: previews.append(pixels.copy()),
    )
    assert result.frames_processed == 3
    assert all(frame.dtype == np.float32 for frame in previews)
    assert any(e[0] == 0 and 0 < e[2] < e[3] and e[5] > 0 for e in events)
    assert events[0][5] is None
    decoded_source = list(decode_timed_frames(str(source), hdr_mode="preserve"))
    assert [
        float(f.timestamp) for f in decode_timed_frames(str(output), hdr_mode="preserve")
    ] == pytest.approx(
        [float(f.timestamp - decoded_source[1].timestamp) for f in decoded_source[1:4]], abs=0.001
    )
    with av.open(str(output)) as container:
        assert len(container.streams.audio) == 1
        assert len(list(container.decode(audio=0))) > 0
    decoded_out = list(decode_timed_frames(str(output), hdr_mode="preserve"))
    for original, restored in zip(decoded_source[1:4], decoded_out, strict=True):
        # Chroma subsampling/codec boundaries allow a small roundtrip error.
        assert np.abs(restored.rgb[::2, ::2] - original.rgb).mean() < 0.015


def test_engine_float_output_does_not_quantize_the_model_result():
    adapter = DummyAdapter(2)
    engine = InferenceEngine(adapter)
    engine.load_model("dummy.pth", "cpu", "fp32", adapter.model_info)
    signal = torch.linspace(0.1, 0.9, 512).repeat(3, 8, 1)
    output = engine.process_frame(
        signal, adapter.model_info, 128, 2, threading.Event(), lambda *_: None, float_output=True
    )
    assert output.dtype == np.float32
    assert np.unique(output[0]).size == 512
    np.testing.assert_allclose(output[:, ::2, ::2], signal.numpy(), atol=1e-7)


def test_uint8_is_refused_by_hdr_encoder_without_publishing_output(tmp_path):
    output = tmp_path / "invalid.mp4"
    with pytest.raises(Exception, match="float32"):
        encode_video(
            [np.zeros((32, 32, 3), np.uint8)],
            str(output),
            fps=24,
            width=32,
            height=32,
            hdr_format="HLG",
        )
    assert not output.exists()
