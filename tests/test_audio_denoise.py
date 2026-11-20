"""Tests for video speech audio denoising (DeepFilterNet3 pipeline & test double)."""

from __future__ import annotations

import shutil
import threading
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
import torch
from torch import nn

from localsr.core.audio_denoise import (
    AUDIO_DENOISER_FACTORIES,
    DeepFilterNet3Denoiser,
    DPDFNetDenoiser,
    denoise_video_audio,
    load_denoiser,
)
from localsr.core.inference import InferenceEngine
from localsr.core.model_adapter import NormalizedModelInfo
from localsr.core.model_catalog import (
    AUDIO_CATALOG_BY_ID,
    AUDIO_MODEL_CATALOG,
    DEFAULT_AUDIO_MODEL_ID,
)
from localsr.core.video_io import probe_video
from localsr.core.video_pipeline import VideoJobConfig, run_video_job

REPO_ROOT = Path(__file__).resolve().parent.parent
DF3_ARCHIVE = REPO_ROOT / ".cache" / "DeepFilterNet3_onnx.tar.gz"
DPDFNET_MODEL = REPO_ROOT / ".cache" / "dpdfnet8_48khz_hr.onnx"


class _DummyModel(nn.Module):
    def __init__(self, scale: int) -> None:
        super().__init__()
        self.scale = scale

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.nn.functional.interpolate(x, scale_factor=self.scale, mode="nearest")


class _DummyAdapter:
    def __init__(self, scale: int = 2) -> None:
        self.model_info = NormalizedModelInfo(
            architecture="Dummy",
            scale=scale,
            in_channels=3,
            out_channels=3,
            tiling_supported=True,
            half_supported=False,
            size_requirements_min=1,
            size_requirements_mult=1,
            filename="dummy.pth",
            warnings=[],
        )

    def load(self, path, device, precision):
        return _DummyModel(self.model_info.scale).to(device).to(precision), None

    def release(self) -> None:
        pass


class _ToneKeepingStubDenoiser:
    """Test double that removes high-frequency white noise while keeping speech-band tones."""

    sample_rate = 48_000

    def __init__(self, cutoff_hz: float = 1_500.0) -> None:
        self.cutoff_hz = cutoff_hz
        self.calls = 0

    def denoise_mono(
        self,
        samples: np.ndarray,
        *,
        cancel_event: threading.Event | None = None,
        progress_cb=None,
    ) -> np.ndarray:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("video job cancelled")
        self.calls += 1
        freqs = np.fft.rfftfreq(len(samples), d=1.0 / self.sample_rate)
        spec = np.fft.rfft(samples.astype(np.float32, copy=False))
        # Suppress white noise above the voice tone band while keeping <= cutoff_hz.
        spec[freqs > self.cutoff_hz] *= 0.02
        out = np.fft.irfft(spec, n=len(samples)).astype(np.float32)
        if progress_cb is not None:
            progress_cb(len(samples), max(1, len(samples)))
        return np.clip(out, -1.0, 1.0)

    def denoise_channels(
        self,
        channels: np.ndarray,
        *,
        cancel_event: threading.Event | None = None,
        progress_cb=None,
    ) -> np.ndarray:
        out = np.empty_like(channels, dtype=np.float32)
        for ch_idx in range(channels.shape[0]):
            out[ch_idx] = self.denoise_mono(
                channels[ch_idx], cancel_event=cancel_event, progress_cb=progress_cb
            )
        return out


