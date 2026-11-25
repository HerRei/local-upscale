"""A disposable minisign key that signs the way `tauri signer` does (prehashed).

For tests and the upgrade check only: it signs release files with a key that exists for
one run, so an older release's updater can check them with that key's public half.
Nothing signed with it is ever published.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from verify_prepared_release import _G, _P, _Q, _mul

CHUNK = 4 * 1024 * 1024


def _compress(point: tuple[int, int, int, int]) -> bytes:
    inverse = pow(point[2], _P - 2, _P)
    x, y = point[0] * inverse % _P, point[1] * inverse % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


class SigningKey:
    """An Ed25519 key in minisign's format; `public_key` is what the app is given."""

    def __init__(self, seed: bytes | None = None, key_id: bytes = b"lsrprobe"):
        seed = seed if seed is not None else os.urandom(32)
        digest = hashlib.sha512(seed).digest()
        scalar = int.from_bytes(digest[:32], "little")
        self.scalar = (scalar & ((1 << 254) - 8)) | (1 << 254)
        self.prefix = digest[32:]
        self.key_id = key_id
        self.point = _compress(_mul(self.scalar, _G))
        text = (
            f"untrusted comment: minisign public key: {key_id.hex().upper()}\n"
            + base64.b64encode(b"Ed" + key_id + self.point).decode()
            + "\n"
        )
        self.public_key = base64.b64encode(text.encode()).decode()

    def _raw(self, message: bytes) -> bytes:
        r = int.from_bytes(hashlib.sha512(self.prefix + message).digest(), "little") % _Q
        commitment = _compress(_mul(r, _G))
        h = (
            int.from_bytes(hashlib.sha512(commitment + self.point + message).digest(), "little")
            % _Q
        )
        return commitment + ((r + h * self.scalar) % _Q).to_bytes(32, "little")

    def _signature(self, prehash: bytes, name: str) -> str:
        signature = self._raw(prehash)
        trusted = f"timestamp:0\tfile:{name}"
        text = (
            "untrusted comment: signature from tauri secret key\n"
            + base64.b64encode(b"ED" + self.key_id + signature).decode()
            + f"\ntrusted comment: {trusted}\n"
            + base64.b64encode(self._raw(signature + trusted.encode())).decode()
            + "\n"
        )
        return base64.b64encode(text.encode()).decode()

    def signature(self, data: bytes, name: str = "file") -> str:
        return self._signature(hashlib.blake2b(data, digest_size=64).digest(), name)

    def sign_file(self, path: Path) -> None:
        """Write <path>.sig, reading the file in chunks (engine parts reach 2 GB)."""
        digest = hashlib.blake2b(digest_size=64)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK), b""):
                digest.update(chunk)
        path.with_name(path.name + ".sig").write_text(
            self._signature(digest.digest(), path.name), encoding="utf-8"
        )
