# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

import gc
import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Union

import mlx.core as mx

from .colorfix import ColorCorrectorWavelet
from .convert import (
    load_checkpoint_state_dict,
    load_torch_state_dict,
    resolve_dtype,
    torch_to_mlx,
)
from .dit import WAN_1_3B_CONFIG
from .lq_proj import Causal_LQ4x_Proj
from .model import WanModel
from .rope import build_freqs_3d, sinusoidal_embedding_1d
from .tcdecoder import TAEHV, build_tcdecoder

# Modified for LocalSR: load posi_prompt.safetensors via mx.load instead of posi_prompt.pth.
_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "posi_prompt.safetensors"


class FlowMatchScheduler:
    """Minimal FlowMatch scheduler matching diffsynth.schedulers.flow_match."""

    def __init__(
        self,
        num_inference_steps: int = 100,
        num_train_timesteps: int = 1000,
        shift: float = 3.0,
        sigma_max: float = 1.0,
        sigma_min: float = 0.003 / 1.002,
        inverse_timesteps: bool = False,
        extra_one_step: bool = False,
        reverse_sigmas: bool = False,
    ):
        self.num_train_timesteps = num_train_timesteps
        self.shift = shift
        self.sigma_max = sigma_max
        self.sigma_min = sigma_min
        self.inverse_timesteps = inverse_timesteps
        self.extra_one_step = extra_one_step
        self.reverse_sigmas = reverse_sigmas
        self.set_timesteps(num_inference_steps)

    def set_timesteps(
        self,
        num_inference_steps: int = 100,
        denoising_strength: float = 1.0,
        shift: Optional[float] = None,
    ):
        if shift is not None:
            self.shift = shift
        sigma_start = self.sigma_min + (self.sigma_max - self.sigma_min) * denoising_strength
        if self.extra_one_step:
            self.sigmas = mx.linspace(sigma_start, self.sigma_min, num_inference_steps + 1)[:-1]
        else:
            self.sigmas = mx.linspace(sigma_start, self.sigma_min, num_inference_steps)
        if self.inverse_timesteps:
            self.sigmas = self.sigmas[::-1]
        self.sigmas = self.shift * self.sigmas / (1.0 + (self.shift - 1.0) * self.sigmas)
        if self.reverse_sigmas:
            self.sigmas = 1.0 - self.sigmas
        self.timesteps = self.sigmas * self.num_train_timesteps


def model_fn_wan_video(
    dit: WanModel,
    x: mx.array,
    timestep: mx.array,
    context: Optional[mx.array] = None,
    LQ_latents: Optional[List[mx.array]] = None,
    is_full_block: bool = False,
    is_stream: bool = False,
    pre_cache_k: Optional[List[Optional[mx.array]]] = None,
    pre_cache_v: Optional[List[Optional[mx.array]]] = None,
    topk_ratio: float = 2.0,
    kv_ratio: float = 3.0,
    cur_process_idx: int = 0,
    t_mod: Optional[mx.array] = None,
    t: Optional[mx.array] = None,
    local_range: int = 9,
):
    """Streaming forward step for WanModel matching diffsynth.pipelines.flashvsr_tiny."""
    x, (f, h, w) = dit.patchify(x)

    win = (2, 8, 8)
    seqlen = f // win[0]
    local_num = seqlen
    window_size = win[0] * h * w // 128
    square_num = window_size * window_size
    topk = int(square_num * topk_ratio) - 1
    kv_len = int(kv_ratio)

    f_start = 0 if cur_process_idx == 0 else 4 + cur_process_idx * 2
    freqs = build_freqs_3d(dit.freqs, f, h, w, f_start=f_start)

    if t is None or t_mod is None:
        t = dit.time_embedding(sinusoidal_embedding_1d(dit.freq_dim, timestep))
        t_mod = dit.time_projection(t).reshape(t.shape[0], 6, dit.dim)

    for block_id, block in enumerate(dit.blocks):
        if LQ_latents is not None and block_id < len(LQ_latents):
            x = x + LQ_latents[block_id]
        if is_stream:
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
                is_stream=True,
                pre_cache_k=pre_cache_k[block_id] if pre_cache_k is not None else None,
                pre_cache_v=pre_cache_v[block_id] if pre_cache_v is not None else None,
                local_range=local_range,
            )
            if pre_cache_k is not None:
                pre_cache_k[block_id] = last_pre_cache_k
            if pre_cache_v is not None:
                pre_cache_v[block_id] = last_pre_cache_v
        else:
            x = block(
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
                is_stream=False,
                local_range=local_range,
            )

    x = dit.head(x, t)
    x = dit.unpatchify(x, (f, h, w))
    return x, pre_cache_k, pre_cache_v


