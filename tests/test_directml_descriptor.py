"""Exercise the real Spandrel descriptor contract without needing a GPU in CI."""

from types import SimpleNamespace

import spandrel
import torch

from localsr.core.model_adapter import _DirectMLDescriptor


def test_directml_keeps_padding_auxiliary_output_and_clamping_without_inference_tensors():
    modes = []

    class AuxiliaryModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.bias = torch.nn.Parameter(torch.tensor(2.0))

        def forward(self, image):
            modes.append((torch.is_inference_mode_enabled(), torch.is_grad_enabled(), image.shape))
            return image + self.bias, "auxiliary-output"

    model = AuxiliaryModel()
    descriptor = spandrel.ImageModelDescriptor(
        model,
        state_dict=model.state_dict(),
        architecture=SimpleNamespace(name="test"),
        purpose="Restoration",
        tags=[],
        supports_half=False,
        supports_bfloat16=False,
        scale=1,
        input_channels=3,
        output_channels=3,
        size_requirements=spandrel.SizeRequirements(minimum=8, multiple_of=8),
        call_fn=lambda model, image: model(image)[0],
    )
    directml = _DirectMLDescriptor(descriptor)
    with torch.inference_mode():
        image = torch.zeros((1, 3, 5, 7))
        output = directml(image)
        assert torch.is_inference_mode_enabled()  # The caller's context is restored.
    assert modes == [(False, False, torch.Size([1, 3, 8, 8]))]
    assert output.shape == (1, 3, 5, 7)
    assert torch.all(output == 1)  # Descriptor clamping, not the raw tuple output.
    assert not output.requires_grad and not torch.is_inference(output)
    assert directml.model is descriptor.model
    descriptor(torch.zeros((1, 3, 8, 8)))
    assert modes[-1][:2] == (True, False)  # The original descriptor remains unchanged.