def _make_video_with_tone_and_noise(
    path: Path,
    *,
    container_format: str = "matroska",
    audio_codec: str = "flac",
    sample_rate: int = 48_000,
    channels: int = 1,
    duration_seconds: float = 1.5,
    fps: int = 12,
    speech_like: bool = False,
    noise_std: float = 0.12,
    include_audio: bool = True,
) -> tuple[Path, np.ndarray | None]:
    """Create a deterministic VP9 video with tone + white noise (mono/stereo) or silent."""
    n_frames = max(2, int(round(duration_seconds * fps)))
    n_samples = int(round(duration_seconds * sample_rate))
    clean_signal: np.ndarray | None = None

    container = av.open(str(path), mode="w", format=container_format)
    video = container.add_stream("libvpx-vp9", rate=Fraction(fps, 1))
    video.width = 32
    video.height = 24
    video.pix_fmt = "yuv420p"
    video.options = {"crf": "20", "b": "0", "deadline": "realtime", "cpu-used": "8"}

    pending_video_packets = []
    for idx in range(n_frames):
        rgb = np.full((24, 32, 3), (idx * 23) % 240, dtype=np.uint8)
        rgb[:, :, 1] = 255 - ((idx * 19) % 240)
        vframe = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        vframe.pts = idx
        pending_video_packets.extend(video.encode(vframe))

    if include_audio:
        t = np.arange(n_samples, dtype=np.float32) / float(sample_rate)
        if speech_like:
            env = 0.5 + 0.5 * np.sin(2.0 * np.pi * 3.2 * t)
            f0 = 180.0 + 35.0 * np.sin(2.0 * np.pi * 2.1 * t)
            phase = 2.0 * np.pi * np.cumsum(f0) / float(sample_rate)
            clean_mono = (
                0.25
                * env
                * (np.sin(phase) + 0.5 * np.sin(2.0 * phase) + 0.25 * np.sin(3.0 * phase))
            )
        else:
            clean_mono = 0.35 * np.sin(2.0 * np.pi * 440.0 * t)
        rng = np.random.default_rng(seed=137)
        noise = rng.normal(0.0, noise_std, size=(channels, n_samples)).astype(np.float32)
        clean_signal = np.tile(clean_mono, (channels, 1)).astype(np.float32)
        if channels == 2:
            clean_signal[1] *= 0.8
        noisy = np.clip(clean_signal + noise, -0.98, 0.98)

        layout = "stereo" if channels == 2 else "mono"
        audio = container.add_stream(audio_codec, rate=sample_rate)
        audio.codec_context.layout = layout
        if not audio.codec_context.is_open:
            audio.codec_context.open()
        target_fmt = "fltp" if audio_codec == "libopus" else "s16"
        resampler = av.AudioResampler(format=target_fmt, layout=layout, rate=sample_rate)
        chunk = 960
        written = 0
        for pos in range(0, n_samples, chunk):
            piece = np.ascontiguousarray(noisy[:, pos : pos + chunk], dtype=np.float32)
            aframe = av.AudioFrame.from_ndarray(piece, format="fltp", layout=layout)
            aframe.sample_rate = sample_rate
            aframe.pts = pos
            aframe.time_base = Fraction(1, sample_rate)
            for resampled in resampler.resample(aframe):
                resampled.pts = written
                resampled.time_base = Fraction(1, sample_rate)
                written += resampled.samples
                for packet in audio.encode(resampled):
                    container.mux(packet)
        for resampled in resampler.resample(None):
            resampled.pts = written
            resampled.time_base = Fraction(1, sample_rate)
            written += resampled.samples
            for packet in audio.encode(resampled):
                container.mux(packet)
        for packet in audio.encode():
            container.mux(packet)

    for packet in pending_video_packets:
        container.mux(packet)
    for packet in video.encode():
        container.mux(packet)
    container.close()
    return path, clean_signal


def _read_audio_arrays(path: Path) -> tuple[int, np.ndarray]:
    """Decode all samples of the first audio stream as (channels, samples) float32."""
    with av.open(str(path), mode="r") as container:
        audio_streams = list(container.streams.audio)
        assert audio_streams, f"expected an audio stream in {path}"
        stream = audio_streams[0]
        sample_rate = int(stream.rate or stream.codec_context.sample_rate)
        channels = int(stream.codec_context.channels)
        layout = "stereo" if channels == 2 else "mono"
        resampler = av.AudioResampler(format="fltp", layout=layout, rate=sample_rate)
        chunks: list[np.ndarray] = []
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                chunks.append(converted.to_ndarray().astype(np.float32))
        for converted in resampler.resample(None):
            chunks.append(converted.to_ndarray().astype(np.float32))
        return sample_rate, np.concatenate(chunks, axis=1)


