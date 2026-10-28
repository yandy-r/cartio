"""Sealing job credentials to one collector (ADR-0009).

Ed25519 keys only sign, so at enrollment the collector also makes an X25519 key pair for
sealing and sends the public half. The controller seals a job's credentials to it: an
ephemeral X25519 key agreement, HKDF-SHA256, then AES-GCM. Only that collector can open them,
so a TLS-inspecting proxy on the path sees the job but never a device password.

The sealed text is bound to a context, the job id, so it can't be moved into another job.
Keys and sealed text travel as unpadded base64url.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_INFO = b"cartio-seal-v1"
_KEY_BYTES = 32
_NONCE_BYTES = 12


class SealError(ValueError):
    """A key or sealed text is malformed, or the text wasn't sealed to this key and context."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise SealError("not base64url") from exc


def new_private_key() -> str:
    """A new X25519 private key, for the collector to keep and never send."""
    return _b64encode(X25519PrivateKey.generate().private_bytes_raw())


def public_key_for(private_key: str) -> str:
    key = X25519PrivateKey.from_private_bytes(_b64decode(private_key))
    return _b64encode(key.public_key().public_bytes_raw())


def check_public_key(text: str) -> str:
    """Return `text` if it is an X25519 public key, or raise SealError."""
    raw = _b64decode(text)
    if len(raw) != _KEY_BYTES:
        raise SealError("an X25519 public key is 32 bytes")
    return _b64encode(X25519PublicKey.from_public_bytes(raw).public_bytes_raw())


def _key(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO + ephemeral + recipient
    ).derive(shared)


def seal(public_key: str, plaintext: bytes, context: bytes) -> str:
    """Encrypt `plaintext` so only the holder of `public_key`'s private key can read it."""
    recipient = _b64decode(check_public_key(public_key))
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes_raw()
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(recipient))
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(_key(shared, ephemeral_public, recipient)).encrypt(nonce, plaintext, context)
    return _b64encode(ephemeral_public + nonce + sealed)


def open_sealed(private_key: str, sealed: str, context: bytes) -> bytes:
    """Decrypt text from `seal`. Raises SealError for another key, context or a changed text."""
    raw = _b64decode(sealed)
    if len(raw) < _KEY_BYTES + _NONCE_BYTES + 16:
        raise SealError("sealed text is too short")
    ephemeral, nonce, body = (
        raw[:_KEY_BYTES],
        raw[_KEY_BYTES : _KEY_BYTES + _NONCE_BYTES],
        raw[_KEY_BYTES + _NONCE_BYTES :],
    )
    key = X25519PrivateKey.from_private_bytes(_b64decode(private_key))
    recipient = key.public_key().public_bytes_raw()
    shared = key.exchange(X25519PublicKey.from_public_bytes(ephemeral))
    try:
        return AESGCM(_key(shared, ephemeral, recipient)).decrypt(nonce, body, context)
    except InvalidTag as exc:
        raise SealError("sealed for another key or job, or changed on the way") from exc
