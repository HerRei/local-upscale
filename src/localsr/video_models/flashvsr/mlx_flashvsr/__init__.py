# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hermes Reisner. Part of the MLX port of FlashVSR (https://github.com/OpenImagingLab/FlashVSR).

from .attention import (
    AttentionModule,
    CrossAttention,
    RMSNorm,
    SelfAttention,
    block_sparse_attention,
    flash_attention,
)
from .block_sparse_attn import (
    block_sparse_attention_metal,
    compact_block_mask,
)
from .colorfix import (
    ColorCorrectorWavelet,
    TorchColorCorrectorWavelet,
    adain,
)
from .convert import (
    convert_state_dict,
    load_checkpoint_state_dict,
    load_torch_state_dict,
    map_pytorch_key,
    map_pytorch_shape,
    resolve_dtype,
    torch_to_mlx,
)
from .dit import (
    MLP,
    WAN_1_3B_CONFIG,
    DiTBlock,
    GateModule,
    Head,
    modulate,
)
from .lq_proj import (
    CACHE_T,
    Causal_LQ4x_Proj,
    CausalConv3d,
    PixelShuffle3d,
    RMS_norm,
)
from .masks import (
    WindowPartition3D,
    build_local_block_mask_shifted_vec,
    build_local_block_mask_shifted_vec_normal_slide,
    generate_causal_block_mask,
    generate_draft_block_mask,
)
from .model import WanModel
from .pipeline import (
    FlashVSRTinyPipeline,
    FlowMatchScheduler,
    model_fn_wan_video,
)
from .rope import (
    build_freqs_3d,
    precompute_freqs_cis,
    precompute_freqs_cis_3d,
    rope_apply,
    sinusoidal_embedding_1d,
)
from .tcdecoder import (
    TAEHV,
    Clamp,
    IdentityConv2d,
    MemBlock,
    TGrow,
    TPool,
    apply_model_with_memblocks,
    build_tcdecoder,
)

__all__ = [
    "AttentionModule",
    "CACHE_T",
    "CausalConv3d",
    "Causal_LQ4x_Proj",
    "Clamp",
    "ColorCorrectorWavelet",
    "CrossAttention",
    "DiTBlock",
    "FlashVSRTinyPipeline",
    "FlowMatchScheduler",
    "GateModule",
    "Head",
    "IdentityConv2d",
    "MLP",
    "MemBlock",
    "PixelShuffle3d",
    "RMSNorm",
    "RMS_norm",
    "SelfAttention",
    "TAEHV",
    "TGrow",
    "TPool",
    "TorchColorCorrectorWavelet",
    "WAN_1_3B_CONFIG",
    "WanModel",
    "WindowPartition3D",
    "adain",
    "apply_model_with_memblocks",
    "block_sparse_attention",
    "block_sparse_attention_metal",
    "build_freqs_3d",
    "build_local_block_mask_shifted_vec",
    "build_local_block_mask_shifted_vec_normal_slide",
    "build_tcdecoder",
    "compact_block_mask",
    "convert_state_dict",
    "flash_attention",
    "generate_causal_block_mask",
    "generate_draft_block_mask",
    "load_checkpoint_state_dict",
    "load_torch_state_dict",
    "map_pytorch_key",
    "map_pytorch_shape",
    "model_fn_wan_video",
    "modulate",
    "precompute_freqs_cis",
    "precompute_freqs_cis_3d",
    "resolve_dtype",
    "rope_apply",
    "sinusoidal_embedding_1d",
    "torch_to_mlx",
]
