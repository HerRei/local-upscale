"""CPU unit, parity, routing, and tile-planner tests for PyTorch CUDA/ROCm FlashVSR."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
import torch.nn as nn
from einops import rearrange
from safetensors.torch import save_file as save_safetensors_file

from localsr.core import video_engines
from localsr.core.video_engines import TemporalEngineUnavailable, resolve_video_engine
from localsr.video_models.flashvsr import engine as flashvsr_engine
from localsr.video_models.flashvsr.engine import (
    BUDGET_FRACTION,
    MIN_TILE_SIDE,
    ROCM_FIXED_MEMORY_BYTES,
    ROCM_PER_PIXEL_MEMORY_BYTES,
    FlashVSREngine,
    plan_tiles,
)
from localsr.video_models.flashvsr.torch_flashvsr import rocm_attention
from localsr.video_models.flashvsr.torch_flashvsr.lq_proj import (
    Causal_LQ4x_Proj,
    CausalConv3d,
    RMS_norm,
)
from localsr.video_models.flashvsr.torch_flashvsr.pipeline import (
    FlashVSRTinyPipeline as VendoredTorchPipeline,
)
from localsr.video_models.flashvsr.torch_flashvsr.tcdecoder import build_tcdecoder
from localsr.video_models.flashvsr.torch_flashvsr.wan_video_dit import (
    WanModel,
    build_local_block_mask_shifted_vec_normal_slide,
    generate_draft_block_mask,
)
from localsr.worker.server import WorkerServer, _engine_features, _video_engines

_FORK_ROOT = Path("/Users/hermesheiniger/Projects/FlashVSR/flashvsr-mlx")
_HAS_FORK = _FORK_ROOT.is_dir()

try:
    from torch.nn.attention.flex_attention import BlockMask, flex_attention  # noqa: F401

    _HAS_FLEX_ATTENTION = True
except (ImportError, AttributeError):
    _HAS_FLEX_ATTENTION = False


def _shrink_lq_proj_for_test(
    lq_proj: nn.Module,
    conv_cls: type[nn.Module],
    norm_cls: type[nn.Module],
    *,
    out_dim: int,
    layer_num: int,
    hidden_dim1: int = 16,
    hidden_dim2: int = 24,
) -> None:
    """Replace Causal_LQ4x_Proj hidden layers with tiny ones for fast CPU tests."""
    in_channels = 3 * lq_proj.ff * lq_proj.hh * lq_proj.ww
    lq_proj.hidden_dim1 = hidden_dim1
    lq_proj.hidden_dim2 = hidden_dim2
    lq_proj.layer_num = layer_num
    lq_proj.conv1 = conv_cls(
        in_channels, hidden_dim1, (4, 3, 3), stride=(2, 1, 1), padding=(1, 1, 1)
    )
    lq_proj.norm1 = norm_cls(hidden_dim1, images=False)
    lq_proj.conv2 = conv_cls(
        hidden_dim1, hidden_dim2, (4, 3, 3), stride=(2, 1, 1), padding=(1, 1, 1)
    )
    lq_proj.norm2 = norm_cls(hidden_dim2, images=False)
    lq_proj.linear_layers = nn.ModuleList(
        [nn.Linear(hidden_dim2, out_dim) for _ in range(layer_num)]
    )


def _load_fork_modules(monkeypatch: pytest.MonkeyPatch):
    """Import flashvsr-mlx diffsynth and utils on macOS CPU using conftest-style shims."""
    if str(_FORK_ROOT) not in sys.path:
        monkeypatch.syspath_prepend(str(_FORK_ROOT))
    examples_wan_dir = str(_FORK_ROOT / "examples" / "WanVSR")
    if examples_wan_dir not in sys.path:
        monkeypatch.syspath_prepend(examples_wan_dir)

    for pkg_name, pkg_path in (
        ("diffsynth", _FORK_ROOT / "diffsynth"),
        ("diffsynth.models", _FORK_ROOT / "diffsynth" / "models"),
        ("diffsynth.pipelines", _FORK_ROOT / "diffsynth" / "pipelines"),
        ("diffsynth.schedulers", _FORK_ROOT / "diffsynth" / "schedulers"),
    ):
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(pkg_path)]
        pkg.__package__ = pkg_name
        monkeypatch.setitem(sys.modules, pkg_name, pkg)

    for unused in (
        "diffsynth.pipelines.base",
        "diffsynth.models.rocm_attention",
        "diffsynth.models.wan_video_dit",
        "diffsynth.models.utils",
        "diffsynth.schedulers.flow_match",
        "diffsynth.pipelines.flashvsr_tiny",
        "utils",
        "utils.utils",
        "utils.TCDecoder",
    ):
        monkeypatch.delitem(sys.modules, unused, raising=False)

    # The fork picks its attention at import. Importing the vendored SeedVR2 code earlier in
    # the session leaves a flash_attn stub whose flash_attn_func is None (its
    # ensure_flash_attn_safe), which the fork would take for a working FlashAttention 2.
    for attention in ("flash_attn", "flash_attn_interface", "sageattention"):
        monkeypatch.delitem(sys.modules, attention, raising=False)

    block_sparse_stub = types.ModuleType("block_sparse_attn")
    block_sparse_stub.block_sparse_attn_func = lambda *_a, **_kw: pytest.fail(  # type: ignore[attr-defined]
        "block_sparse_attn_func should not be called under CPU reference test"
    )
    monkeypatch.setitem(sys.modules, "block_sparse_attn", block_sparse_stub)

    diffsynth_models = sys.modules["diffsynth.models"]
    diffsynth_models.ModelManager = object  # type: ignore[attr-defined]
    vae_stub = types.ModuleType("diffsynth.models.wan_video_vae")
    vae_stub.WanVideoVAE = object  # type: ignore[attr-defined]
    vae_stub.RMS_norm = object  # type: ignore[attr-defined]
    vae_stub.CausalConv3d = object  # type: ignore[attr-defined]
    vae_stub.Upsample = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "diffsynth.models.wan_video_vae", vae_stub)

    try:
        import torchvision  # noqa: F401
    except ModuleNotFoundError:
        tv_pkg = types.ModuleType("torchvision")
        tv_transforms = types.ModuleType("torchvision.transforms")
        tv_transforms.GaussianBlur = object  # type: ignore[attr-defined]
        tv_pkg.transforms = tv_transforms  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "torchvision", tv_pkg)
        monkeypatch.setitem(sys.modules, "torchvision.transforms", tv_transforms)

    try:
        import tqdm  # noqa: F401
    except ModuleNotFoundError:

        class _NoopTqdm:
            def __init__(self, iterable=None, *_a, **_kw):
                self._iterable = iterable if iterable is not None else ()

            def __iter__(self):
                return iter(self._iterable)

            def update(self, _n=1):
                pass

            def close(self):
                pass

        tqdm_mod = types.ModuleType("tqdm")
        tqdm_mod.__path__ = []  # type: ignore[attr-defined]
        tqdm_mod.tqdm = _NoopTqdm  # type: ignore[attr-defined]
        tqdm_auto = types.ModuleType("tqdm.auto")
        tqdm_auto.tqdm = _NoopTqdm  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "tqdm", tqdm_mod)
        monkeypatch.setitem(sys.modules, "tqdm.auto", tqdm_auto)

    fork_rocm_attn = importlib.import_module("diffsynth.models.rocm_attention")
    fork_dit_mod = importlib.import_module("diffsynth.models.wan_video_dit")

    orig_flash_attention = fork_dit_mod.flash_attention

    def _ref_flash_attention(
        q,
        k,
        v,
        num_heads,
        compatibility_mode=False,
        attention_mask=None,
        return_KV=False,
    ):
        if attention_mask is not None:
            q_h = q.reshape(q.shape[0], q.shape[1], num_heads, -1).permute(0, 2, 1, 3)
            k_h = k.reshape(k.shape[0], k.shape[1], num_heads, -1).permute(0, 2, 1, 3)
            v_h = v.reshape(v.shape[0], v.shape[1], num_heads, -1).permute(0, 2, 1, 3)
            out = fork_rocm_attn.reference_attention(q_h, k_h, v_h, attention_mask)
            return out.permute(0, 2, 1, 3).reshape(q.shape[0], q.shape[1], -1)
        return orig_flash_attention(
            q,
            k,
            v,
            num_heads=num_heads,
            compatibility_mode=compatibility_mode,
            attention_mask=None,
            return_KV=return_KV,
        )

    monkeypatch.setattr(fork_dit_mod, "flash_attention", _ref_flash_attention)

    fork_pipeline_mod = importlib.import_module("diffsynth.pipelines.flashvsr_tiny")
    fork_lq_mod = importlib.import_module("utils.utils")
    fork_tcd_mod = importlib.import_module("utils.TCDecoder")
    return fork_dit_mod, fork_pipeline_mod, fork_lq_mod, fork_tcd_mod


@pytest.mark.skipif(not _HAS_FORK, reason="flashvsr-mlx fork checkout is not present")
def test_vendored_torch_pipeline_matches_fork_on_cpu_three_chunks(monkeypatch):
    monkeypatch.delenv("FLASHVSR_ATTENTION", raising=False)
    fork_dit_mod, fork_pipeline_mod, fork_lq_mod, fork_tcd_mod = _load_fork_modules(monkeypatch)

    torch.manual_seed(17)
    cfg = {
        "dim": 48,
        "in_dim": 16,
        "ffn_dim": 96,
        "out_dim": 16,
        "text_dim": 32,
        "freq_dim": 16,
        "eps": 1e-6,
        "patch_size": (1, 2, 2),
        "num_heads": 2,
        "num_layers": 2,
    }
    tcd_channels = (16, 16, 8, 8)

    with torch.device("meta"):
        fork_lq = fork_lq_mod.Causal_LQ4x_Proj(in_dim=3, out_dim=cfg["dim"], layer_num=2)
        vendored_lq = Causal_LQ4x_Proj(in_dim=3, out_dim=cfg["dim"], layer_num=2)
    _shrink_lq_proj_for_test(
        fork_lq,
        fork_lq_mod.CausalConv3d,
        fork_lq_mod.RMS_norm,
        out_dim=cfg["dim"],
        layer_num=2,
    )
    _shrink_lq_proj_for_test(
        vendored_lq,
        CausalConv3d,
        RMS_norm,
        out_dim=cfg["dim"],
        layer_num=2,
    )

    fork_dit = fork_dit_mod.WanModel(**cfg).float().eval()
    fork_dit.LQ_proj_in = fork_lq.float().eval()
    fork_tcd = fork_tcd_mod.build_tcdecoder(
        new_channels=list(tcd_channels),
        device="cpu",
        dtype=torch.float32,
        new_latent_channels=16 + 768,
    ).eval()

    vendored_dit = WanModel(**cfg).float().eval()
    vendored_dit.LQ_proj_in = vendored_lq.float().eval()
    vendored_tcd = build_tcdecoder(
        new_channels=tcd_channels,
        device="cpu",
        dtype=torch.float32,
        new_latent_channels=16 + 768,
    ).eval()

    # Copy exact weights from fork models into vendored models.
    vendored_dit.load_state_dict(fork_dit.state_dict(), strict=True)
    vendored_tcd.load_state_dict(fork_tcd.state_dict(), strict=True)

    fork_pipe = fork_pipeline_mod.FlashVSRTinyPipeline(device="cpu", torch_dtype=torch.float32)
    fork_pipe.dit = fork_dit
    fork_pipe.TCDecoder = fork_tcd

    vendored_pipe = VendoredTorchPipeline(
        dit=vendored_dit,
        tcdecoder=vendored_tcd,
        device="cpu",
        torch_dtype=torch.float32,
    )

    context = torch.randn(1, 6, cfg["text_dim"], dtype=torch.float32)
    fork_pipe.init_cross_kv(context_tensor=context)
    vendored_pipe.init_cross_kv(context_tensor=context)

    # 41 frames -> (41 - 1) // 8 - 2 = 3 streaming chunks; 128x256 -> (h=8, w=16) 2D blocks.
    num_frames, height, width = 41, 128, 256
    lq_video = torch.rand(1, 3, num_frames, height, width, dtype=torch.float32) * 2.0 - 1.0
    topk_ratio = 2.0 * 768.0 * 1280.0 / float(height * width)

    fork_out = fork_pipe(
        prompt="",
        negative_prompt="",
        cfg_scale=1.0,
        num_inference_steps=1,
        seed=0,
        LQ_video=lq_video.clone(),
        num_frames=num_frames,
        height=height,
        width=width,
        is_full_block=False,
        if_buffer=True,
        topk_ratio=topk_ratio,
        kv_ratio=3.0,
        local_range=11,
        color_fix=True,
    )
    steps_seen: list[tuple[int, int]] = []
    vendored_out = vendored_pipe(
        height=height,
        width=width,
        num_frames=num_frames,
        seed=0,
        LQ_video=lq_video.clone(),
        is_full_block=False,
        if_buffer=True,
        topk_ratio=topk_ratio,
        kv_ratio=3.0,
        local_range=11,
        color_fix=True,
        progress_callback=lambda step, total: steps_seen.append((step, total)),
    )

    assert vendored_out.shape == fork_out.shape
    assert vendored_out.shape == (3, 37, height, width)
    assert steps_seen == [(1, 3), (2, 3), (3, 3)]
    max_diff = float((vendored_out - fork_out).abs().max().item())
    print(f"Parity max_abs_diff (3 streaming chunks, float32): {max_diff:.3e}")
    assert max_diff <= 1e-4, f"max abs diff {max_diff} exceeded 1e-4"


def test_vendored_torch_from_pretrained_loads_safetensors_and_ckpt_without_prompt_pickle(
    tmp_path, monkeypatch
):
    small_cfg = {
        "dim": 48,
        "in_dim": 16,
        "ffn_dim": 96,
        "out_dim": 16,
        "text_dim": 4096,
        "freq_dim": 16,
        "eps": 1e-6,
        "patch_size": (1, 2, 2),
        "num_heads": 2,
        "num_layers": 1,
    }
    monkeypatch.setattr(
        "localsr.video_models.flashvsr.torch_flashvsr.pipeline.WAN_1_3B_CONFIG",
        small_cfg,
    )

    dit = WanModel(**small_cfg)
    save_safetensors_file(
        dit.state_dict(),
        str(tmp_path / "diffusion_pytorch_model_streaming_dmd.safetensors"),
    )
    with torch.device("meta"):
        lq_proj = Causal_LQ4x_Proj(in_dim=3, out_dim=small_cfg["dim"], layer_num=1)
    _shrink_lq_proj_for_test(lq_proj, CausalConv3d, RMS_norm, out_dim=small_cfg["dim"], layer_num=1)
    torch.save(lq_proj.state_dict(), tmp_path / "LQ_proj_in.ckpt")

    tcd = build_tcdecoder(
        new_channels=(16, 16, 8, 8),
        device="cpu",
        dtype=torch.float32,
        new_latent_channels=16 + 768,
    )
    torch.save(tcd.state_dict(), tmp_path / "TCDecoder.ckpt")

    # Swap constructors inside pipeline module to match tiny lq_proj / tcd channels.
    def tiny_lq_factory(in_dim, out_dim, layer_num=1):
        del in_dim
        with torch.device("meta"):
            mod = Causal_LQ4x_Proj(in_dim=3, out_dim=out_dim, layer_num=layer_num)
        _shrink_lq_proj_for_test(mod, CausalConv3d, RMS_norm, out_dim=out_dim, layer_num=layer_num)
        return mod

    monkeypatch.setattr(
        "localsr.video_models.flashvsr.torch_flashvsr.pipeline.Causal_LQ4x_Proj",
        tiny_lq_factory,
    )
    orig_build_tcdecoder = build_tcdecoder
    monkeypatch.setattr(
        "localsr.video_models.flashvsr.torch_flashvsr.pipeline.build_tcdecoder",
        lambda **_kw: orig_build_tcdecoder(
            new_channels=(16, 16, 8, 8),
            device="cpu",
            dtype=torch.float32,
            new_latent_channels=16 + 768,
        ),
    )

    weights_only_flags: list[bool] = []
    real_torch_load = torch.load

    def spy_torch_load(path, *args, **kwargs):
        if Path(path).name == "posi_prompt.safetensors" or Path(path).suffix == ".pth":
            pytest.fail("Prompt loading must never call torch.load")
        weights_only_flags.append(bool(kwargs.get("weights_only", False)))
        return real_torch_load(path, *args, **kwargs)

    monkeypatch.setattr(torch, "load", spy_torch_load)

    pipe = VendoredTorchPipeline.from_pretrained(tmp_path, device="cpu", dtype=torch.float32)
    assert weights_only_flags == [True, True]
    assert pipe.prompt_emb_posi is not None
    assert tuple(pipe.prompt_emb_posi["context"].shape) == (1, 512, 4096)
    assert pipe.dit is not None and pipe.dit._cross_kv_initialized


def test_gather_attention_matches_reference_on_random_and_lcsa_masks(monkeypatch):
    monkeypatch.delenv("FLASHVSR_ATTENTION", raising=False)
    monkeypatch.delenv("FLASHVSR_ROCM_PRECISION", raising=False)
    torch.manual_seed(123)

    # Random q/k/v with ragged tail (Sq=260, Sk=391), empty rows, full rows, and multi-batch/head.
    q = torch.randn(2, 3, 260, 64, dtype=torch.float32)
    k = torch.randn(2, 3, 391, 64, dtype=torch.float32)
    v = torch.randn(2, 3, 391, 64, dtype=torch.float32)
    mask = torch.rand(2, 3, 3, 4) > 0.5
    mask[0, 0, 1, :] = False  # empty row
    mask[0, 2, :, :] = False  # entire head empty
    mask[1, 1, 0, :] = True  # full row
    mask[1, 2, 2, :] = True  # full row touching ragged tail

    expected = rocm_attention.reference_attention(q, k, v, mask)
    actual_default = rocm_attention.sparse_attention(q, k, v, mask)
    actual_chunked = rocm_attention.gather_attention(q, k, v, mask, chunk_bytes=16 * 1024)

    max_diff = float((actual_default - expected).abs().max().item())
    max_diff_chunked = float((actual_chunked - expected).abs().max().item())
    assert max_diff <= 1e-5
    assert max_diff_chunked <= 1e-5
    assert actual_default[0, 0, 128:256].abs().max().item() == 0.0
    assert actual_default[0, 2].abs().max().item() == 0.0

    # Non-contiguous rearrange layout as produced by flash_attention in wan_video_dit.py,
    # paired with a real LCSA block mask from generate_draft_block_mask.
    B, H, D = 1, 4, 64
    f_blocks, h_blocks, w_blocks = 2, 2, 4  # 8 spatial blocks -> 256*8=2048 tokens for q
    q_w = torch.randn(B * f_blocks * h_blocks * w_blocks, 128, H * D, dtype=torch.float32)
    k_w = torch.randn(B * 3 * h_blocks * w_blocks, 128, H * D, dtype=torch.float32)
    v_w = torch.randn_like(k_w)
    local_mask = build_local_block_mask_shifted_vec_normal_slide(
        h_blocks, w_blocks, win_h=3, win_w=3, include_self=True
    )
    lcsa_mask = generate_draft_block_mask(
        B, H, f_blocks, q_w, k_w, topk=20, local_attn_mask=local_mask
    )
    q_h = rearrange(q_w.reshape(B, -1, H * D), "b s (n d) -> b n s d", n=H)
    k_h = rearrange(k_w.reshape(B, -1, H * D), "b s (n d) -> b n s d", n=H)
    v_h = rearrange(v_w.reshape(B, -1, H * D), "b s (n d) -> b n s d", n=H)
    lcsa_ref = rocm_attention.reference_attention(q_h, k_h, v_h, lcsa_mask)
    lcsa_gather = rocm_attention.sparse_attention(q_h, k_h, v_h, lcsa_mask)
    assert float((lcsa_gather - lcsa_ref).abs().max().item()) <= 1e-5

    # Precision modes on bfloat16 preserve dtype and zero empty rows cleanly.
    q_bf = q.to(torch.bfloat16)
    k_bf = k.to(torch.bfloat16)
    v_bf = v.to(torch.bfloat16)
    for precision in ("native", "fp32"):
        monkeypatch.setenv("FLASHVSR_ROCM_PRECISION", precision)
        out_bf = rocm_attention.sparse_attention(q_bf, k_bf, v_bf, mask)
        assert out_bf.dtype == torch.bfloat16
        assert torch.isfinite(out_bf).all()
        assert out_bf[0, 0, 128:256].abs().max().item() == 0.0

    # All-empty mask returns exact zeros.
    assert (
        rocm_attention.sparse_attention(q, k, v, torch.zeros_like(mask)).abs().max().item() == 0.0
    )

    # Invalid env vars fail loudly.
    monkeypatch.setenv("FLASHVSR_ATTENTION", "bogus")
    with pytest.raises(ValueError, match="FLASHVSR_ATTENTION"):
        rocm_attention.sparse_attention(q, k, v, mask)
    monkeypatch.setenv("FLASHVSR_ATTENTION", "gather")
    monkeypatch.setenv("FLASHVSR_ROCM_PRECISION", "bf16_bad")
    with pytest.raises(ValueError, match="FLASHVSR_ROCM_PRECISION"):
        rocm_attention.sparse_attention(q, k, v, mask)


@pytest.mark.skipif(not _HAS_FLEX_ATTENTION, reason="flex_attention unavailable in this PyTorch")
def test_flex_attention_sparse_path_shapes_and_empty_row_zeroing(monkeypatch):
    from torch.nn.attention.flex_attention import flex_attention as eager_flex_attention

    captured_masks: list = []

    def spy_flex(q_in, k_in, v_in, block_mask=None):
        captured_masks.append(block_mask)
        return eager_flex_attention(q_in, k_in, v_in, block_mask=block_mask)

    # Eager flex_attention executes on CPU (torch.compile for flex_attention targets GPU).
    # Note: eager flex_attention without mask_mod does not apply full_kv_indices; compiled GPU
    # kernels do, so on CPU we verify BlockMask construction, output shape/dtype, and empty-row zeroing.
    monkeypatch.setattr(rocm_attention, "_compiled_attention", lambda: spy_flex)
    monkeypatch.setenv("FLASHVSR_ATTENTION", "flex")

    torch.manual_seed(7)
    q = torch.randn(1, 2, 130, 64, dtype=torch.float32)
    k = torch.randn(1, 2, 260, 64, dtype=torch.float32)
    v = torch.randn_like(k)
    mask = torch.tensor(
        [
            [
                [[True, False, True], [False, True, False]],
                [[False, False, False], [True, True, False]],
            ]
        ],
        dtype=torch.bool,
    )

    for precision in ("native", "fp32"):
        monkeypatch.setenv("FLASHVSR_ROCM_PRECISION", precision)
        out_flex = rocm_attention.sparse_attention(q, k, v, mask)
        assert out_flex.shape == q.shape
        assert out_flex.dtype == q.dtype
        assert torch.isfinite(out_flex).all()
        assert out_flex[:, 1, :128].abs().max().item() == 0.0

    assert len(captured_masks) == 2
    assert captured_masks[0].shape == (1, 2, 130, 260)
    assert captured_masks[0].full_kv_num_blocks.tolist() == [[[2, 1], [0, 2]]]

    monkeypatch.setenv("FLASHVSR_ATTENTION", "reference")
    out_env_ref = rocm_attention.sparse_attention(q, k, v, mask)
    out_ref = rocm_attention.reference_attention(q, k, v, mask)
    torch.testing.assert_close(out_env_ref, out_ref, atol=0.0, rtol=0.0)


def test_grouped_draft_block_mask_and_decoder_cache_hook_lifecycle(monkeypatch):
    torch.manual_seed(42)
    q = torch.randn(12, 128, 256)
    k = torch.randn(18, 128, 256)
    locality = torch.tensor([[True, False, True], [False, True, True]], dtype=torch.bool)
    expected = generate_draft_block_mask(1, 2, 3, q, k, topk=3, local_attn_mask=locality)
    actual = generate_draft_block_mask(
        1, 2, 3, q, k, topk=3, local_attn_mask=locality, _force_grouped=True
    )
    assert torch.equal(actual, expected)

    monkeypatch.setattr(torch.version, "hip", "7.2.0")
    tcd = build_tcdecoder(new_channels=(8, 8, 8, 8), device="cpu", dtype=torch.float32)
    tcd.set_rocm_cache_release(True)
    assert len(tcd._rocm_cache_handles) > 0
    count_once = len(tcd._rocm_cache_handles)
    tcd.set_rocm_cache_release(True)
    assert len(tcd._rocm_cache_handles) == count_once
    tcd.set_rocm_cache_release(False)
    assert len(tcd._rocm_cache_handles) == 0


def test_engine_routing_mps_cuda_rocm_and_refusals(monkeypatch, tmp_path):
    # CUDA without either HIP or CUDA runtime built in is refused.
    monkeypatch.setattr(torch.version, "hip", None)
    monkeypatch.setattr(torch.version, "cuda", None)
    with pytest.raises(ValueError, match="CUDA or AMD ROCm"):
        FlashVSREngine(str(tmp_path), "cuda:0", "bf16")
    with pytest.raises(ValueError, match="CUDA or AMD ROCm"):
        FlashVSREngine(str(tmp_path), "cuda", "bf16")

    # CPU, other GPU backends, and malformed CUDA device names are refused.
    for bad in ("cpu", "xpu:0", "directml:0", "openvino:GPU"):
        with pytest.raises(ValueError, match="Apple Silicon GPU"):
            FlashVSREngine(str(tmp_path), bad, "bf16")
    with pytest.raises(ValueError, match="Invalid"):
        FlashVSREngine(str(tmp_path), "cuda:bad", "bf16")

    # Fake ROCm device routes to torch_flashvsr in bfloat16 and clears CUDA cache on close.
    monkeypatch.setattr(torch.version, "hip", "7.2.0")
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda _dev=0: (16_000_000_000, 17_179_869_184))
    empty_cache_calls: list[bool] = []
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: empty_cache_calls.append(True))

    created: dict[str, object] = {}

    class FakeTorchPipeline:
        def __init__(self):
            self.dit = SimpleNamespace(clear_cross_kv=lambda: None, LQ_proj_in=None)
            self.TCDecoder = SimpleNamespace(
                set_rocm_cache_release=lambda _v: None,
                clean_mem=lambda: None,
            )
            self.ColorCorrector = object()
            self.prompt_emb_posi = {"context": torch.zeros(1)}
            self.t = torch.zeros(1)
            self.t_mod = torch.zeros(1)

        def __call__(self, *, LQ_video, num_frames, progress_callback=None, **_kw):
            created["lq_type"] = type(LQ_video)
            created["lq_dtype"] = LQ_video.dtype
            if progress_callback is not None:
                progress_callback(1, 1)
            return LQ_video[0, :, : num_frames - 4, :, :]

    def fake_from_pretrained(cls, bundle_dir, *, device, dtype):
        created["bundle_dir"] = Path(bundle_dir)
        created["device"] = device
        created["dtype"] = dtype
        return FakeTorchPipeline()

    monkeypatch.setattr(
        VendoredTorchPipeline,
        "from_pretrained",
        classmethod(fake_from_pretrained),
    )

    engine = FlashVSREngine(str(tmp_path), device="cuda:1", precision="fp16")
    assert engine._backend == "rocm"
    assert engine.device == "cuda:1"
    assert created["device"] == "cuda:1"
    assert created["dtype"] == torch.bfloat16

    orig_to = torch.Tensor.to
    seen_to_devices: list[object] = []

    def cpu_redirect_to(self, *args, **kwargs):
        if kwargs.get("device") in ("cuda:1", "cuda"):
            seen_to_devices.append(kwargs["device"])
            kwargs = {**kwargs, "device": "cpu"}
        elif args and args[0] in ("cuda:1", "cuda"):
            seen_to_devices.append(args[0])
            args = ("cpu", *args[1:])
        return orig_to(self, *args, **kwargs)

    monkeypatch.setattr(torch.Tensor, "to", cpu_redirect_to)
    frames = [np.full((32, 48, 3), 128, dtype=np.uint8) for _ in range(4)]
    out = engine.process_frames(frames)
    assert seen_to_devices == ["cuda:1"]
    assert len(out) == 4
    assert out[0].shape == (128, 192, 3)
    assert created["lq_type"] is torch.Tensor
    assert created["lq_dtype"] == torch.bfloat16

    engine.close()
    assert engine.pipeline is None
    assert empty_cache_calls

    # "auto" on Linux with ROCm routes to cuda / rocm.
    monkeypatch.setattr(flashvsr_engine.sys, "platform", "linux")
    auto_engine = FlashVSREngine(str(tmp_path), device="auto", precision="bf16")
    assert auto_engine._backend == "rocm"
    assert auto_engine.device == "cuda"
    auto_engine.close()

    # Fake NVIDIA CUDA (without HIP) routes to torch_flashvsr on both Linux and Windows.
    monkeypatch.setattr(torch.version, "hip", None)
    monkeypatch.setattr(torch.version, "cuda", "12.8")
    empty_cache_calls.clear()
    seen_to_devices.clear()
    cuda_engine = FlashVSREngine(str(tmp_path), device="cuda", precision="bf16")
    assert cuda_engine._backend == "cuda"
    assert cuda_engine.device == "cuda"
    cuda_out = cuda_engine.process_frames(frames)
    assert seen_to_devices == ["cuda"]
    assert len(cuda_out) == 4
    cuda_engine.close()
    assert empty_cache_calls

    monkeypatch.setattr(flashvsr_engine.sys, "platform", "win32")
    win_auto = FlashVSREngine(str(tmp_path), device="auto", precision="bf16")
    assert win_auto._backend == "cuda"
    assert win_auto.device == "cuda"
    win_auto.close()

    # MPS routes to MLX backend when available.
    if sys.platform == "darwin":
        import mlx.core as mx

        fake_mlx_pipe = SimpleNamespace(
            dit=SimpleNamespace(parameters=lambda: {}, freqs=mx.zeros((1,))),
            TCDecoder=None,
            prompt_emb_posi=None,
        )
        monkeypatch.setattr(
            "localsr.video_models.flashvsr.mlx_flashvsr.pipeline.FlashVSRTinyPipeline.from_pretrained",
            classmethod(lambda cls, *_a, **_kw: fake_mlx_pipe),
        )
        mps_engine = FlashVSREngine(str(tmp_path), device="mps", precision="bf16")
        assert mps_engine._backend == "mps"
        assert mps_engine.device == "mps"


def test_cuda_and_rocm_tile_planner_and_budget_from_mem_get_info(monkeypatch):
    from localsr.video_models.flashvsr.engine import (
        DISCRETE_BUDGET_FRACTION,
        ROCM_CALIBRATED_PAD_FRAMES,
    )

    # Measured on the RX 9060 XT at n_pad 41 (see the constants' comment in engine.py).
    assert ROCM_FIXED_MEMORY_BYTES == 4_840_000_000
    assert ROCM_PER_PIXEL_MEMORY_BYTES == 7_040.0
    assert ROCM_CALIBRATED_PAD_FRAMES == 41

    # 16 GB card, 16 GB free: 768x1408 as one tile filled the card and crawled through
    # out-of-memory retries, so it must be split now.
    monkeypatch.setattr(
        torch.cuda, "mem_get_info", lambda _dev="cuda": (16_000_000_000, 17_179_869_184)
    )
    for backend in ("rocm", "cuda"):
        plan = plan_tiles(768, 1408, n_frames=37, backend=backend, device="cuda:0")
        assert plan.budget_bytes == int(16_000_000_000 * DISCRETE_BUDGET_FRACTION)
        assert plan.max_tile_area == int(
            (plan.budget_bytes - ROCM_FIXED_MEMORY_BYTES) / ROCM_PER_PIXEL_MEMORY_BYTES
        )
        assert len(plan.tiles) >= 2
        # 896x512 (measured 8.07 GB) stays one tile.
        assert len(plan_tiles(896, 512, n_frames=37, backend=backend, device="cuda:0").tiles) == 1

        # Longer windows scale the VRAM per-pixel cost without subtracting host-RAM buffers.
        plan_61 = plan_tiles(3840, 2160, n_frames=61, backend=backend, device="cuda:0")
        assert plan_61.max_tile_area == int(
            (plan_61.budget_bytes - ROCM_FIXED_MEMORY_BYTES)
            / (ROCM_PER_PIXEL_MEMORY_BYTES * 65 / 41)
        )

    # 1080p exceeds single-tile ROCm/CUDA area on 16 GB -> tiles cleanly with weights summing to 1.
    plan_1080 = plan_tiles(1920, 1080, backend="cuda", device="cuda:0")
    assert len(plan_1080.tiles) >= 2
    assert plan_1080.tile_width >= MIN_TILE_SIDE and plan_1080.tile_height >= MIN_TILE_SIDE
    canvas = np.zeros((plan_1080.padded_height, plan_1080.padded_width), dtype=np.float32)
    for tile in plan_1080.tiles:
        canvas[tile.y : tile.y + tile.height, tile.x : tile.x + tile.width] += tile.weights
    assert np.allclose(canvas, 1.0, atol=1e-6)

    # Below 512x512 tile area -> refuses without triggering SeedVR2's "out of memory" rewrite.
    low_free = int(
        (ROCM_FIXED_MEMORY_BYTES + 256 * 256 * ROCM_PER_PIXEL_MEMORY_BYTES) / BUDGET_FRACTION
    )
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda _dev="cuda": (low_free, 8_000_000_000))
    with pytest.raises(RuntimeError, match="512x512") as exc_info:
        plan_tiles(1024, 1024, backend="cuda", device="cuda:0")
    assert "out of memory" not in str(exc_info.value).lower()
    assert flashvsr_engine.rocm_free_memory_bytes("cuda:0") == low_free


def test_cuda_and_rocm_runtime_issue_and_server_device_guard(monkeypatch):
    # Triton is never required on ROCm or CUDA.
    monkeypatch.setitem(sys.modules, "triton", None)

    for platform_name, hip_val, cuda_val in (
        ("linux", "7.2.0", None),
        ("linux", None, "12.8"),
        ("win32", None, "12.8"),
    ):
        monkeypatch.setattr(video_engines.sys, "platform", platform_name)
        monkeypatch.setattr(torch.version, "hip", hip_val)
        monkeypatch.setattr(torch.version, "cuda", cuda_val)
        assert video_engines.flashvsr_runtime_issue() is None
        assert resolve_video_engine("flashvsr") is FlashVSREngine
        assert "flashvsr" in _video_engines()
        assert "video_flashvsr" in _engine_features()

    # CPU-only torch refuses FlashVSR with a plain sentence.
    monkeypatch.setattr(video_engines.sys, "platform", "linux")
    monkeypatch.setattr(torch.version, "hip", None)
    monkeypatch.setattr(torch.version, "cuda", None)
    issue = video_engines.flashvsr_runtime_issue()
    assert issue is not None and "NVIDIA CUDA" in issue
    with pytest.raises(TemporalEngineUnavailable, match="NVIDIA CUDA"):
        resolve_video_engine("flashvsr")

    # server.py device guard rejects CPU/XPU/DirectML/OpenVINO and malformed CUDA ids,
    # and rejects cuda:0 on CPU-only torch.
    for bad_dev in ("cpu", "xpu:0", "directml:0", "openvino:GPU"):
        with pytest.raises(ValueError, match="Apple Silicon GPU"):
            WorkerServer._run_video_job(
                SimpleNamespace(), f"bad-{bad_dev}", {"model_kind": "flashvsr", "device": bad_dev}
            )
    with pytest.raises(ValueError, match="Invalid"):
        WorkerServer._run_video_job(
            SimpleNamespace(), "bad-cuda", {"model_kind": "flashvsr", "device": "cuda:bad"}
        )
    with pytest.raises(ValueError, match="NVIDIA CUDA"):
        WorkerServer._run_video_job(
            SimpleNamespace(), "cpu-torch-cuda", {"model_kind": "flashvsr", "device": "cuda:0"}
        )

    for hip_val, cuda_val in (("7.2.0", None), (None, "12.8")):
        monkeypatch.setattr(torch.version, "hip", hip_val)
        monkeypatch.setattr(torch.version, "cuda", cuda_val)
        reached: list[tuple[str, str]] = []
        fake_server = SimpleNamespace(
            _verify_temporal_bundle=lambda _data, _job_id: None,
            _run_temporal_video_job=lambda job_id, data, factory, out=reached: out.append(
                (str(data["device"]), factory.__name__)
            ),
        )
        WorkerServer._run_video_job(
            fake_server,
            "gpu-ok",
            {"model_kind": "flashvsr", "device": "cuda:0", "hdr_mode": "tone_map"},
        )
        assert reached == [("cuda:0", "FlashVSREngine")]
