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
def _load_key(file_variable: str, variable: str) -> MasterKey | None:
    path = os.environ.get(file_variable, "")
    try:
        encoded = Path(path).read_text().strip() if path else os.environ.get(variable, "")
    except OSError as exc:
        raise ImproperlyConfigured(
            f"Can't read the master key file {path}: {exc.strerror}."
        ) from exc
    if not encoded:
        return None
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"The key in {file_variable} or {variable} must be base64."
        ) from exc
    # The id names the key without revealing it, so a rotation can find what it wrapped.
    key_id = hmac.new(key, b"cartio master key id", hashlib.sha256).hexdigest()[:16]
    return MasterKey(key_id, key)


def master_key() -> MasterKey:
    """Load the master key from CARTIO_MASTER_KEY_FILE or CARTIO_MASTER_KEY (base64, 32 bytes).

    New secrets are always wrapped with this key.
    """
    key = _load_key("CARTIO_MASTER_KEY_FILE", "CARTIO_MASTER_KEY")
    if key is None:
        raise ImproperlyConfigured(
            "Credentials need a master key. Set CARTIO_MASTER_KEY_FILE to a file holding a "
            "base64-encoded 32-byte key (or CARTIO_MASTER_KEY to the key itself)."
        )
    return key


def previous_master_key() -> MasterKey | None:
    """The key being rotated away from, set only during a rotation (YAN-1722).

    Read from CARTIO_PREVIOUS_MASTER_KEY_FILE or CARTIO_PREVIOUS_MASTER_KEY. It's used only to
    open secrets it wrapped, never to wrap new ones.
    """
    return _load_key("CARTIO_PREVIOUS_MASTER_KEY_FILE", "CARTIO_PREVIOUS_MASTER_KEY")


def master_key_for(key_id: str) -> MasterKey:
    """The configured master key with this id: the current one, or the previous one mid-rotation."""
    current = master_key()
    if key_id == current.key_id:
        return current
    previous = previous_master_key()
    if previous is not None and key_id == previous.key_id:
        return previous
    raise ImproperlyConfigured(
        f"This secret was wrapped by another master key ({key_id}). Restore that key file, or "
        "set it as CARTIO_PREVIOUS_MASTER_KEY_FILE and run rotate_master_key."
    )
