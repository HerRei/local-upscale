# Spandrel detection and hyperparameter inference for PULSE checkpoints.

from spandrel import Architecture, ArchSupport, ImageModelDescriptor, StateDict
from spandrel.util import (
    KeyCondition,
    get_scale_and_output_channels,
    get_seq_len,
)

from .arch import PULSE

PULSE_DETECTION_KEYS = (
    "stem.weight",
    "body.0.conv1.weight",
    "body.0.conv2.weight",
    "head.0.weight",
    "head.0.bias",
)


class PULSEArch(Architecture[PULSE]):
    def __init__(self) -> None:
        super().__init__(
            id="PULSE",
            name="PULSE",
            detect=KeyCondition.has_all(*PULSE_DETECTION_KEYS),
        )

    def load(self, state_dict: StateDict) -> ImageModelDescriptor[PULSE]:
        state = state_dict

        in_ch = state["stem.weight"].shape[1]
        if in_ch != 3:
            raise ValueError(f"PULSE only supports 3 input channels, got {in_ch}")
        dim = state["stem.weight"].shape[0]
        n_blocks = get_seq_len(state, "body")
        scale, out_ch = get_scale_and_output_channels(state["head.0.weight"].shape[0], in_ch)

        model = PULSE(dim=dim, scale=scale, n_blocks=n_blocks)

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


def pulse_support() -> ArchSupport:
    """PULSE for a Spandrel registry; it is checked after Spandrel's own architectures."""
    return ArchSupport.from_architecture(PULSEArch())
