# Copyright (c) 2023 piddnad / DDColor authors. Apache-2.0 License (see NOTICE.md).
# Adapted from piddnad/DDColor (basicsr/archs/ddcolor_arch_utils/transformer_utils.py).

from torch import Tensor, nn
from torch.nn import functional as F


def _get_activation_fn(activation: str):
    if activation == "relu":
        return F.relu
    if activation == "gelu":
        return F.gelu
    if activation == "glu":
        return F.glu
    raise RuntimeError(f"activation should be relu/gelu, not {activation}.")


class SelfAttentionLayer(nn.Module):
    def __init__(
        self,
        d_model: int,
        nhead: int,
        dropout: float = 0.0,
        activation: str = "relu",
        normalize_before: bool = False,
    ):
        super().__init__()
        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.norm = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout)
        self.activation = _get_activation_fn(activation)
        self.normalize_before = normalize_before
        self._reset_parameters()

    def _reset_parameters(self) -> None:
        for p in self.parameters():
            if p.dim() > 1:
                nn.init.xavier_uniform_(p)

    def with_pos_embed(self, tensor: Tensor, pos: Tensor | None) -> Tensor:
        return tensor if pos is None else tensor + pos

    def forward_post(
        self,
        tgt: Tensor,
        tgt_mask: Tensor | None = None,
        tgt_key_padding_mask: Tensor | None = None,
        query_pos: Tensor | None = None,
    ) -> Tensor:
        q = k = self.with_pos_embed(tgt, query_pos)
        tgt2 = self.self_attn(
            q, k, value=tgt, attn_mask=tgt_mask, key_padding_mask=tgt_key_padding_mask
        )[0]
        tgt = tgt + self.dropout(tgt2)
        return self.norm(tgt)

    def forward_pre(
        self,
        tgt: Tensor,
        tgt_mask: Tensor | None = None,
        tgt_key_padding_mask: Tensor | None = None,
        query_pos: Tensor | None = None,
    ) -> Tensor:
        tgt2 = self.norm(tgt)
        q = k = self.with_pos_embed(tgt2, query_pos)
        tgt2 = self.self_attn(
            q, k, value=tgt2, attn_mask=tgt_mask, key_padding_mask=tgt_key_padding_mask
        )[0]
        return tgt + self.dropout(tgt2)

    def forward(
        self,
        tgt: Tensor,
        tgt_mask: Tensor | None = None,
        tgt_key_padding_mask: Tensor | None = None,
        query_pos: Tensor | None = None,
    ) -> Tensor:
        if self.normalize_before:
            return self.forward_pre(tgt, tgt_mask, tgt_key_padding_mask, query_pos)
        return self.forward_post(tgt, tgt_mask, tgt_key_padding_mask, query_pos)


class CrossAttentionLayer(nn.Module):
    def __init__(
        self,
        d_model: int,
        nhead: int,
        dropout: float = 0.0,
        activation: str = "relu",
        normalize_before: bool = False,
    ):
        super().__init__()
        self.multihead_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.norm = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout)
        self.activation = _get_activation_fn(activation)
        self.normalize_before = normalize_before
        self._reset_parameters()

    def _reset_parameters(self) -> None:
        for p in self.parameters():
            if p.dim() > 1:
                nn.init.xavier_uniform_(p)

    def with_pos_embed(self, tensor: Tensor, pos: Tensor | None) -> Tensor:
        return tensor if pos is None else tensor + pos

    def forward_post(
        self,
        tgt: Tensor,
        memory: Tensor,
        memory_mask: Tensor | None = None,
        memory_key_padding_mask: Tensor | None = None,
        pos: Tensor | None = None,
        query_pos: Tensor | None = None,
    ) -> Tensor:
        tgt2 = self.multihead_attn(
            query=self.with_pos_embed(tgt, query_pos),
            key=self.with_pos_embed(memory, pos),
            value=memory,
            attn_mask=memory_mask,
            key_padding_mask=memory_key_padding_mask,
        )[0]
        tgt = tgt + self.dropout(tgt2)
        return self.norm(tgt)

    def forward_pre(
        self,
        tgt: Tensor,
        memory: Tensor,
        memory_mask: Tensor | None = None,
        memory_key_padding_mask: Tensor | None = None,
        pos: Tensor | None = None,
        query_pos: Tensor | None = None,
    ) -> Tensor:
        tgt2 = self.norm(tgt)
        tgt2 = self.multihead_attn(
            query=self.with_pos_embed(tgt2, query_pos),
            key=self.with_pos_embed(memory, pos),
            value=memory,
            attn_mask=memory_mask,
            key_padding_mask=memory_key_padding_mask,
        )[0]
        return tgt + self.dropout(tgt2)

    def forward(
        self,
        tgt: Tensor,
        memory: Tensor,
        memory_mask: Tensor | None = None,
        memory_key_padding_mask: Tensor | None = None,
        pos: Tensor | None = None,
        query_pos: Tensor | None = None,
    ) -> Tensor:
        if self.normalize_before:
            return self.forward_pre(
                tgt, memory, memory_mask, memory_key_padding_mask, pos, query_pos
            )
        return self.forward_post(tgt, memory, memory_mask, memory_key_padding_mask, pos, query_pos)


class FFNLayer(nn.Module):
    def __init__(
        self,
        d_model: int,
        dim_feedforward: int = 2048,
        dropout: float = 0.0,
        activation: str = "relu",
        normalize_before: bool = False,
    ):
        super().__init__()
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(dim_feedforward, d_model)
        self.norm = nn.LayerNorm(d_model)
        self.activation = _get_activation_fn(activation)
        self.normalize_before = normalize_before
        self._reset_parameters()

    def _reset_parameters(self) -> None:
        for p in self.parameters():
            if p.dim() > 1:
                nn.init.xavier_uniform_(p)

    def forward_post(self, tgt: Tensor) -> Tensor:
        tgt2 = self.linear2(self.dropout(self.activation(self.linear1(tgt))))
        tgt = tgt + self.dropout(tgt2)
        return self.norm(tgt)

    def forward_pre(self, tgt: Tensor) -> Tensor:
        tgt2 = self.norm(tgt)
        tgt2 = self.linear2(self.dropout(self.activation(self.linear1(tgt2))))
        return tgt + self.dropout(tgt2)

    def forward(self, tgt: Tensor) -> Tensor:
        if self.normalize_before:
            return self.forward_pre(tgt)
        return self.forward_post(tgt)


class MLP(nn.Module):
    """Multi-layer perceptron."""

    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int, num_layers: int):
        super().__init__()
        self.num_layers = num_layers
        h = [hidden_dim] * (num_layers - 1)
        self.layers = nn.ModuleList(
            nn.Linear(n, k) for n, k in zip([input_dim] + h, h + [output_dim], strict=True)
        )

    def forward(self, x: Tensor) -> Tensor:
        for i, layer in enumerate(self.layers):
            x = F.relu(layer(x)) if i < self.num_layers - 1 else layer(x)
        return x
