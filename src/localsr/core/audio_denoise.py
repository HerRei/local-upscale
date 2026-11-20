"""Speech noise suppression for video audio tracks via DeepFilterNet3 ONNX.

Runs the official DeepFilterNet3 ONNX export (``enc.onnx``, ``erb_dec.onnx``,
``df_dec.onnx``, ``config.ini``) through ``onnxruntime`` on CPU. Audio is
decoded via PyAV (after any system-codec or external-FFmpeg pre-conversion),
resampled to 48 kHz, denoised in overlapping chunks per channel, resampled
back to each track's original sample rate, and written to a lossless FLAC
intermediate for muxing/re-encoding by the selected export path.
"""

from __future__ import annotations

import configparser
import math
import shutil
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Protocol

import av
import numpy as np

from .eta import RemainingTime
from .output_publish import owned_temporary

DF3_SAMPLE_RATE = 48000
_CHUNK_SECONDS = 8.0
_LEFT_OVERLAP_SECONDS = 1.0
_RIGHT_OVERLAP_SECONDS = 0.2
_CROSSFADE_SAMPLES = 480


class AudioDenoiser(Protocol):
    """Interface for a 48 kHz multi-channel audio denoiser."""

    def denoise_channels(
        self,
        channels: np.ndarray,
        *,
        cancel_event: threading.Event | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> np.ndarray: ...


@dataclass(frozen=True)
class DeepFilterParams:
    sr: int = 48000
    fft_size: int = 960
    hop_size: int = 480
    nb_erb: int = 32
    nb_df: int = 96
    norm_tau: float = 1.0
    min_nb_erb_freqs: int = 2
    df_order: int = 5
    df_lookahead: int = 2
    conv_lookahead: int = 2
    mask_pf: bool = False
    pf_beta: float = 0.02


def _read_bundle_members(bundle_path: Path) -> tuple[DeepFilterParams, dict[str, bytes]]:
    needed = ("config.ini", "enc.onnx", "erb_dec.onnx", "df_dec.onnx")
    blobs: dict[str, bytes] = {}
    archive_path = bundle_path
    if bundle_path.is_dir():
        for name in needed:
            candidate = bundle_path / name
            if candidate.is_file():
                blobs[name] = candidate.read_bytes()
        if len(blobs) < len(needed):
            tar_candidate = bundle_path / "DeepFilterNet3_onnx.tar.gz"
            if tar_candidate.is_file():
                archive_path = tar_candidate
    if not archive_path.is_dir() and len(blobs) < len(needed) and archive_path.is_file():
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                basename = Path(member.name).name
                if basename in needed:
                    extracted = archive.extractfile(member)
                    if extracted is not None:
                        blobs[basename] = extracted.read()
    missing = [name for name in needed if name not in blobs]
    if missing:
        raise FileNotFoundError(
            f"DeepFilterNet3 bundle at {bundle_path} is missing: {', '.join(missing)}"
        )

    parser = configparser.ConfigParser()
    parser.read_string(blobs["config.ini"].decode("utf-8"))
    df = parser["df"]
    net = parser["deepfilternet"]
    params = DeepFilterParams(
        sr=int(df.get("sr", "48000")),
        fft_size=int(df.get("fft_size", "960")),
        hop_size=int(df.get("hop_size", "480")),
        nb_erb=int(df.get("nb_erb", "32")),
        nb_df=int(df.get("nb_df", "96")),
        norm_tau=float(df.get("norm_tau", "1")),
        min_nb_erb_freqs=int(df.get("min_nb_erb_freqs", "2")),
        df_order=int(df.get("df_order", "5")),
        df_lookahead=int(df.get("df_lookahead", "2")),
        conv_lookahead=int(net.get("conv_lookahead", "2")),
        mask_pf=net.getboolean("mask_pf", fallback=False),
        pf_beta=float(net.get("pf_beta", "0.02")),
    )
    return params, blobs


def _vorbis_window(fft_size: int) -> np.ndarray:
    half = fft_size // 2
    idx = np.arange(fft_size, dtype=np.float64)
    sin_term = np.sin(0.5 * np.pi * (idx + 0.5) / half)
    return np.sin(0.5 * np.pi * sin_term * sin_term).astype(np.float32)


def _erb_filterbank(sr: int, fft_size: int, nb_bands: int, min_nb_freqs: int) -> list[int]:
    def freq2erb(freq_hz: float) -> float:
        return 9.265 * math.log1p(freq_hz / (24.7 * 9.265))

    def erb2freq(erb: float) -> float:
        return 24.7 * 9.265 * (math.exp(erb / 9.265) - 1.0)

    nyquist = sr // 2
    freq_width = sr / fft_size
    erb_low = freq2erb(0.0)
    erb_high = freq2erb(float(nyquist))
    step = (erb_high - erb_low) / nb_bands
    widths = [0] * nb_bands
    prev_freq = 0
    freq_over = 0
    for index in range(1, nb_bands + 1):
        freq = erb2freq(erb_low + index * step)
        fb = int(round(freq / freq_width))
        nb_freqs = fb - prev_freq - freq_over
        if nb_freqs < min_nb_freqs:
            freq_over = min_nb_freqs - nb_freqs
            nb_freqs = min_nb_freqs
        else:
            freq_over = 0
        widths[index - 1] = nb_freqs
        prev_freq = fb
    widths[nb_bands - 1] += 1
    too_large = sum(widths) - (fft_size // 2 + 1)
    if too_large > 0:
        widths[nb_bands - 1] -= too_large
    return widths


def _calc_norm_alpha(sr: int, hop_size: int, tau: float) -> float:
    dt = hop_size / sr
    alpha = math.exp(-dt / tau)
    rounded = 1.0
    precision = 3
    while rounded >= 1.0:
        scale = 10**precision
        rounded = round(alpha * scale) / scale
        precision += 1
    return rounded


class DeepFilterNet3Denoiser:
    """CPU ONNX Runtime runner for DeepFilterNet3."""

    def __init__(self, bundle_path: str | Path) -> None:
        self.bundle_path = Path(bundle_path)
        try:
            import onnxruntime as ort
        except ImportError as error:
            raise RuntimeError(
                "Audio cleanup is not available in this build of LocalSR (ONNX Runtime is missing)."
            ) from error
        self.params, blobs = _read_bundle_members(self.bundle_path)
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self._enc = ort.InferenceSession(
            blobs["enc.onnx"], sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._erb_dec = ort.InferenceSession(
            blobs["erb_dec.onnx"], sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._df_dec = ort.InferenceSession(
            blobs["df_dec.onnx"], sess_options=opts, providers=["CPUExecutionProvider"]
        )
        p = self.params
        self._window = _vorbis_window(p.fft_size)
        self._wnorm = np.float32(1.0 / (p.fft_size**2 / (2.0 * p.hop_size)))
        self._alpha = np.float32(_calc_norm_alpha(p.sr, p.hop_size, p.norm_tau))
        widths = _erb_filterbank(p.sr, p.fft_size, p.nb_erb, p.min_nb_erb_freqs)
        n_freqs = p.fft_size // 2 + 1
        erb_mat = np.zeros((p.nb_erb, n_freqs), dtype=np.float32)
        offset = 0
        for band, width in enumerate(widths):
            erb_mat[band, offset : offset + width] = 1.0 / width
            offset += width
        self._erb_mat = erb_mat
        self._erb_inv = (erb_mat > 0).astype(np.float32)
        self._mean_init = np.linspace(-60.0, -90.0, p.nb_erb, dtype=np.float32)
        self._unit_init = np.linspace(0.001, 0.0001, p.nb_df, dtype=np.float32)

    def _denoise_window(self, audio: np.ndarray) -> np.ndarray:
        """Denoise one contiguous 1D float32 array at 48 kHz, returning the same length."""
        n = len(audio)
        if n == 0:
            return audio.copy()
        p = self.params
        fft_size = p.fft_size
        hop = p.hop_size
        rem = (-n) % hop
        padded = np.pad(audio.astype(np.float32, copy=False), (0, fft_size + rem))
        n_frames = len(padded) // hop

        frames_in = np.concatenate([np.zeros(fft_size - hop, dtype=np.float32), padded])
        stft_frames = np.lib.stride_tricks.sliding_window_view(frames_in, fft_size)[::hop][
            :n_frames
        ]
        spec = (np.fft.rfft(stft_frames * self._window, axis=-1) * self._wnorm).astype(np.complex64)

        alpha = self._alpha
        one_minus_alpha = np.float32(1.0) - alpha
        power = spec.real**2 + spec.imag**2
        erb_db = 10.0 * np.log10(power @ self._erb_mat.T + 1e-10)
        mean_state = self._mean_init.copy()
        feat_erb = np.empty_like(erb_db)
        for frame_idx in range(n_frames):
            mean_state = erb_db[frame_idx] * one_minus_alpha + mean_state * alpha
            feat_erb[frame_idx] = (erb_db[frame_idx] - mean_state) / 40.0

        spec_df = spec[:, : p.nb_df]
        spec_abs = np.abs(spec_df)
        unit_state = self._unit_init.copy()
        feat_spec_c = np.empty_like(spec_df)
        for frame_idx in range(n_frames):
            unit_state = spec_abs[frame_idx] * one_minus_alpha + unit_state * alpha
            feat_spec_c[frame_idx] = spec_df[frame_idx] / np.sqrt(unit_state)

        feat_erb_in = np.zeros((1, 1, n_frames, p.nb_erb), dtype=np.float32)
        feat_spec_in = np.zeros((1, 2, n_frames, p.nb_df), dtype=np.float32)
        lookahead = p.conv_lookahead
        if n_frames > lookahead:
            keep = n_frames - lookahead
            feat_erb_in[0, 0, :keep] = feat_erb[lookahead:]
            feat_spec_in[0, 0, :keep] = feat_spec_c[lookahead:].real
            feat_spec_in[0, 1, :keep] = feat_spec_c[lookahead:].imag
        elif lookahead == 0:
            feat_erb_in[0, 0] = feat_erb
            feat_spec_in[0, 0] = feat_spec_c.real
            feat_spec_in[0, 1] = feat_spec_c.imag

        e0, e1, e2, e3, emb, c0, _lsnr = self._enc.run(
            None, {"feat_erb": feat_erb_in, "feat_spec": feat_spec_in}
        )
        (mask,) = self._erb_dec.run(None, {"emb": emb, "e3": e3, "e2": e2, "e1": e1, "e0": e0})
        coefs, _ = self._df_dec.run(None, {"emb": emb, "c0": c0})

        mask_full = mask[0, 0] @ self._erb_inv
        spec_m = spec * mask_full

        coefs_r = coefs[0].reshape(n_frames, p.nb_df, p.df_order, 2)
        coefs_c = coefs_r[..., 0] + 1j * coefs_r[..., 1]
        pad_left = p.df_order - 1 - p.df_lookahead
        pad_right = p.df_lookahead
        spec_padded = np.pad(spec_df, ((pad_left, pad_right), (0, 0)))
        spec_df_out = np.zeros((n_frames, p.nb_df), dtype=np.complex64)
        for lag in range(p.df_order):
            spec_df_out += spec_padded[lag : lag + n_frames] * coefs_c[:, :, lag]

        spec_e = spec_m.copy()
        spec_e[:, : p.nb_df] = spec_df_out

        if p.mask_pf:
            eps = 1e-12
            gain = np.clip(np.abs(spec_e) / (np.abs(spec) + eps), eps, 1.0)
            gain_sin = np.maximum(gain * np.sin(np.pi * gain / 2.0), eps)
            pf = (1.0 + p.pf_beta) / (1.0 + p.pf_beta * (gain / gain_sin) ** 2)
            spec_e = spec_e * pf

        time_frames = np.fft.irfft(spec_e, n=fft_size, axis=-1).astype(np.float32) * (
            fft_size * self._window
        )
        out = np.zeros(n_frames * hop + (fft_size - hop), dtype=np.float32)
        for frame_idx in range(n_frames):
            start = frame_idx * hop
            out[start : start + fft_size] += time_frames[frame_idx]
        delay = fft_size - hop
        return np.clip(out[delay : delay + n], -1.0, 1.0)

    def denoise_mono(
        self,
        samples: np.ndarray,
        *,
        cancel_event: threading.Event | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        """Denoise a 1D float32 mono buffer at 48 kHz in overlapping chunks."""
        total = len(samples)
        if total == 0:
            return samples.astype(np.float32, copy=True)

        step = int(_CHUNK_SECONDS * self.params.sr)
        left_ctx = int(_LEFT_OVERLAP_SECONDS * self.params.sr)
        right_ctx = int(_RIGHT_OVERLAP_SECONDS * self.params.sr)
        xfade = _CROSSFADE_SAMPLES
        if total <= step + right_ctx:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            out = self._denoise_window(samples)
            if progress_cb is not None:
                progress_cb(total, total)
            return out

        out = np.empty(total, dtype=np.float32)
        pos = 0
        fade_in = np.linspace(0.0, 1.0, xfade, dtype=np.float32)
        fade_out = 1.0 - fade_in
        prev_tail: np.ndarray | None = None

        while pos < total:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("video job cancelled")
            win_start = max(0, pos - left_ctx)
            win_end = min(total, pos + step + right_ctx)
            enhanced = self._denoise_window(samples[win_start:win_end])
            local_start = pos - win_start
            emit_len = min(step, total - pos)
            valid = enhanced[local_start : local_start + emit_len]

            if prev_tail is not None:
                overlap = min(len(prev_tail), len(valid))
                valid[:overlap] = (
                    prev_tail[:overlap] * fade_out[:overlap] + valid[:overlap] * fade_in[:overlap]
                )

            is_last = pos + emit_len >= total
            if not is_last and emit_len > xfade:
                keep_len = emit_len - xfade
                out[pos : pos + keep_len] = valid[:keep_len]
                prev_tail = valid[keep_len:].copy()
                pos += keep_len
            else:
                out[pos : pos + emit_len] = valid
                prev_tail = None
                pos += emit_len

            if progress_cb is not None:
                progress_cb(pos, total)

        return out

    def denoise_channels(
        self,
        channels: np.ndarray,
        *,
        cancel_event: threading.Event | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> np.ndarray:
        """Denoise ``(C, T)`` float32 audio at 48 kHz, processing each channel cleanly."""
        if channels.ndim != 2:
            raise ValueError("Expected (channels, samples) float32 array.")
        n_channels, n_samples = channels.shape
        out = np.empty_like(channels, dtype=np.float32)
        total_work = max(1, n_channels * n_samples)
        for ch_idx in range(n_channels):

            def _report(done: int, _total: int, offset: int = ch_idx * n_samples) -> None:
                if progress_cb is not None:
                    progress_cb(offset + done, total_work)

            out[ch_idx] = self.denoise_mono(
                channels[ch_idx], cancel_event=cancel_event, progress_cb=_report
            )
        return out


def _encode_pcm_stream(
    output_stream,
    output_container,
    resampler: av.AudioResampler,
    samples_48k: np.ndarray,
    layout_name: str,
    next_pts_48k: int,
    initial_pts_orig: int,
    written_orig_samples: int,
    orig_rate: int,
) -> tuple[int, int]:
    """Resample a 48 kHz ``(C, T)`` chunk back to ``orig_rate`` and mux as FLAC."""
    if samples_48k.shape[1] > 0:
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(samples_48k, dtype=np.float32),
            format="fltp",
            layout=layout_name,
        )
        frame.sample_rate = DF3_SAMPLE_RATE
        frame.time_base = Fraction(1, DF3_SAMPLE_RATE)
        frame.pts = next_pts_48k
        next_pts_48k += samples_48k.shape[1]
        for converted in resampler.resample(frame):
            converted.pts = initial_pts_orig + written_orig_samples
            converted.time_base = Fraction(1, orig_rate)
            written_orig_samples += converted.samples
            for packet in output_stream.encode(converted):
                output_container.mux(packet)
    return next_pts_48k, written_orig_samples


def _denoise_one_stream(
    src,
    stream,
    out,
    denoiser: AudioDenoiser,
    *,
    stream_idx: int,
    est_samples_per_stream: int,
    total_est_work: int,
    cancel_event: threading.Event | None,
    progress_cb: Callable[[int, int], None] | None,
) -> None:
    """Decode, denoise at 48 kHz in overlapping chunks, resample back, and encode one stream."""
    step = int(_CHUNK_SECONDS * DF3_SAMPLE_RATE)
    left_ctx = int(_LEFT_OVERLAP_SECONDS * DF3_SAMPLE_RATE)
    right_ctx = int(_RIGHT_OVERLAP_SECONDS * DF3_SAMPLE_RATE)
    xfade = _CROSSFADE_SAMPLES
    fade_in = np.linspace(0.0, 1.0, xfade, dtype=np.float32)
    fade_out = 1.0 - fade_in

    orig_rate = int(stream.rate or stream.codec_context.sample_rate or 48000)
    channels = int(stream.codec_context.channels or 1)
    layout_name = {1: "mono", 2: "stereo"}.get(channels, stream.codec_context.layout.name)
    out_stream = out.add_stream("flac", rate=orig_rate)
    out_stream.codec_context.layout = layout_name
    out_stream.metadata.update(stream.metadata)

    to_48k = av.AudioResampler(format="fltp", layout=layout_name, rate=DF3_SAMPLE_RATE)
    to_orig = av.AudioResampler(format="s16", layout=layout_name, rate=orig_rate)

    initial_pts_orig: int | None = None
    next_input_time: Fraction | None = None
    buf: np.ndarray | None = None
    ctx_len = 0
    prev_tail: np.ndarray | None = None
    next_pts_48k = 0
    written_orig = 0
    processed_stream_samples = 0

    def flush_chunk(final: bool) -> None:
        nonlocal buf, ctx_len, prev_tail, next_pts_48k, written_orig, processed_stream_samples
        if buf is None or buf.shape[1] == 0:
            return
        avail = buf.shape[1] - ctx_len
        if avail <= 0:
            return
        if final:
            window = buf
            emit_len = avail
        else:
            if avail < step + right_ctx:
                return
            window = buf[:, : ctx_len + step + right_ctx]
            emit_len = step

        def _chunk_prog(done: int, total: int) -> None:
            if progress_cb is not None:
                frac = done / max(1, total)
                cur = (
                    stream_idx * est_samples_per_stream
                    + processed_stream_samples
                    + int(frac * emit_len)
                )
                progress_cb(min(cur, total_est_work), max(total_est_work, cur + 1))

        enhanced = denoiser.denoise_channels(
            window, cancel_event=cancel_event, progress_cb=_chunk_prog
        )
        valid = enhanced[:, ctx_len : ctx_len + emit_len].copy()

        if prev_tail is not None:
            overlap = min(prev_tail.shape[1], valid.shape[1])
            valid[:, :overlap] = (
                prev_tail[:, :overlap] * fade_out[:overlap] + valid[:, :overlap] * fade_in[:overlap]
            )

        if not final and emit_len > xfade:
            keep_len = emit_len - xfade
            to_write = valid[:, :keep_len]
            prev_tail = valid[:, keep_len:].copy()
            new_start = ctx_len + keep_len
            retain_from = max(0, new_start - left_ctx)
            ctx_len = new_start - retain_from
            buf = buf[:, retain_from:].copy()
        else:
            to_write = valid
            keep_len = emit_len
            prev_tail = None
            buf = None
            ctx_len = 0

        next_pts_48k, written_orig = _encode_pcm_stream(
            out_stream,
            out,
            to_orig,
            to_write,
            layout_name,
            next_pts_48k,
            initial_pts_orig or 0,
            written_orig,
            orig_rate,
        )
        processed_stream_samples += keep_len
        if progress_cb is not None:
            cur = stream_idx * est_samples_per_stream + processed_stream_samples
            progress_cb(min(cur, total_est_work), max(total_est_work, cur))

    def push_resampled(converted_frames) -> None:
        nonlocal buf
        parts = [
            frame.to_ndarray().astype(np.float32, copy=False)
            for frame in converted_frames
            if frame.samples > 0
        ]
        if not parts:
            return
        chunk = np.concatenate(parts, axis=1)
        buf = chunk if buf is None else np.concatenate([buf, chunk], axis=1)
        while buf is not None and buf.shape[1] - ctx_len >= step + right_ctx:
            flush_chunk(final=False)

    for frame in src.decode(stream):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("video job cancelled")
        if frame.pts is None and next_input_time is not None:
            frame.time_base = Fraction(1, frame.sample_rate)
            frame.pts = round(next_input_time * frame.sample_rate)
        if frame.pts is not None and frame.time_base is not None:
            if initial_pts_orig is None:
                initial_pts_orig = round(frame.pts * frame.time_base * orig_rate)
            next_input_time = frame.pts * frame.time_base + Fraction(
                frame.samples, frame.sample_rate
            )
        push_resampled(to_48k.resample(frame))

    push_resampled(to_48k.resample(None))
    flush_chunk(final=True)

    for converted in to_orig.resample(None):
        converted.pts = (initial_pts_orig or 0) + written_orig
        converted.time_base = Fraction(1, orig_rate)
        written_orig += converted.samples
        for packet in out_stream.encode(converted):
            out.mux(packet)
    for packet in out_stream.encode(None):
        out.mux(packet)


def denoise_video_audio(
    source_path: str | Path,
    output_path: str | Path,
    denoiser: AudioDenoiser,
    *,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> bool:
    """Decode, denoise at 48 kHz in overlapping chunks, and write lossless FLAC MKV.

    Returns ``True`` if at least one decodable audio track was processed, or
    ``False`` when the source contains no audio stream.
    """
    source = Path(source_path)
    destination = Path(output_path)

    with av.open(str(source)) as src:
        audio_streams = [
            stream
            for stream in src.streams
            if stream.type == "audio" and stream.codec_context is not None
        ]
        if not audio_streams:
            return False

        duration_s = float(src.duration) / 1_000_000.0 if src.duration else 0.0
        est_samples_per_stream = max(1, int(round(duration_s * DF3_SAMPLE_RATE)))
        total_est_work = max(1, len(audio_streams) * est_samples_per_stream)

        temp_out = owned_temporary(destination.parent, "audio-denoise")
        completed = False
        try:
            with av.open(str(temp_out), mode="w", format="matroska") as out:
                for stream_idx, stream in enumerate(audio_streams):
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("video job cancelled")
                    if stream_idx > 0:
                        src.seek(0)
                    _denoise_one_stream(
                        src,
                        stream,
                        out,
                        denoiser,
                        stream_idx=stream_idx,
                        est_samples_per_stream=est_samples_per_stream,
                        total_est_work=total_est_work,
                        cancel_event=cancel_event,
                        progress_cb=progress_cb,
                    )

            temp_out.replace(destination)
            completed = True
            return True
        finally:
            if not completed:
                try:
                    temp_out.unlink()
                except FileNotFoundError:
                    pass


@contextmanager
def cleaned_audio_source(
    source_path: str,
    *,
    enabled: bool,
    bundle_dir: str | None = None,
    denoiser: AudioDenoiser | None = None,
    temporary_directory: str | None = None,
    output_video_path: str | None = None,
    cancel_event: threading.Event | None = None,
    stage_cb: Callable[[str], None] | None = None,
    progress_cb: Callable[[int, int, float, float | None], None] | None = None,
    started_at: float | None = None,
) -> Iterator[tuple[str, bool]]:
    """Denoise ``source_path``'s audio into a temporary FLAC MKV when ``enabled``."""
    if not enabled:
        yield source_path, False
        return
    if denoiser is None:
        if not bundle_dir:
            raise FileNotFoundError(
                "DeepFilterNet3 is not downloaded. Download it, or turn Clean up audio off."
            )
        candidate = Path(bundle_dir)
        archive = candidate / "DeepFilterNet3_onnx.tar.gz"
        denoiser = DeepFilterNet3Denoiser(archive if archive.is_file() else candidate)

    if stage_cb is not None:
        stage_cb("audio_denoise")

    base_dir = temporary_directory or (
        str(Path(output_video_path).parent) if output_video_path else None
    )
    scratch = tempfile.mkdtemp(prefix="localsr-audio-", dir=base_dir)
    denoised_path = Path(scratch) / "audio_denoised.mkv"
    origin = started_at if started_at is not None else time.perf_counter()
    remaining_time: RemainingTime | None = None

    def report_progress(done: int, total: int) -> None:
        nonlocal remaining_time
        now = time.perf_counter()
        if remaining_time is None and total > 0:
            remaining_time = RemainingTime(float(total), min_intervals=1, min_seconds=0.2)
        if remaining_time is not None:
            if total > remaining_time.total:
                remaining_time.total = float(total)
            remaining_time.update(now, float(done))
        if progress_cb is not None:
            progress_cb(
                done,
                total,
                now - origin,
                remaining_time.remaining() if remaining_time else None,
            )

    try:
        wrote = denoise_video_audio(
            source_path,
            denoised_path,
            denoiser,
            cancel_event=cancel_event,
            progress_cb=report_progress,
        )
        denoiser = None
        if wrote:
            yield str(denoised_path), True
        else:
            yield source_path, False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
