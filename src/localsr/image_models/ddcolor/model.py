# Copyright (c) 2023 piddnad / DDColor authors. Apache-2.0 License (see NOTICE.md).
# Adapted from piddnad/DDColor (ddcolor/model.py).
# Modified for LocalSR: inference-only DDColor with configurable dims/depths for testing.

from collections.abc import Sequence

import torch
import torch.nn as nn

from .convnext import ConvNeXt
from .position_encoding import PositionEmbeddingSine
from .transformer_utils import MLP, CrossAttentionLayer, FFNLayer, SelfAttentionLayer
from .unet import CustomPixelShuffle_ICNR, Hook, NormType, UnetBlockWide, custom_conv_layer


class ImageEncoder(nn.Module):
    def __init__(
        self,
        encoder_name: str,
        hook_names: Sequence[str],
        *,
        depths: Sequence[int] | None = None,
        dims: Sequence[int] | None = None,
    ):
        super().__init__()
        if depths is not None and dims is not None:
            self.arch = ConvNeXt(depths=list(depths), dims=list(dims))
        elif encoder_name in {"convnext-t", "convnext"}:
            self.arch = ConvNeXt(depths=[3, 3, 9, 3], dims=[96, 192, 384, 768])
        elif encoder_name == "convnext-l":
            self.arch = ConvNeXt(depths=[3, 3, 27, 3], dims=[192, 384, 768, 1536])
        else:
            raise ValueError(f"Unsupported DDColor encoder: {encoder_name}")

        self.encoder_name = encoder_name
        self.hook_names = list(hook_names)
        self.hooks = [Hook(self.arch._modules[name]) for name in self.hook_names]  # type: ignore[arg-type]

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.arch(x)


