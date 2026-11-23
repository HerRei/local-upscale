# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2024-2026 ModelScope / DiffSynth-Studio, OpenImagingLab FlashVSR authors,
# and Hermes Reisner (KV-cache/LQ release before decoding).
# Modified for LocalSR: self-contained Tiny pipeline without diffsynth/ModelManager/WanVAE,
# loads posi_prompt.safetensors via safetensors, and reports progress via progress_callback.

from __future__ import annotations

import gc
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Optional, Tuple, Union

import torch
import torch.nn as nn
import torch.nn.functional as F
from safetensors import safe_open
from safetensors.torch import load_file as load_safetensors_file

from .flow_match import FlowMatchScheduler
from .lq_proj import Causal_LQ4x_Proj
from .tcdecoder import TAEHV, build_tcdecoder
from .wan_video_dit import WAN_1_3B_CONFIG, WanModel, sinusoidal_embedding_1d

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "posi_prompt.safetensors"


def _load_ckpt_state_dict(path: Path) -> dict[str, torch.Tensor]:
    loaded = torch.load(str(path), map_location="cpu", weights_only=True)
    if (
        isinstance(loaded, Mapping)
        and "state_dict" in loaded
        and isinstance(loaded["state_dict"], Mapping)
    ):
        return dict(loaded["state_dict"])
    if isinstance(loaded, Mapping):
        return dict(loaded)
    raise TypeError(f"Unsupported checkpoint payload type {type(loaded)!r} in {path}")


def _calc_mean_std(feat: torch.Tensor, eps: float = 1e-5) -> Tuple[torch.Tensor, torch.Tensor]:
    assert feat.dim() == 4, "feat must be (N, C, H, W)"
    N, C = feat.shape[:2]
    var = feat.view(N, C, -1).var(dim=2, unbiased=False) + eps
    std = var.sqrt().view(N, C, 1, 1)
    mean = feat.view(N, C, -1).mean(dim=2).view(N, C, 1, 1)
    return mean, std


def _adain(content_feat: torch.Tensor, style_feat: torch.Tensor) -> torch.Tensor:
    assert content_feat.shape[:2] == style_feat.shape[:2], "ADAIN: N and C must match"
    size = content_feat.size()
    style_mean, style_std = _calc_mean_std(style_feat)
    content_mean, content_std = _calc_mean_std(content_feat)
    normalized = (content_feat - content_mean.expand(size)) / content_std.expand(size)
    return normalized * style_std.expand(size) + style_mean.expand(size)


def _make_gaussian3x3_kernel(dtype: torch.dtype, device: torch.device) -> torch.Tensor:
    vals = [
        [0.0625, 0.125, 0.0625],
        [0.125, 0.25, 0.125],
        [0.0625, 0.125, 0.0625],
    ]
    return torch.tensor(vals, dtype=dtype, device=device)


def _wavelet_blur(x: torch.Tensor, radius: int) -> torch.Tensor:
    assert x.dim() == 4, "x must be (N, C, H, W)"
    C = x.shape[1]
    base = _make_gaussian3x3_kernel(x.dtype, x.device)
    weight = base.view(1, 1, 3, 3).repeat(C, 1, 1, 1)
    pad = radius
    x_pad = F.pad(x, (pad, pad, pad, pad), mode="replicate")
    return F.conv2d(x_pad, weight, bias=None, stride=1, padding=0, dilation=radius, groups=C)


def _wavelet_decompose(x: torch.Tensor, levels: int = 5) -> Tuple[torch.Tensor, torch.Tensor]:
    assert x.dim() == 4, "x must be (N, C, H, W)"
    high = torch.zeros_like(x)
    low = x
    for i in range(levels):
        radius = 2**i
        blurred = _wavelet_blur(low, radius)
        high = high + (low - blurred)
        low = blurred
    return high, low


def _wavelet_reconstruct(
    content: torch.Tensor, style: torch.Tensor, levels: int = 5
) -> torch.Tensor:
    c_high, _ = _wavelet_decompose(content, levels=levels)
    _, s_low = _wavelet_decompose(style, levels=levels)
    return c_high + s_low


