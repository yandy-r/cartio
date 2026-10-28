"""Envelope encryption for device credentials (ADR-0003, Hosted mode).

Each customer has its own data key. Data keys are stored in the database only wrapped (encrypted)
by the master key, which lives outside the database: in a key file named by
CARTIO_MASTER_KEY_FILE, or in CARTIO_MASTER_KEY. A database dump alone reveals no secret.

The master key sits behind `MasterKey`, so a KMS or an external vault can replace the local key
later without touching the credential code.
"""

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.exceptions import ImproperlyConfigured
from django.views.decorators.debug import sensitive_variables

KEY_BYTES = 32
_NONCE_BYTES = 12


class VaultError(Exception):
    """A secret couldn't be decrypted: wrong key, or the stored bytes were changed or moved."""


@sensitive_variables("key", "plaintext")
def encrypt(key: bytes, plaintext: bytes, context: bytes) -> bytes:
    """AES-256-GCM. `context` is bound to the ciphertext, so it can't be moved to another row."""
    nonce = secrets.token_bytes(_NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, context)


@sensitive_variables("key")
def decrypt(key: bytes, blob: bytes, context: bytes) -> bytes:
    nonce, ciphertext = bytes(blob[:_NONCE_BYTES]), bytes(blob[_NONCE_BYTES:])
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, context)
    except InvalidTag as exc:
        raise VaultError("the secret can't be decrypted with this key and context") from exc


def new_data_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


@dataclass(frozen=True, repr=False)
class MasterKey:
    """The key that wraps every customer's data key. Never stored in the database."""

    key_id: str
    _key: bytes

    def __post_init__(self) -> None:
        if len(self._key) != KEY_BYTES:
            raise ImproperlyConfigured(f"The master key must be {KEY_BYTES} bytes.")

    def __repr__(self) -> str:
        return f"MasterKey(key_id={self.key_id!r})"

    def wrap(self, data_key: bytes, context: bytes) -> bytes:
        return encrypt(self._key, data_key, context)

    def unwrap(self, wrapped: bytes, context: bytes) -> bytes:
        return decrypt(self._key, wrapped, context)


@sensitive_variables("encoded", "key")
def master_key() -> MasterKey:
    """Load the master key from CARTIO_MASTER_KEY_FILE or CARTIO_MASTER_KEY (base64, 32 bytes)."""
    path = os.environ.get("CARTIO_MASTER_KEY_FILE", "")
    try:
        encoded = (
            Path(path).read_text().strip() if path else os.environ.get("CARTIO_MASTER_KEY", "")
        )
    except OSError as exc:
        raise ImproperlyConfigured(
            f"Can't read the master key file {path}: {exc.strerror}."
        ) from exc
    if not encoded:
        raise ImproperlyConfigured(
            "Credentials need a master key. Set CARTIO_MASTER_KEY_FILE to a file holding a "
            "base64-encoded 32-byte key (or CARTIO_MASTER_KEY to the key itself)."
        )
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ImproperlyConfigured("The master key must be base64.") from exc
    # The id names the key without revealing it, so a rotation can find what it wrapped.
    key_id = hmac.new(key, b"cartio master key id", hashlib.sha256).hexdigest()[:16]
    return MasterKey(key_id, key)