class FlashVSRTinyPipeline:
    """MLX FlashVSR v1.1 Tiny streaming pipeline (LQ video -> denoised video frames or latents)."""

    def __init__(
        self,
        dit: Optional[WanModel] = None,
        tcdecoder: Optional[TAEHV] = None,
        dtype: Union[str, mx.Dtype] = mx.bfloat16,
    ):
        self.dtype = resolve_dtype(dtype)
        self.scheduler = FlowMatchScheduler(shift=5, sigma_min=0.0, extra_one_step=True)
        self.dit = dit
        self.TCDecoder = tcdecoder
        self.ColorCorrector = ColorCorrectorWavelet(levels=5)
        self.height_division_factor = 16
        self.width_division_factor = 16
        self.prompt_emb_posi = None
        self.timestep = None
        self.t = None
        self.t_mod = None
        self.last_lq_cur_idx = 0

    @classmethod
    def from_pretrained(
        cls,
        model_dir: Union[str, os.PathLike],
        dtype: Union[str, mx.Dtype] = "bfloat16",
        init_cross_kv: bool = True,
        prompt_path: Optional[Union[str, os.PathLike]] = None,
    ) -> "FlashVSRTinyPipeline":
        """Load WanModel, Causal_LQ4x_Proj, and TCDecoder from a FlashVSR-v1.1 directory."""
        root = Path(model_dir)
        target = resolve_dtype(dtype)

        dit_path = root / "diffusion_pytorch_model_streaming_dmd.safetensors"
        lq_path = root / "LQ_proj_in.ckpt"
        tcd_path = root / "TCDecoder.ckpt"
        for p in (dit_path, lq_path, tcd_path):
            if not p.is_file():
                raise FileNotFoundError(f"Missing model file: {p}")

        dit = WanModel(**WAN_1_3B_CONFIG)
        load_torch_state_dict(dit, dit_path, dtype=target, strict=True)

        lq_proj = Causal_LQ4x_Proj(in_dim=3, out_dim=WAN_1_3B_CONFIG["dim"], layer_num=1)
        load_torch_state_dict(lq_proj, lq_path, dtype=target, strict=True)
        dit.LQ_proj_in = lq_proj

        tcdecoder = build_tcdecoder(
            new_channels=(512, 256, 128, 128),
            dtype=target,
            new_latent_channels=16 + 768,
        )
        tcd_sd = load_checkpoint_state_dict(tcd_path)
        tcd_sd = tcdecoder.patch_tgrow_layers(tcd_sd)
        load_torch_state_dict(tcdecoder, tcd_sd, dtype=target, strict=False)
        del tcd_sd
        gc.collect()
        mx.clear_cache()

        pipe = cls(dit=dit, tcdecoder=tcdecoder, dtype=target)
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

    def init_cross_kv(
        self,
        context_tensor: Optional[mx.array] = None,
        prompt_path: Optional[Union[str, os.PathLike]] = None,
    ):
        if self.dit is None:
            raise RuntimeError("self.dit must be initialized before calling init_cross_kv()")

        if context_tensor is None:
            path = Path(prompt_path) if prompt_path is not None else _DEFAULT_PROMPT_PATH
            if path.suffix != ".safetensors":
                raise ValueError(
                    f"Prompt tensor must be a .safetensors file to avoid pickle loading, got {path}"
                )
            loaded = mx.load(str(path))
            if isinstance(loaded, dict):
                raw_ctx = (
                    loaded["embedding"] if "embedding" in loaded else next(iter(loaded.values()))
                )
            else:
                raw_ctx = loaded
            ctx = raw_ctx if raw_ctx.dtype == self.dtype else raw_ctx.astype(self.dtype)
        else:
            ctx = torch_to_mlx(context_tensor, dtype=self.dtype)

        if self.prompt_emb_posi is None:
            self.prompt_emb_posi = {}
        self.prompt_emb_posi["context"] = ctx

        self.dit.reinit_cross_kv(ctx)
        self.timestep = mx.array([1000.0], dtype=self.dtype)
        self.t = self.dit.time_embedding(sinusoidal_embedding_1d(self.dit.freq_dim, self.timestep))
        self.t_mod = self.dit.time_projection(self.t).reshape(self.t.shape[0], 6, self.dit.dim)
        self.scheduler.set_timesteps(1, denoising_strength=1.0, shift=5.0)
        mx.eval(
            self.t,
            self.t_mod,
            *[blk.cross_attn.cache_k for blk in self.dit.blocks],
            *[blk.cross_attn.cache_v for blk in self.dit.blocks],
        )

    def generate_noise(
        self,
        shape: Tuple[int, ...],
        seed: Optional[int] = None,
        dtype: Optional[mx.Dtype] = None,
    ) -> mx.array:
        target = self.dtype if dtype is None else resolve_dtype(dtype)
        key = mx.random.key(seed) if seed is not None else None
        return mx.random.normal(shape, dtype=target, key=key)

    def __call__(
        self,
        height: int = 480,
        width: int = 832,
        num_frames: int = 81,
        cfg_scale: float = 1.0,
        seed: Optional[int] = None,
        LQ_video: Optional[mx.array] = None,
        is_full_block: bool = False,
        if_buffer: bool = False,
        topk_ratio: float = 2.0,
        kv_ratio: float = 3.0,
        local_range: int = 9,
        noise: Optional[mx.array] = None,
        color_fix: bool = True,
        color_fix_method: str = "adain",
        return_latents: bool = False,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> mx.array:
        assert cfg_scale == 1.0, "cfg_scale must be 1.0"
        if self.prompt_emb_posi is None or "context" not in self.prompt_emb_posi:
            raise RuntimeError("Cross-Attn KV is not initialized. Call init_cross_kv() first.")

        height, width = self.check_resize_height_width(height, width)
        if num_frames % 4 != 1:
            num_frames = (num_frames + 2) // 4 * 4 + 1

        if noise is None:
            latent_frames = (num_frames - 1) // 4 if if_buffer else (num_frames - 1) // 4 + 1
            latents = self.generate_noise(
                (1, 16, latent_frames, height // 8, width // 8),
                seed=seed,
                dtype=self.dtype,
            )
        else:
            latents = noise.astype(self.dtype) if noise.dtype != self.dtype else noise

        if LQ_video is not None and LQ_video.dtype != self.dtype:
            LQ_video = LQ_video.astype(self.dtype)

        process_total_num = (num_frames - 1) // 8 - 2
        if process_total_num <= 0:
            raise ValueError(
                f"num_frames={num_frames} yields process_total_num={process_total_num} <= 0; need >= 25."
            )

        lq_proj = getattr(self.dit, "LQ_proj_in", None)
        if lq_proj is not None:
            lq_proj.clear_cache()

        if self.TCDecoder is not None:
            self.TCDecoder.clean_mem()

        latents_total: List[mx.array] = []
        lq_cur_idx = 0
        pre_cache_k: List[Optional[mx.array]] = [None] * len(self.dit.blocks)
        pre_cache_v: List[Optional[mx.array]] = [None] * len(self.dit.blocks)

        for cur_process_idx in range(process_total_num):
            if cur_process_idx == 0:
                pre_cache_k = [None] * len(self.dit.blocks)
                pre_cache_v = [None] * len(self.dit.blocks)
                lq_latents = None
                inner_loop_num = 7
                for inner_idx in range(inner_loop_num):
                    if LQ_video is not None and lq_proj is not None:
                        clip = LQ_video[
                            :, :, max(0, inner_idx * 4 - 3) : (inner_idx + 1) * 4 - 3, :, :
                        ]
                        cur = lq_proj.stream_forward(clip)
                        mx.eval(
                            *(cur if cur is not None else ()),
                            *[c for c in lq_proj.cache.values() if c is not None],
                        )
                    else:
                        cur = None
                    if cur is None:
                        continue
                    if lq_latents is None:
                        lq_latents = cur
                    else:
                        for layer_idx in range(len(lq_latents)):
                            lq_latents[layer_idx] = mx.concatenate(
                                [lq_latents[layer_idx], cur[layer_idx]], axis=1
                            )
                lq_cur_idx = (inner_loop_num - 1) * 4 - 3
                cur_latents = latents[:, :, :6, :, :]
            else:
                lq_latents = None
                inner_loop_num = 2
                for inner_idx in range(inner_loop_num):
                    if LQ_video is not None and lq_proj is not None:
                        start_f = cur_process_idx * 8 + 17 + inner_idx * 4
                        end_f = cur_process_idx * 8 + 21 + inner_idx * 4
                        cur = lq_proj.stream_forward(LQ_video[:, :, start_f:end_f, :, :])
                        mx.eval(
                            *(cur if cur is not None else ()),
                            *[c for c in lq_proj.cache.values() if c is not None],
                        )
                    else:
                        cur = None
                    if cur is None:
                        continue
                    if lq_latents is None:
                        lq_latents = cur
                    else:
                        for layer_idx in range(len(lq_latents)):
                            lq_latents[layer_idx] = mx.concatenate(
                                [lq_latents[layer_idx], cur[layer_idx]], axis=1
                            )
                lq_cur_idx = cur_process_idx * 8 + 21 + (inner_loop_num - 2) * 4
                cur_latents = latents[:, :, 4 + cur_process_idx * 2 : 6 + cur_process_idx * 2, :, :]

            noise_pred_posi, pre_cache_k, pre_cache_v = model_fn_wan_video(
                self.dit,
                x=cur_latents,
                timestep=self.timestep,
                context=None,
                LQ_latents=lq_latents,
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
            mx.eval(
                cur_latents,
                *[c for c in pre_cache_k if c is not None],
                *[c for c in pre_cache_v if c is not None],
            )
            latents_total.append(cur_latents)
            if progress_callback is not None:
                progress_callback(cur_process_idx + 1, process_total_num)

        self.last_lq_cur_idx = lq_cur_idx
        latents = mx.concatenate(latents_total, axis=2)
        if self.TCDecoder is None or return_latents:
            return latents

        cond = LQ_video[:, :, :lq_cur_idx, :, :] if LQ_video is not None else None
        frames = self.TCDecoder.decode_video(
            latents.transpose(0, 2, 1, 3, 4),
            parallel=False,
            show_progress_bar=False,
            cond=cond,
        )
        frames = frames.transpose(0, 2, 1, 3, 4) * 2.0 - 1.0
        mx.eval(frames)

        if color_fix and LQ_video is not None:
            frames = self.ColorCorrector(
                frames,
                LQ_video[:, :, : frames.shape[2], :, :],
                clip_range=(-1.0, 1.0),
                chunk_size=16,
                method=color_fix_method,
            )
            mx.eval(frames)

        return frames[0]
