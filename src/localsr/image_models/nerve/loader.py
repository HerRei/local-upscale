# Spandrel detection and hyperparameter inference for NERVE checkpoints.
#
# Adapted from libs/spandrel/spandrel/architectures/NERVE/__init__.py in
# chaiNNer-org/spandrel#333 at commit 3429b70e043a96838b5f10d2fe275e56f69e5c69
# (MIT, Copyright (c) 2024 The ChaiNNer Organization). Changes by LocalSR are
# listed in NOTICE.md next to this file.

from spandrel import Architecture, ArchSupport, ImageModelDescriptor, StateDict
from spandrel.util import (
    KeyCondition,
    get_scale_and_output_channels,
    get_seq_len,
)

from .arch import NERVE

NERVE_DETECTION_KEYS = (
    "stem.weight",
    "blocks.0.conv1.weight",
    "upsampler.0.weight",
)


class NERVEArch(Architecture[NERVE]):
    def __init__(self) -> None:
        super().__init__(
            id="NERVE",
            name="NERVE",
            detect=KeyCondition.has_all(*NERVE_DETECTION_KEYS),
        )

    def load(self, state_dict: StateDict) -> ImageModelDescriptor[NERVE]:
        state = state_dict

        in_ch = state["stem.weight"].shape[1]
        dim = state["stem.weight"].shape[0]
        n_blocks = get_seq_len(state, "blocks")
        scale, out_ch = get_scale_and_output_channels(state["upsampler.0.weight"].shape[0], in_ch)

        model = NERVE(
            in_ch=in_ch,
            out_ch=out_ch,
            dim=dim,
            n_blocks=n_blocks,
            upscale=scale,
        )

        return ImageModelDescriptor(
            model,
            state,
            architecture=self,
            purpose="Restoration" if scale == 1 else "SR",
            tags=[f"{dim}dim", f"{n_blocks}nb"],
            supports_half=True,
            supports_bfloat16=True,
            scale=scale,
            input_channels=in_ch,
            output_channels=out_ch,
        )


def nerve_support() -> ArchSupport:
    """NERVE for a Spandrel registry; it is checked after Spandrel's own architectures."""
    return ArchSupport.from_architecture(NERVEArch())