def _tone_and_noise_metrics(
    samples: np.ndarray, sample_rate: int, tone_hz: float = 440.0
) -> tuple[float, float]:
    """Return (tone magnitude around tone_hz, mean noise power above 2 kHz)."""
    mono = samples[0]
    freqs = np.fft.rfftfreq(len(mono), d=1.0 / sample_rate)
    mag = np.abs(np.fft.rfft(mono)) / max(1, len(mono))
    tone_mask = np.abs(freqs - tone_hz) <= 15.0
    high_noise_mask = freqs > 2_000.0
    return float(mag[tone_mask].max()), float(np.mean(mag[high_noise_mask] ** 2))


@pytest.mark.parametrize("channels,sample_rate", [(1, 48_000), (2, 44_100), (1, 11_025)])
def test_audio_cleanup_reduces_noise_keeps_tone_and_preserves_video(
    tmp_path: Path, channels: int, sample_rate: int
) -> None:
    src = tmp_path / "noisy.mkv"
    out = tmp_path / "clean.mkv"
    _make_video_with_tone_and_noise(
        src,
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=1.5,
        fps=12,
    )
    stub = _ToneKeepingStubDenoiser()
    progress_events: list[tuple[int, int, float, float | None]] = []
    adapter = _DummyAdapter(scale=2)
    engine = InferenceEngine(adapter)

    run_video_job(
        VideoJobConfig(
            video_path=str(src),
            model_path="dummy.pth",
            output_video_path=str(out),
            model_info=adapter.model_info,
            device_str="cpu",
            precision_str="fp32",
            tile_size=64,
            halo=0,
            safe_memory=False,
            container="mkv",
            video_codec="vp9",
            audio_denoise=True,
            audio_denoiser=stub,
        ),
        engine=engine,
        cancel_event=threading.Event(),
        audio_progress_cb=lambda done, total, elapsed, rem: progress_events.append(
            (done, total, elapsed, rem)
        ),
    )

    assert stub.calls >= channels
    assert progress_events and progress_events[-1][0] <= progress_events[-1][1]

    in_probe = probe_video(str(src))
    out_probe = probe_video(str(out))
    assert in_probe.has_audio is True
    assert out_probe.has_audio is True
    assert out_probe.frame_count == in_probe.frame_count
    assert out_probe.fps == pytest.approx(in_probe.fps, abs=0.05)
    assert out_probe.width == in_probe.width * 2
    assert out_probe.height == in_probe.height * 2

    in_rate, in_audio = _read_audio_arrays(src)
    out_rate, out_audio = _read_audio_arrays(out)
    assert out_rate == in_rate
    assert out_audio.shape[0] == channels
    assert abs(out_audio.shape[1] - in_audio.shape[1]) <= max(128, in_rate // 20)

    in_tone, in_noise_pwr = _tone_and_noise_metrics(in_audio, in_rate)
    out_tone, out_noise_pwr = _tone_and_noise_metrics(out_audio, out_rate)
    assert out_tone > 0.7 * in_tone
    assert out_noise_pwr < 0.05 * in_noise_pwr


def test_audio_cleanup_skips_silent_video_and_supports_mp4_opus(tmp_path: Path) -> None:
    silent_src = tmp_path / "silent.mkv"
    silent_out = tmp_path / "silent_out.mkv"
    _make_video_with_tone_and_noise(silent_src, include_audio=False)
    assert probe_video(str(silent_src)).has_audio is False

    stub = _ToneKeepingStubDenoiser()
    adapter = _DummyAdapter(scale=2)
    engine = InferenceEngine(adapter)
    run_video_job(
        VideoJobConfig(
            video_path=str(silent_src),
            model_path="dummy.pth",
            output_video_path=str(silent_out),
            model_info=adapter.model_info,
            device_str="cpu",
            precision_str="fp32",
            tile_size=64,
            halo=0,
            safe_memory=False,
            container="mkv",
            video_codec="vp9",
            audio_denoise=True,
            audio_denoiser=stub,
        ),
        engine=engine,
        cancel_event=threading.Event(),
    )
    assert stub.calls == 0
    assert probe_video(str(silent_out)).has_audio is False

    mp4_src = tmp_path / "noisy.mp4"
    mp4_out = tmp_path / "clean.mp4"
    _make_video_with_tone_and_noise(
        mp4_src,
        container_format="mp4",
        audio_codec="libopus",
        sample_rate=48_000,
        channels=2,
    )
    run_video_job(
        VideoJobConfig(
            video_path=str(mp4_src),
            model_path="dummy.pth",
            output_video_path=str(mp4_out),
            model_info=adapter.model_info,
            device_str="cpu",
            precision_str="fp32",
            tile_size=64,
            halo=0,
            safe_memory=False,
            container="mp4",
            video_codec="vp9",
            audio_denoise=True,
            audio_denoiser=stub,
        ),
        engine=engine,
        cancel_event=threading.Event(),
    )
    assert stub.calls > 0
    assert probe_video(str(mp4_out)).has_audio is True


def test_audio_cleanup_cancels_cleanly(tmp_path: Path) -> None:
    src = tmp_path / "noisy.mkv"
    cleaned = tmp_path / "cleaned.mkv"
    _make_video_with_tone_and_noise(src)
    cancel_event = threading.Event()
    cancel_event.set()
    with pytest.raises(InterruptedError, match="cancelled"):
        denoise_video_audio(
            str(src),
            cleaned,
            _ToneKeepingStubDenoiser(),
            cancel_event=cancel_event,
        )


@pytest.mark.skipif(not DF3_ARCHIVE.is_file(), reason="DeepFilterNet3 archive not cached locally")
def test_real_deepfilternet3_onnx_denoises_speech_across_chunks(tmp_path: Path) -> None:
    bundle = tmp_path / "deepfilternet3"
    bundle.mkdir()
    shutil.copy2(DF3_ARCHIVE, bundle / DF3_ARCHIVE.name)

    src = tmp_path / "speech.mkv"
    out = tmp_path / "speech_clean.mkv"
    # 10.5 s spans more than one 8.0 s chunk to exercise overlap + 480-sample crossfade.
    _make_video_with_tone_and_noise(
        src,
        sample_rate=48_000,
        channels=1,
        duration_seconds=10.5,
        fps=4,
        speech_like=True,
        noise_std=0.04,
    )
    denoiser = DeepFilterNet3Denoiser(bundle)
    denoise_video_audio(str(src), out, denoiser)

    in_rate, in_audio = _read_audio_arrays(src)
    out_rate, out_audio = _read_audio_arrays(out)
    assert out_rate == in_rate == 48_000
    assert abs(out_audio.shape[1] - in_audio.shape[1]) <= 64

    freqs = np.fft.rfftfreq(out_audio.shape[1], d=1.0 / out_rate)
    in_spec = np.abs(np.fft.rfft(in_audio[0])) / out_audio.shape[1]
    out_spec = np.abs(np.fft.rfft(out_audio[0])) / out_audio.shape[1]
    speech_band = (freqs >= 120.0) & (freqs <= 700.0)
    noise_band = freqs > 4_000.0
    assert float(out_spec[speech_band].max()) > 0.6 * float(in_spec[speech_band].max())
    assert float(np.mean(out_spec[noise_band] ** 2)) < 0.05 * float(
        np.mean(in_spec[noise_band] ** 2)
    )


def test_audio_denoiser_registry_dispatches_and_rejects_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    assert AUDIO_DENOISER_FACTORIES["deepfilternet3"] is DeepFilterNet3Denoiser
    assert AUDIO_DENOISER_FACTORIES["dpdfnet"] is DPDFNetDenoiser

    seen: list[tuple[str, Path]] = []

    def fake_df3(path: Path) -> _ToneKeepingStubDenoiser:
        seen.append(("deepfilternet3", path))
        return _ToneKeepingStubDenoiser()

    def fake_dpdf(path: Path) -> _ToneKeepingStubDenoiser:
        seen.append(("dpdfnet", path))
        return _ToneKeepingStubDenoiser()

    monkeypatch.setitem(AUDIO_DENOISER_FACTORIES, "deepfilternet3", fake_df3)
    monkeypatch.setitem(AUDIO_DENOISER_FACTORIES, "dpdfnet", fake_dpdf)

    df3_dir = tmp_path / "df3"
    df3_dir.mkdir()
    (df3_dir / "DeepFilterNet3_onnx.tar.gz").write_bytes(b"stub")
    load_denoiser("deepfilternet3", df3_dir, filenames=["DeepFilterNet3_onnx.tar.gz"])

    dpdf_dir = tmp_path / "dpdf"
    dpdf_dir.mkdir()
    (dpdf_dir / "dpdfnet8_48khz_hr.onnx").write_bytes(b"stub")
    load_denoiser("dpdfnet", dpdf_dir, filenames=["dpdfnet8_48khz_hr.onnx"])

    assert seen == [
        ("deepfilternet3", df3_dir / "DeepFilterNet3_onnx.tar.gz"),
        ("dpdfnet", dpdf_dir / "dpdfnet8_48khz_hr.onnx"),
    ]
    with pytest.raises(ValueError, match="Unsupported audio cleanup engine"):
        load_denoiser("unknown_engine", tmp_path)


def test_audio_catalog_uniqueness_default_and_export() -> None:
    import importlib.util
    import json

    assert DEFAULT_AUDIO_MODEL_ID in AUDIO_CATALOG_BY_ID
    assert len({m.model_id for m in AUDIO_MODEL_CATALOG}) == len(AUDIO_MODEL_CATALOG)
    assert len({m.family for m in AUDIO_MODEL_CATALOG}) == len(AUDIO_MODEL_CATALOG)
    assert all(m.summary for m in AUDIO_MODEL_CATALOG)

    spec = importlib.util.spec_from_file_location(
        "export_desktop_catalog", REPO_ROOT / "scripts" / "export_desktop_catalog.py"
    )
    assert spec and spec.loader
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)

    manifest = exporter.build_manifest()
    by_id = {entry["model_id"]: entry for entry in manifest["audio_models"]}
    assert set(by_id) == {"deepfilternet3", "dpdfnet8_48khz_hr"}
    assert by_id["deepfilternet3"]["engine_id"] == "localsr.audio.deepfilternet3"
    assert by_id["dpdfnet8_48khz_hr"]["engine_id"] == "localsr.audio.dpdfnet"
    assert by_id["dpdfnet8_48khz_hr"]["summary"] == "Stronger speech noise removal"
    shipped = json.loads(
        (REPO_ROOT / "desktop" / "src-tauri" / "resources" / "model-catalog.json").read_text()
    )
    assert shipped == manifest