class MultiScaleColorDecoder(nn.Module):
    def __init__(
        self,
        in_channels: Sequence[int],
        hidden_dim: int = 256,
        num_queries: int = 100,
        nheads: int = 8,
        dim_feedforward: int = 2048,
        dec_layers: int = 9,
        pre_norm: bool = False,
        color_embed_dim: int = 256,
        enforce_input_project: bool = True,
        num_scales: int = 3,
    ):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_queries = num_queries
        self.num_layers = dec_layers
        self.num_feature_levels = num_scales

        self.pe_layer = PositionEmbeddingSine(hidden_dim // 2, normalize=True)
        self.query_feat = nn.Embedding(num_queries, hidden_dim)
        self.query_embed = nn.Embedding(num_queries, hidden_dim)
        self.level_embed = nn.Embedding(num_scales, hidden_dim)

        self.input_proj = nn.ModuleList(
            [
                self._make_input_proj(in_ch, hidden_dim, enforce_input_project)
                for in_ch in in_channels
            ]
        )

        self.transformer_self_attention_layers = nn.ModuleList()
        self.transformer_cross_attention_layers = nn.ModuleList()
        self.transformer_ffn_layers = nn.ModuleList()
        for _ in range(dec_layers):
            self.transformer_self_attention_layers.append(
                SelfAttentionLayer(
                    d_model=hidden_dim, nhead=nheads, dropout=0.0, normalize_before=pre_norm
                )
            )
            self.transformer_cross_attention_layers.append(
                CrossAttentionLayer(
                    d_model=hidden_dim, nhead=nheads, dropout=0.0, normalize_before=pre_norm
                )
            )
            self.transformer_ffn_layers.append(
                FFNLayer(
                    d_model=hidden_dim,
                    dim_feedforward=dim_feedforward,
                    dropout=0.0,
                    normalize_before=pre_norm,
                )
            )

        self.decoder_norm = nn.LayerNorm(hidden_dim)
        self.color_embed = MLP(hidden_dim, hidden_dim, color_embed_dim, 3)

    def _make_input_proj(self, in_ch: int, hidden_dim: int, enforce: bool) -> nn.Module:
        if in_ch != hidden_dim or enforce:
            proj = nn.Conv2d(in_ch, hidden_dim, kernel_size=1)
            nn.init.kaiming_uniform_(proj.weight, a=1)
            if proj.bias is not None:
                nn.init.constant_(proj.bias, 0)
            return proj
        return nn.Sequential()

    def _get_src_and_pos(
        self, x: Sequence[torch.Tensor]
    ) -> tuple[list[torch.Tensor], list[torch.Tensor]]:
        src: list[torch.Tensor] = []
        pos: list[torch.Tensor] = []
        for i, feature in enumerate(x):
            pos.append(self.pe_layer(feature).flatten(2).permute(2, 0, 1))
            projected = self.input_proj[i](feature).flatten(2)
            src.append((projected + self.level_embed.weight[i][None, :, None]).permute(2, 0, 1))
        return src, pos

    def forward(self, x: Sequence[torch.Tensor], img_features: torch.Tensor) -> torch.Tensor:
        assert len(x) == self.num_feature_levels
        src, pos = self._get_src_and_pos(x)
        bs = src[0].shape[1]

        query_embed = self.query_embed.weight.unsqueeze(1).repeat(1, bs, 1)
        output = self.query_feat.weight.unsqueeze(1).repeat(1, bs, 1)

        for i in range(self.num_layers):
            level_index = i % self.num_feature_levels
            output = self.transformer_cross_attention_layers[i](
                output,
                src[level_index],
                memory_mask=None,
                memory_key_padding_mask=None,
                pos=pos[level_index],
                query_pos=query_embed,
            )
            output = self.transformer_self_attention_layers[i](
                output,
                tgt_mask=None,
                tgt_key_padding_mask=None,
                query_pos=query_embed,
            )
            output = self.transformer_ffn_layers[i](output)

        decoder_output = self.decoder_norm(output).transpose(0, 1)
        color_embed = self.color_embed(decoder_output)
        return torch.einsum("bqc,bchw->bqhw", color_embed, img_features)


class DuelDecoder(nn.Module):
    def __init__(
        self,
        hooks: Sequence[Hook],
        nf: int = 512,
        blur: bool = True,
        last_norm: str = "Weight",
        num_queries: int = 256,
        num_scales: int = 3,
        dec_layers: int = 9,
        decoder_name: str = "MultiScaleColorDecoder",
        hidden_dim: int = 256,
        nheads: int = 8,
        dim_feedforward: int = 2048,
    ):
        super().__init__()
        self.hooks = list(hooks)
        self.nf = nf
        self.blur = blur
        self.last_norm = getattr(NormType, last_norm)
        self.decoder_name = decoder_name
        if decoder_name != "MultiScaleColorDecoder":
            raise ValueError(f"Unsupported DDColor decoder: {decoder_name}")

        self.layers = self.make_layers()
        embed_dim = nf // 2
        self.last_shuf = CustomPixelShuffle_ICNR(
            embed_dim, embed_dim, blur=self.blur, norm_type=self.last_norm, scale=4
        )
        self.color_decoder = MultiScaleColorDecoder(
            in_channels=[nf, nf, embed_dim],
            hidden_dim=hidden_dim,
            num_queries=num_queries,
            nheads=nheads,
            dim_feedforward=dim_feedforward,
            dec_layers=dec_layers,
            color_embed_dim=embed_dim,
            num_scales=num_scales,
        )

    def make_layers(self) -> nn.Sequential:
        decoder_layers: list[nn.Module] = []
        last_feature = self.hooks[-1].feature
        assert last_feature is not None
        in_c = last_feature.shape[1]
        out_c = self.nf

        setup_hooks = self.hooks[-2::-1]
        for layer_index, hook in enumerate(setup_hooks):
            assert hook.feature is not None
            feature_c = hook.feature.shape[1]
            if layer_index == len(setup_hooks) - 1:
                out_c = out_c // 2
            decoder_layers.append(
                UnetBlockWide(
                    in_c,
                    feature_c,
                    out_c,
                    hook,
                    blur=self.blur,
                    self_attention=False,
                    norm_type=NormType.Spectral,
                )
            )
            in_c = out_c
        return nn.Sequential(*decoder_layers)

    def forward(self) -> torch.Tensor:
        encode_feat = self.hooks[-1].feature
        assert encode_feat is not None
        out0 = self.layers[0](encode_feat)
        out1 = self.layers[1](out0)
        out2 = self.layers[2](out1)
        out3 = self.last_shuf(out2)
        return self.color_decoder([out0, out1, out2], out3)


class DDColor(nn.Module):
    def __init__(
        self,
        encoder_name: str = "convnext-l",
        decoder_name: str = "MultiScaleColorDecoder",
        num_input_channels: int = 3,
        input_size: tuple[int, int] = (512, 512),
        nf: int = 512,
        num_output_channels: int = 2,
        last_norm: str = "Spectral",
        do_normalize: bool = False,
        num_queries: int = 100,
        num_scales: int = 3,
        dec_layers: int = 9,
        depths: Sequence[int] | None = None,
        dims: Sequence[int] | None = None,
        hidden_dim: int = 256,
        nheads: int = 8,
        dim_feedforward: int = 2048,
    ):
        super().__init__()
        self.encoder = ImageEncoder(
            encoder_name, ["norm0", "norm1", "norm2", "norm3"], depths=depths, dims=dims
        )
        self.encoder.eval()
        test_input = torch.randn(1, num_input_channels, *input_size)
        with torch.no_grad():
            self.encoder(test_input)

        self.decoder = DuelDecoder(
            self.encoder.hooks,
            nf=nf,
            last_norm=last_norm,
            num_queries=num_queries,
            num_scales=num_scales,
            dec_layers=dec_layers,
            decoder_name=decoder_name,
            hidden_dim=hidden_dim,
            nheads=nheads,
            dim_feedforward=dim_feedforward,
        )
        self.refine_net = nn.Sequential(
            custom_conv_layer(
                num_queries + 3,
                num_output_channels,
                ks=1,
                use_activ=False,
                norm_type=NormType.Spectral,
            )
        )
        self.do_normalize = do_normalize
        self.register_buffer("mean", torch.Tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer("std", torch.Tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))
        self.mean: torch.Tensor
        self.std: torch.Tensor

    def normalize(self, img: torch.Tensor) -> torch.Tensor:
        return (img - self.mean) / self.std

    def denormalize(self, img: torch.Tensor) -> torch.Tensor:
        return img * self.std + self.mean

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.shape[1] == 3:
            x = self.normalize(x)
        self.encoder(x)
        out_feat = self.decoder()
        coarse_input = torch.cat([out_feat, x], dim=1)
        out = self.refine_net(coarse_input)
        if self.do_normalize:
            out = self.denormalize(out)
        return out
