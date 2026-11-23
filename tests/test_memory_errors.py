"""A job that ran out of graphics memory says so in plain words, with what to try."""

from __future__ import annotations

import threading

import pytest
import torch

from localsr.core.disk_space import failure_fields
from localsr.core.inference import InferenceEngine
from localsr.core.memory_errors import (
    DeviceOutOfMemoryError,
    is_device_out_of_memory,
    memory_failure_message,
)
from localsr.core.pipeline import PipelineStage, PipelineStageError
from localsr.core.video_io import VideoStageError

# The user's real failure, as PyTorch 2.15 words it on a 16 GB M1 Pro.
MPS_TEXT = (
    "MPS backend out of memory (MPS allocated: 5.57 GiB, other allocations: 552.25 MiB, "
    "max allowed: 7.34 GiB). Tried to allocate 1.44 GiB on shared pool. Use "
    "PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0 to disable upper limit for memory allocations "
    "(may cause system failure)."
)
CUDA_TEXT = "CUDA out of memory. Tried to allocate 2.00 GiB (GPU 0; 8.00 GiB total capacity)"
VIDEO_AFTER = {"interpolation": "fps120", "interpolation_order": "after_upscale"}


def assert_plain(message: str) -> None:
    assert "PYTORCH" not in message and "MPS backend" not in message and "GiB" not in message
    assert "—" not in message and "–" not in message


def test_the_users_encode_stage_failure_becomes_a_plain_sentence_with_what_to_try():
    error = VideoStageError("encode", MPS_TEXT)
    message = failure_fields(error, "/out/video.mkv", video_request=VIDEO_AFTER)["error_message"]
    assert message == (
        "LocalSR ran out of graphics memory. Turn on Safe memory mode, choose a smaller output "
        "scale or fewer fixes, or set Interpolate to Before upscaling."
    )
    assert_plain(message)


@pytest.mark.parametrize(
    "request_data",
    [{"interpolation": "off"}, {"interpolation": "x2", "interpolation_order": "before_upscale"}],
)
def test_video_advice_names_interpolation_only_when_it_runs_after_upscaling(request_data):
    message = failure_fields(RuntimeError(CUDA_TEXT), None, video_request=request_data)
    assert message["error_message"] == (
        "LocalSR ran out of graphics memory. Turn on Safe memory mode, or choose a smaller "
        "output scale or fewer fixes."
    )


def test_a_photo_that_ran_out_after_the_smallest_tile_names_the_photo_settings():
    gave_up = DeviceOutOfMemoryError(
        "Out of memory. Reduced tile size to minimum and still failed.", device_type="mps"
    )
    stage = PipelineStage(kind="upscale", model_id="m", model_path="/m.pth", role="primary")
    wrapped = PipelineStageError(stage, str(gave_up))
    wrapped.__cause__ = gave_up
    message = failure_fields(wrapped, None)["error_message"]
    assert message == (
        "LocalSR ran out of graphics memory. Turn on Safe memory mode, or choose a smaller "
        "tile size or output scale."
    )


def test_the_cpu_running_out_is_not_called_graphics_memory():
    gave_up = DeviceOutOfMemoryError("Out of memory.", device_type="cpu", step="upscaling")
    assert memory_failure_message(gave_up).startswith("LocalSR ran out of memory during upscaling.")


def test_a_message_that_already_explains_itself_is_kept():
    # SeedVR2 measures and words its own report; its cause is PyTorch's text.
    explained = RuntimeError("SeedVR2 ran out of memory during decode: 3840 × 2160 ...")
    explained.__cause__ = RuntimeError(MPS_TEXT)
    assert failure_fields(explained, None, video_request={})["error_message"] == str(explained)


def test_other_failures_keep_their_text():
    assert failure_fields(ValueError("bad"), None, video_request={}) == {"error_message": "bad"}
    assert not is_device_out_of_memory(RuntimeError("DefaultCPUAllocator: not enough memory"))
    assert is_device_out_of_memory(RuntimeError(MPS_TEXT))
    assert is_device_out_of_memory(RuntimeError(CUDA_TEXT))


class _Adapter:
    def __init__(self, model):
        from localsr.core.model_adapter import NormalizedModelInfo

        self.model = model
        self.model_info = NormalizedModelInfo(
            architecture="Dummy",
            scale=1,
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
        return self.model, None

    def release(self):
        pass


def test_a_frame_that_never_fits_ends_in_the_typed_error_after_smaller_tiles():
    sizes: list[int] = []

    class AlwaysFull(torch.nn.Module):
        def forward(self, x):
            sizes.append(int(x.shape[-1]))
            raise RuntimeError(MPS_TEXT)

    adapter = _Adapter(AlwaysFull())
    engine = InferenceEngine(adapter)
    engine.load_model("dummy", "cpu", "fp32", adapter.model_info)
    with pytest.raises(DeviceOutOfMemoryError) as caught:
        engine.process_frame(
            torch.rand(3, 64, 64),
            adapter.model_info,
            64,
            0,
            threading.Event(),
            lambda *_: None,
            safe_memory=False,
        )
    assert caught.value.device_type == "cpu"
    assert sizes[0] == 64 and sizes[-1] == 4  # every smaller tile was tried first
    assert memory_failure_message(caught.value) is not None