class TorchColorCorrectorWavelet(nn.Module):
    def __init__(self, levels: int = 5):
        super().__init__()
        self.levels = levels

    @staticmethod
    def _flatten_time(x: torch.Tensor) -> Tuple[torch.Tensor, int, int]:
        assert x.dim() == 5, "input must be (B, C, f, H, W)"
        B, C, f, H, W = x.shape
        y = x.permute(0, 2, 1, 3, 4).reshape(B * f, C, H, W)
        return y, B, f

    @staticmethod
    def _unflatten_time(y: torch.Tensor, B: int, f: int) -> torch.Tensor:
        BF, C, H, W = y.shape
        assert BF == B * f
        return y.reshape(B, f, C, H, W).permute(0, 2, 1, 3, 4)

    def forward(
        self,
        hq_image: torch.Tensor,
        lq_image: torch.Tensor,
        clip_range: Tuple[float, float] = (-1.0, 1.0),
        method: Literal["wavelet", "adain"] = "wavelet",
        chunk_size: Optional[int] = None,
    ) -> torch.Tensor:
        assert hq_image.shape == lq_image.shape, "HQ and LQ shapes must match"
        assert hq_image.dim() == 5 and hq_image.shape[1] == 3, "input must be (B, 3, f, H, W)"

        _B, _C, f, _H, _W = hq_image.shape
        if chunk_size is None or chunk_size >= f:
            hq4, B, f = self._flatten_time(hq_image)
            lq4, _, _ = self._flatten_time(lq_image)
            if method == "wavelet":
                out4 = _wavelet_reconstruct(hq4, lq4, levels=self.levels)
            elif method == "adain":
                out4 = _adain(hq4, lq4)
            else:
                raise ValueError(f"Unknown color correction method: {method!r}")
            out4 = torch.clamp(out4, *clip_range)
            return self._unflatten_time(out4, B, f)

        outs = []
        for start in range(0, f, chunk_size):
            end = min(start + chunk_size, f)
            hq_chunk = hq_image[:, :, start:end]
            lq_chunk = lq_image[:, :, start:end]
            hq4, B_, f_ = self._flatten_time(hq_chunk)
            lq4, _, _ = self._flatten_time(lq_chunk)
            if method == "wavelet":
                out4 = _wavelet_reconstruct(hq4, lq4, levels=self.levels)
            elif method == "adain":
                out4 = _adain(hq4, lq4)
            else:
                raise ValueError(f"Unknown color correction method: {method!r}")
            out4 = torch.clamp(out4, *clip_range)
            outs.append(self._unflatten_time(out4, B_, f_))
        return torch.cat(outs, dim=2)


def model_fn_wan_video(
    dit: WanModel,
    x: torch.Tensor,
    timestep: torch.Tensor,
    context: Optional[torch.Tensor] = None,
    LQ_latents: Optional[list[torch.Tensor]] = None,
    is_full_block: bool = False,
    is_stream: bool = False,
    pre_cache_k: Optional[list[Optional[torch.Tensor]]] = None,
    pre_cache_v: Optional[list[Optional[torch.Tensor]]] = None,
    topk_ratio: float = 2.0,
    kv_ratio: float = 3.0,
    cur_process_idx: int = 0,
    t_mod: Optional[torch.Tensor] = None,
    t: Optional[torch.Tensor] = None,
    local_range: int = 9,
):
    x, (f, h, w) = dit.patchify(x)

    win = (2, 8, 8)
    seqlen = f // win[0]
    local_num = seqlen
    window_size = win[0] * h * w // 128
    square_num = window_size * window_size
    topk = int(square_num * topk_ratio) - 1
    kv_len = int(kv_ratio)

    if cur_process_idx == 0:
        freqs = (
            torch.cat(
                [
                    dit.freqs[0][:f].view(f, 1, 1, -1).expand(f, h, w, -1),
                    dit.freqs[1][:h].view(1, h, 1, -1).expand(f, h, w, -1),
                    dit.freqs[2][:w].view(1, 1, w, -1).expand(f, h, w, -1),
                ],
                dim=-1,
            )
            .reshape(f * h * w, 1, -1)
            .to(x.device)
        )
    else:
        f_start = 4 + cur_process_idx * 2
        freqs = (
            torch.cat(
                [
                    dit.freqs[0][f_start : f_start + f].view(f, 1, 1, -1).expand(f, h, w, -1),
                    dit.freqs[1][:h].view(1, h, 1, -1).expand(f, h, w, -1),
                    dit.freqs[2][:w].view(1, 1, w, -1).expand(f, h, w, -1),
                ],
                dim=-1,
            )
            .reshape(f * h * w, 1, -1)
            .to(x.device)
        )

    if t is None or t_mod is None:
        t = dit.time_embedding(sinusoidal_embedding_1d(dit.freq_dim, timestep))
        t_mod = dit.time_projection(t).unflatten(1, (6, dit.dim))

    for block_id, block in enumerate(dit.blocks):
        if LQ_latents is not None and block_id < len(LQ_latents):
            x = x + LQ_latents[block_id]
        x, last_pre_cache_k, last_pre_cache_v = block(
            x,
            context,
            t_mod,
            freqs,
            f,
            h,
            w,
            local_num,
            topk,
            block_id=block_id,
            kv_len=kv_len,
            is_full_block=is_full_block,
            is_stream=is_stream,
            pre_cache_k=pre_cache_k[block_id] if pre_cache_k is not None else None,
            pre_cache_v=pre_cache_v[block_id] if pre_cache_v is not None else None,
            local_range=local_range,
        )
        if pre_cache_k is not None:
            pre_cache_k[block_id] = last_pre_cache_k
        if pre_cache_v is not None:
            pre_cache_v[block_id] = last_pre_cache_v

    x = dit.head(x, t)
    x = dit.unpatchify(x, (f, h, w))
    return x, pre_cache_k, pre_cache_v


