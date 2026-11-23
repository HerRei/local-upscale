from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
import spandrel

from localsr.core.model_adapter import (
    UNVERIFIED_CHECKPOINT_ENV,
    CheckpointSecurityError,
    ModelAdapter,
)
from localsr.core.model_catalog import CATALOG_BY_FILENAME


@pytest.mark.parametrize("suffix", [".pth", ".pt", ".ckpt"])
def test_unverified_pickle_checkpoint_is_blocked_before_spandrel(tmp_path, monkeypatch, suffix):
    checkpoint = tmp_path / f"downloaded-from-a-forum{suffix}"
    checkpoint.write_bytes(b"not deserialized")
    monkeypatch.delenv(UNVERIFIED_CHECKPOINT_ENV, raising=False)
    adapter = ModelAdapter()
    called = False

    def unexpected_load(_path):
        nonlocal called
        called = True
        raise AssertionError("Spandrel must not receive an unverified pickle checkpoint")

    monkeypatch.setattr(adapter.loader, "load_from_file", unexpected_load)
    with pytest.raises(CheckpointSecurityError, match="blocks unverified"):
        adapter.inspect(str(checkpoint))
    assert called is False


def test_unverified_pickle_checkpoint_requires_explicit_opt_in(tmp_path):
    checkpoint = tmp_path / "trusted-by-user.ckpt"
    checkpoint.write_bytes(b"checkpoint")
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    assert adapter._checkpoint_trust(str(checkpoint)) == "explicitly-trusted"


def test_safetensors_is_allowed_without_opt_in(tmp_path):
    checkpoint = tmp_path / "weights.safetensors"
    checkpoint.write_bytes(b"safe format fixture")
    adapter = ModelAdapter(allow_unverified_checkpoints=False)
    assert adapter._checkpoint_trust(str(checkpoint)) == "safe-format"


def test_catalog_checkpoint_requires_exact_digest_even_with_unsafe_opt_in(tmp_path, monkeypatch):
    expected = b"catalog bytes"
    checkpoint = tmp_path / "catalog-test.pth"
    checkpoint.write_bytes(expected)
    model = SimpleNamespace(
        name="Catalog Test",
        size_bytes=len(expected),
        sha256=hashlib.sha256(expected).hexdigest(),
    )
    monkeypatch.setitem(CATALOG_BY_FILENAME, checkpoint.name, model)
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    assert adapter._checkpoint_trust(str(checkpoint)) == "catalog-verified"

    checkpoint.write_bytes(b"tampered byte")
    with pytest.raises(CheckpointSecurityError, match="SHA-256"):
        adapter._checkpoint_trust(str(checkpoint))


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ({"gblocks.0.weight", "gblocks.1.fc1.weight", "gblocks.1.conv.weight"}, "MoSR"),
        ({"in_to_dim.weight", "blocks.0.blocks.0.conv.dwconv_hw.weight"}, "MoESR"),
        ({"unrelated.weight"}, "could not be recognized"),
    ],
)
def test_unsupported_checkpoint_reports_architecture_hint(tmp_path, monkeypatch, keys, expected):
    checkpoint = tmp_path / "custom.safetensors"
    checkpoint.write_bytes(b"fixture")
    adapter = ModelAdapter()

    def unsupported(_path):
        raise spandrel.UnsupportedModelError()

    monkeypatch.setattr(adapter.loader, "load_from_file", unsupported)
    monkeypatch.setattr(
        adapter.loader, "load_state_dict_from_file", lambda _path: dict.fromkeys(keys)
    )
    with pytest.raises(RuntimeError, match=expected) as error:
        adapter.inspect(str(checkpoint))
    if expected in {"MoSR", "MoESR"}:
        assert "not supported yet" in str(error.value)
        assert "https://github.com/HerRei/local-upscale/issues/154" in str(error.value)


def test_remembered_consent_refuses_changed_checkpoint(tmp_path):
    checkpoint = tmp_path / "trusted.pth"
    checkpoint.write_bytes(b"approved")
    adapter = ModelAdapter(allow_unverified_checkpoints=True)
    adapter.approved_checkpoint = (str(checkpoint), hashlib.sha256(b"approved").hexdigest())
    assert adapter._checkpoint_trust(str(checkpoint)) == "explicitly-trusted"
    checkpoint.write_bytes(b"replaced")
    with pytest.raises(CheckpointSecurityError, match="changed"):
        adapter._checkpoint_trust(str(checkpoint))