@pytest.mark.skipif(not DPDFNET_MODEL.is_file(), reason="DPDFNet ONNX model not cached locally")
def test_real_dpdfnet_onnx_denoises_speech_and_lowers_gap_noise(tmp_path: Path) -> None:
    bundle = tmp_path / "dpdfnet8_48khz_hr"
    bundle.mkdir()
    shutil.copy2(DPDFNET_MODEL, bundle / DPDFNET_MODEL.name)

    sr = 48_000
    duration_s = 4.0
    n_samples = int(duration_s * sr)
    t = np.arange(n_samples, dtype=np.float32) / float(sr)
    env = 0.5 + 0.5 * np.sin(2.0 * np.pi * 3.2 * t)
    f0 = 180.0 + 35.0 * np.sin(2.0 * np.pi * 2.1 * t)
    phase = 2.0 * np.pi * np.cumsum(f0) / float(sr)
    speech = (
        0.25 * env * (np.sin(phase) + 0.5 * np.sin(2.0 * phase) + 0.25 * np.sin(3.0 * phase))
    ).astype(np.float32)
    # Silent gap in [1.5s, 2.5s] so noise suppression can be measured cleanly without speech.
    gap_start, gap_end = int(1.5 * sr), int(2.5 * sr)
    speech[gap_start:gap_end] = 0.0
    rng = np.random.default_rng(seed=271)
    noisy = np.clip(speech + rng.normal(0.0, 0.04, size=n_samples).astype(np.float32), -0.98, 0.98)

    denoiser = load_denoiser("dpdfnet", bundle, filenames=[DPDFNET_MODEL.name])
    out = denoiser.denoise_channels(noisy[None, :])

    assert out.shape == (1, n_samples)
    assert np.all(np.isfinite(out))

    # Measure inside the interior of the silent gap (avoiding edge transitions).
    inner = slice(gap_start + 2400, gap_end - 2400)
    in_gap_energy = float(np.mean(noisy[inner] ** 2))
    out_gap_energy = float(np.mean(out[0, inner] ** 2))
    assert out_gap_energy < 0.05 * in_gap_energy
