"""CPU unit, parity, routing, and tile-planner tests for PyTorch/ROCm FlashVSR."""

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
    monkeypatch.setenv("FLASHVSR_ROCM_ATTENTION", "flex")

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

    monkeypatch.setenv("FLASHVSR_ROCM_ATTENTION", "reference")
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


def test_engine_routing_mps_rocm_and_refusals(monkeypatch, tmp_path):
    # CUDA without HIP is refused.
    monkeypatch.setattr(torch.version, "hip", None)
    with pytest.raises(ValueError, match="ROCm"):
        FlashVSREngine(str(tmp_path), "cuda:0", "bf16")
    with pytest.raises(ValueError, match="ROCm"):
        FlashVSREngine(str(tmp_path), "cuda", "bf16")

    # CPU and invalid devices are refused.
    with pytest.raises(ValueError, match="Apple Silicon GPU"):
        FlashVSREngine(str(tmp_path), "cpu", "bf16")
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
        if kwargs.get("device") == "cuda:1":
            seen_to_devices.append(kwargs["device"])
            kwargs = {**kwargs, "device": "cpu"}
        elif args and args[0] == "cuda:1":
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


def test_rocm_tile_planner_and_budget_from_mem_get_info(monkeypatch):
    assert ROCM_FIXED_MEMORY_BYTES == 4_900_000_000
    assert ROCM_PER_PIXEL_MEMORY_BYTES == 6_300.0

    # 16 GB RX 9060 XT: 16 GB free -> budget 13.6 GB -> 768x1408 fits in 1 tile.
    monkeypatch.setattr(
        torch.cuda, "mem_get_info", lambda _dev="cuda": (16_000_000_000, 17_179_869_184)
    )
    plan_single = plan_tiles(768, 1408, backend="rocm", device="cuda:0")
    assert plan_single.budget_bytes == int(16_000_000_000 * BUDGET_FRACTION)
    expected_area = int(
        (plan_single.budget_bytes - ROCM_FIXED_MEMORY_BYTES) / ROCM_PER_PIXEL_MEMORY_BYTES
    )
    assert plan_single.max_tile_area == expected_area
    assert len(plan_single.tiles) == 1
    assert (plan_single.tile_width, plan_single.tile_height) == (768, 1408)

    # 1080p exceeds single-tile ROCm area on 16 GB -> tiles cleanly with weights summing to 1.
    plan_1080 = plan_tiles(1920, 1080, backend="rocm", device="cuda:0")
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
        plan_tiles(1024, 1024, backend="rocm", device="cuda:0")
    assert "out of memory" not in str(exc_info.value).lower()
    assert flashvsr_engine.rocm_free_memory_bytes("cuda:0") == low_free


def test_linux_rocm_runtime_issue_and_server_device_guard(monkeypatch):
    monkeypatch.setattr(video_engines.sys, "platform", "linux")
    monkeypatch.setattr(torch.version, "hip", "7.2.0")

    # Missing triton -> unavailable with plain sentence.
    monkeypatch.setitem(sys.modules, "triton", None)
    issue = video_engines.flashvsr_runtime_issue()
    assert issue is not None and "Triton" in issue
    with pytest.raises(TemporalEngineUnavailable, match="Triton"):
        resolve_video_engine("flashvsr")

    # With triton and flex_attention available -> FlashVSR enabled on Linux ROCm.
    monkeypatch.setitem(sys.modules, "triton", types.ModuleType("triton"))
    if not _HAS_FLEX_ATTENTION:
        fake_flex = types.ModuleType("torch.nn.attention.flex_attention")
        fake_flex.BlockMask = object  # type: ignore[attr-defined]
        fake_flex.flex_attention = object  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "torch.nn.attention.flex_attention", fake_flex)
    assert video_engines.flashvsr_runtime_issue() is None
    assert resolve_video_engine("flashvsr") is FlashVSREngine
    assert "flashvsr" in _video_engines()
    assert "video_flashvsr" in _engine_features()

    # server.py device guard rejects non-ROCm CUDA and CPU, accepts ROCm cuda[:N].
    monkeypatch.setattr(torch.version, "hip", None)
    with pytest.raises(ValueError, match="ROCm"):
        WorkerServer._run_video_job(
            SimpleNamespace(), "non-rocm", {"model_kind": "flashvsr", "device": "cuda:0"}
        )
    with pytest.raises(ValueError, match="Apple Silicon GPU"):
        WorkerServer._run_video_job(
            SimpleNamespace(), "cpu-flash", {"model_kind": "flashvsr", "device": "cpu"}
        )

    monkeypatch.setattr(torch.version, "hip", "7.2.0")
    reached: list[tuple[str, str]] = []
    fake_server = SimpleNamespace(
        _verify_temporal_bundle=lambda _data, _job_id: None,
        _run_temporal_video_job=lambda job_id, data, factory: reached.append(
            (str(data["device"]), factory.__name__)
        ),
    )
    WorkerServer._run_video_job(
        fake_server,
        "rocm-ok",
        {"model_kind": "flashvsr", "device": "cuda:0", "hdr_mode": "tone_map"},
    )
    assert reached == [("cuda:0", "FlashVSREngine")]
