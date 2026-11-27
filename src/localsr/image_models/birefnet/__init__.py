"""Local, inference-only BiRefNet checkpoint support."""

import math
from collections.abc import Mapping
from types import SimpleNamespace

from .model import BiRefNet


def is_birefnet_state_dict(state):
    return isinstance(state, Mapping) and {
        "bb.patch_embed.proj.weight",
        "bb.layers.0.blocks.0.attn.relative_position_bias_table",
        "decoder.ipt_blk5.conv1.weight",
        "decoder.conv_out1.0.weight",
        "squeeze_module.0.dec_att.aspp1.atrous_conv.offset_conv.weight",
    } <= set(state)


class BiRefNetDescriptor:
    architecture = SimpleNamespace(name="BiRefNet")
    scale = 1
    input_channels = 3
    output_channels = 1
    supports_half = False
    size_requirements = SimpleNamespace(minimum=0, multiple_of=1, square=False)

    def __init__(self, model):
        self.model = model

    def to(self, *args, **kwargs):
        self.model.to(*args, **kwargs)
        return self

    def eval(self):
        self.model.eval()
        return self

    def __call__(self, image):
        return self.model(image)


def load_birefnet_descriptor(state):
    if not is_birefnet_state_dict(state):
        raise ValueError("State dict does not match BiRefNet")
    embed_dim = state["bb.patch_embed.proj.weight"].shape[0]
    heads = tuple(
        state[f"bb.layers.{i}.blocks.0.attn.relative_position_bias_table"].shape[1]
        for i in range(4)
    )
    depths = tuple(
        1 + max(int(k.split(".")[4]) for k in state if k.startswith(f"bb.layers.{i}.blocks."))
        for i in range(4)
    )
    table_size = state["bb.layers.0.blocks.0.attn.relative_position_bias_table"].shape[0]
    window = (math.isqrt(table_size) + 1) // 2
    model = BiRefNet(embed_dim=embed_dim, depths=depths, heads=heads, window_size=window)
    ignored = ("decoder.conv_ms_spvn_", "decoder.gdt_convs_pred_")
    inference_state = {k: v for k, v in state.items() if not k.startswith(ignored)}
    model.load_state_dict(inference_state, strict=True)
    return BiRefNetDescriptor(model)