class FlashVSRTinyPipeline:
    """PyTorch FlashVSR v1.1 Tiny streaming pipeline."""

    def __init__(
        self,
        dit: Optional[WanModel] = None,
        tcdecoder: Optional[TAEHV] = None,
        device: Union[str, torch.device] = "cuda",
        torch_dtype: torch.dtype = torch.bfloat16,
    ):
        self.device = device
        self.torch_dtype = torch_dtype
        self.scheduler = FlowMatchScheduler(shift=5, sigma_min=0.0, extra_one_step=True)
        self.dit = dit
        self.TCDecoder = tcdecoder
        self.ColorCorrector = TorchColorCorrectorWavelet(levels=5)
        self.height_division_factor = 16
        self.width_division_factor = 16
        self.prompt_emb_posi: Optional[dict[str, torch.Tensor]] = None
        self.timestep: Optional[torch.Tensor] = None
        self.t: Optional[torch.Tensor] = None
        self.t_mod: Optional[torch.Tensor] = None

    @classmethod
    def from_pretrained(
        cls,
        model_dir: Union[str, os.PathLike],
        device: Union[str, torch.device] = "cuda",
        dtype: torch.dtype = torch.bfloat16,
        init_cross_kv: bool = True,
        prompt_path: Optional[Union[str, os.PathLike]] = None,
        rocm_cache_release: bool = True,
    ) -> "FlashVSRTinyPipeline":
        """Load WanModel, Causal_LQ4x_Proj, and TCDecoder from a FlashVSR-v1.1 directory."""
        root = Path(model_dir)
        dit_path = root / "diffusion_pytorch_model_streaming_dmd.safetensors"
        lq_path = root / "LQ_proj_in.ckpt"
        tcd_path = root / "TCDecoder.ckpt"
        for path in (dit_path, lq_path, tcd_path):
            if not path.is_file():
                raise FileNotFoundError(f"Missing model file: {path}")

        with torch.device("meta"):
            dit = WanModel(**WAN_1_3B_CONFIG)
        dit_sd: dict[str, torch.Tensor] = {}
        with safe_open(str(dit_path), framework="pt", device="cpu") as handle:
            for key in handle.keys():
                dit_sd[key] = handle.get_tensor(key).to(device=device, dtype=dtype)
        dit.load_state_dict(dit_sd, strict=True, assign=True)
        dit_sd.clear()
        del dit_sd

        lq_proj = Causal_LQ4x_Proj(in_dim=3, out_dim=WAN_1_3B_CONFIG["dim"], layer_num=1)
        lq_sd = _load_ckpt_state_dict(lq_path)
        lq_proj.load_state_dict(lq_sd, strict=True)
        del lq_sd
        lq_proj.to(device=device, dtype=dtype)
        dit.LQ_proj_in = lq_proj

        tcdecoder = build_tcdecoder(
            new_channels=(512, 256, 128, 128),
            device="cpu",
            dtype=dtype,
            new_latent_channels=16 + 768,
        )
        tcd_sd = _load_ckpt_state_dict(tcd_path)
        tcd_sd = tcdecoder.patch_tgrow_layers(tcd_sd)
        tcdecoder.load_state_dict(tcd_sd, strict=False)
        del tcd_sd
        tcdecoder.to(device=device, dtype=dtype)
        if rocm_cache_release and torch.version.hip:
            tcdecoder.set_rocm_cache_release(True)

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        pipe = cls(dit=dit, tcdecoder=tcdecoder, device=device, torch_dtype=dtype)
        if init_cross_kv:
            pipe.init_cross_kv(prompt_path=prompt_path)
        return pipe

    def check_resize_height_width(self, height: int, width: int) -> Tuple[int, int]:
        if height % self.height_division_factor != 0:
            height = (
                (height + self.height_division_factor - 1)
                // self.height_division_factor
                * self.height_division_factor
            )
        if width % self.width_division_factor != 0:
            width = (
                (width + self.width_division_factor - 1)
                // self.width_division_factor
                * self.width_division_factor
            )
        return height, width

    def generate_noise(
        self,
        shape: Tuple[int, ...],
        seed: Optional[int] = None,
        device: Union[str, torch.device] = "cpu",
        dtype: torch.dtype = torch.float16,
    ) -> torch.Tensor:
        generator = None if seed is None else torch.Generator(device).manual_seed(seed)
        return torch.randn(shape, generator=generator, device=device, dtype=dtype)

    def init_cross_kv(
        self,
        context_tensor: Optional[torch.Tensor] = None,
        prompt_path: Optional[Union[str, os.PathLike]] = None,
    ) -> None:
        if self.dit is None:
            raise RuntimeError("self.dit must be initialized before calling init_cross_kv()")

        if context_tensor is None:
            path = Path(prompt_path) if prompt_path is not None else _DEFAULT_PROMPT_PATH
            if path.suffix != ".safetensors":
                raise ValueError(
                    f"Prompt tensor must be a .safetensors file to avoid pickle loading, got {path}"
                )
            loaded = load_safetensors_file(str(path), device="cpu")
            raw_ctx = loaded["embedding"] if "embedding" in loaded else next(iter(loaded.values()))
        else:
            raw_ctx = context_tensor

        ctx = raw_ctx.to(dtype=self.torch_dtype, device=self.device)
        if self.prompt_emb_posi is None:
            self.prompt_emb_posi = {}
        self.prompt_emb_posi["context"] = ctx

        self.dit.reinit_cross_kv(ctx)
        self.timestep = torch.tensor([1000.0], device=self.device, dtype=self.torch_dtype)
        self.t = self.dit.time_embedding(sinusoidal_embedding_1d(self.dit.freq_dim, self.timestep))
        self.t_mod = self.dit.time_projection(self.t).unflatten(1, (6, self.dit.dim))
        self.scheduler.set_timesteps(1, denoising_strength=1.0, shift=5.0)

    @torch.no_grad()
    def __call__(
        self,
        height: int = 480,
        width: int = 832,
        num_frames: int = 81,
        cfg_scale: float = 1.0,
        seed: Optional[int] = None,
        LQ_video: Optional[torch.Tensor] = None,
        is_full_block: bool = False,
        if_buffer: bool = False,
        topk_ratio: float = 2.0,
        kv_ratio: float = 3.0,
        local_range: int = 9,
        noise: Optional[torch.Tensor] = None,
        color_fix: bool = True,
        color_fix_method: Literal["wavelet", "adain"] = "adain",
        return_latents: bool = False,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> torch.Tensor:
        assert cfg_scale == 1.0, "cfg_scale must be 1.0"
        if self.prompt_emb_posi is None or "context" not in self.prompt_emb_posi:
            raise RuntimeError("Cross-Attn KV is not initialized. Call init_cross_kv() first.")
        if self.dit is None:
            raise RuntimeError("self.dit must be initialized before calling the pipeline.")

        height, width = self.check_resize_height_width(height, width)
        if num_frames % 4 != 1:
            num_frames = (num_frames + 2) // 4 * 4 + 1

        if noise is None:
            latent_frames = (num_frames - 1) // 4 if if_buffer else (num_frames - 1) // 4 + 1
            latents = self.generate_noise(
                (1, 16, latent_frames, height // 8, width // 8),
                seed=seed,
                device=self.device,
                dtype=self.torch_dtype,
            )
        else:
            latents = noise.to(device=self.device, dtype=self.torch_dtype)

        if LQ_video is not None and (
            LQ_video.dtype != self.torch_dtype or LQ_video.device != torch.device(self.device)
        ):
            LQ_video = LQ_video.to(device=self.device, dtype=self.torch_dtype)

        process_total_num = (num_frames - 1) // 8 - 2
        if process_total_num <= 0:
            raise ValueError(
                f"num_frames={num_frames} yields process_total_num={process_total_num} <= 0; need >= 25."
            )

        lq_proj: Any = getattr(self.dit, "LQ_proj_in", None)
        if lq_proj is not None:
            lq_proj.clear_cache()

        if self.TCDecoder is not None:
            self.TCDecoder.clean_mem()

        latents_total: list[torch.Tensor] = []
        LQ_cur_idx = 0
        pre_cache_k: list[Optional[torch.Tensor]] = [None] * len(self.dit.blocks)
        pre_cache_v: list[Optional[torch.Tensor]] = [None] * len(self.dit.blocks)

        for cur_process_idx in range(process_total_num):
            if cur_process_idx == 0:
                pre_cache_k = [None] * len(self.dit.blocks)
                pre_cache_v = [None] * len(self.dit.blocks)
                LQ_latents = None
                inner_loop_num = 7
                for inner_idx in range(inner_loop_num):
                    cur = (
                        lq_proj.stream_forward(
                            LQ_video[
                                :, :, max(0, inner_idx * 4 - 3) : (inner_idx + 1) * 4 - 3, :, :
                            ]
                        )
                        if LQ_video is not None and lq_proj is not None
                        else None
                    )
                    if cur is None:
                        continue
                    if LQ_latents is None:
                        LQ_latents = cur
                    else:
                        for layer_idx in range(len(LQ_latents)):
                            LQ_latents[layer_idx] = torch.cat(
                                [LQ_latents[layer_idx], cur[layer_idx]], dim=1
                            )
                LQ_cur_idx = (inner_loop_num - 1) * 4 - 3
                cur_latents = latents[:, :, :6, :, :]
            else:
                LQ_latents = None
                inner_loop_num = 2
                for inner_idx in range(inner_loop_num):
                    start_f = cur_process_idx * 8 + 17 + inner_idx * 4
                    end_f = cur_process_idx * 8 + 21 + inner_idx * 4
                    cur = (
                        lq_proj.stream_forward(LQ_video[:, :, start_f:end_f, :, :])
                        if LQ_video is not None and lq_proj is not None
                        else None
                    )
                    if cur is None:
                        continue
                    if LQ_latents is None:
                        LQ_latents = cur
                    else:
                        for layer_idx in range(len(LQ_latents)):
                            LQ_latents[layer_idx] = torch.cat(
                                [LQ_latents[layer_idx], cur[layer_idx]], dim=1
                            )
                LQ_cur_idx = cur_process_idx * 8 + 21 + (inner_loop_num - 2) * 4
                cur_latents = latents[:, :, 4 + cur_process_idx * 2 : 6 + cur_process_idx * 2, :, :]

            noise_pred_posi, pre_cache_k, pre_cache_v = model_fn_wan_video(
                self.dit,
                x=cur_latents,
                timestep=self.timestep,
                context=None,
                LQ_latents=LQ_latents,
                is_full_block=is_full_block,
                is_stream=True,
                pre_cache_k=pre_cache_k,
                pre_cache_v=pre_cache_v,
                topk_ratio=topk_ratio,
                kv_ratio=kv_ratio,
                cur_process_idx=cur_process_idx,
                t_mod=self.t_mod,
                t=self.t,
                local_range=local_range,
            )

            cur_latents = cur_latents - noise_pred_posi
            latents_total.append(cur_latents)
            if progress_callback is not None:
                progress_callback(cur_process_idx + 1, process_total_num)

        latents = torch.cat(latents_total, dim=2)

        # Free streaming caches and LQ projections before decoding (required to fit 768x1408 on 16 GB).
        pre_cache_k = pre_cache_v = LQ_latents = latents_total = None  # type: ignore[assignment]
        if lq_proj is not None:
            lq_proj.clear_cache()
        if torch.device(self.device).type == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()

        if self.TCDecoder is None or return_latents:
            return latents

        cond = LQ_video[:, :, :LQ_cur_idx, :, :] if LQ_video is not None else None
        frames = (
            self.TCDecoder.decode_video(
                latents.transpose(1, 2),
                parallel=False,
                show_progress_bar=False,
                cond=cond,
            )
            .transpose(1, 2)
            .mul_(2)
            .sub_(1)
        )

        if color_fix and LQ_video is not None:
            frames = self.ColorCorrector(
                frames.to(device=LQ_video.device),
                LQ_video[:, :, : frames.shape[2], :, :],
                clip_range=(-1.0, 1.0),
                chunk_size=16,
                method=color_fix_method,
            )

        return frames[0]
