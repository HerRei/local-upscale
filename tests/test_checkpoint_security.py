from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from localsr.core.model_adapter import (
    UNVERIFIED_CHECKPOINT_ENV,
    CheckpointSecurityError,
    ModelAdapter,
)
from localsr.core.model_catalog import CATALOG_BY_FILENAME


def test_unverified_pickle_checkpoint_is_blocked_before_spandrel(tmp_path, monkeypatch):
    checkpoint = tmp_path / "downloaded-from-a-forum.pth"
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
