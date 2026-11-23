# Spandrel detection and hyperparameter inference for HEART checkpoints.
#
# Adapted from libs/spandrel/spandrel/architectures/HEART/__init__.py in
# chaiNNer-org/spandrel#334 at commit 2778b9f8231fd3fcc766aff83d37c50c6f45e146
# (MIT, Copyright (c) 2024 The ChaiNNer Organization). Changes by LocalSR are
# listed in NOTICE.md next to this file.

import math

from spandrel import (
    Architecture,
    ArchSupport,
    ImageModelDescriptor,
    SizeRequirements,
    StateDict,
)
from spandrel.util import (
    KeyCondition,
    get_pixelshuffle_params,
    get_seq_len,
)

from .arch import HEART

HEART_DETECTION_KEYS = (
    "conv_first.weight",
    "layers.0.residual_group.blocks.0.attn.to_qkv.weight",
    "layers.0.residual_group.blocks.0.attn.hidden_b",
    "layers.0.residual_group.blocks.0.conv_block.cab.0.weight",
    "layers.0.residual_group.blocks.0.mlp.fc1.weight",
    "norm.weight",
    "conv_last.weight",
)


def _inv_int_div(a: int, c: int) -> int:
    """Return a number b such that a // b == c."""
    b = a / c
    if b.is_integer():
        return int(b)
    if c == a // math.ceil(b):
        return math.ceil(b)
    if c == a // math.floor(b):
        return math.floor(b)
    raise ValueError(f"Could not find b such that {a} // b == {c}")


def _depths(state_dict: StateDict) -> list[int]:
    num_layers = get_seq_len(state_dict, "layers")
    return [get_seq_len(state_dict, f"layers.{i}.residual_group.blocks") for i in range(num_layers)]


class HEARTArch(Architecture[HEART]):
    def __init__(self) -> None:
        super().__init__(
            id="HEART",
            name="HEART",
            detect=KeyCondition.has_all(*HEART_DETECTION_KEYS),
        )

    def load(self, state_dict: StateDict) -> ImageModelDescriptor[HEART]:
        state = state_dict

        in_chans = state["conv_first.weight"].shape[1]
        embed_dim = state["conv_first.weight"].shape[0]
        num_feat = state["conv_last.weight"].shape[1]
        out_chans = state["conv_last.weight"].shape[0]
        scale, _ = get_pixelshuffle_params(state, "upsample", num_feat)

        depths = _depths(state)
        # Not deducible from the state dict; HEART's default is 6 heads per layer.
        num_heads = [6] * len(depths)
        # Not deducible from the state dict.
        window_size = 32
        rank = 8
        rib_n_freqs = 10
        attention_freq = 2
        conv_scale = 0.01
        drop_rate = 0.0
        drop_path_rate = 0.1
        use_checkpoint = False

        rib_hidden_dim = state["layers.0.residual_group.blocks.0.attn.hidden_b"].shape[1]

        compress_ratio = _inv_int_div(
            embed_dim,
            state["layers.0.residual_group.blocks.0.conv_block.cab.0.weight"].shape[0],
        )
        squeeze_factor = _inv_int_div(
            embed_dim,
            state["layers.0.residual_group.blocks.0.conv_block.cab.3.attention.1.weight"].shape[0],
        )

        mlp_hidden_dim = state["layers.0.residual_group.blocks.0.mlp.fc1.weight"].shape[0]
        mlp_ratio = mlp_hidden_dim / embed_dim

        patch_norm = "patch_embed.norm.weight" in state
        ape = "absolute_pos_embed" in state
        resi_connection = "1conv" if "conv_after_body.weight" in state else "identity"

        model = HEART(
            img_size=64,
            patch_size=1,
            in_chans=in_chans,
            embed_dim=embed_dim,
            depths=depths,
            num_heads=num_heads,
            window_size=window_size,
            compress_ratio=compress_ratio,
            squeeze_factor=squeeze_factor,
            conv_scale=conv_scale,
            mlp_ratio=mlp_ratio,
            drop_rate=drop_rate,
            drop_path_rate=drop_path_rate,
            ape=ape,
            patch_norm=patch_norm,
            use_checkpoint=use_checkpoint,
            upsampler="pixelshuffle",
            resi_connection=resi_connection,
            num_feat=num_feat,
            rank=rank,
            rib_hidden_dim=rib_hidden_dim,
            rib_n_freqs=rib_n_freqs,
            attention_freq=attention_freq,
            upscale=scale,
        )

        return ImageModelDescriptor(
            model,
            state,
            architecture=self,
            purpose="Restoration" if scale == 1 else "SR",
            tags=[f"{embed_dim}dim", *[f"{d}d" for d in depths]],
            supports_half=True,
            supports_bfloat16=True,
            scale=scale,
            input_channels=in_chans,
            output_channels=out_chans,
            size_requirements=SizeRequirements(multiple_of=32),
        )


def heart_support() -> ArchSupport:
    """HEART for a Spandrel registry; it is checked after Spandrel's own architectures."""
    return ArchSupport.from_architecture(HEARTArch())
