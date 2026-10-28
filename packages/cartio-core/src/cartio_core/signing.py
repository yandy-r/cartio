"""Signed collector requests (ADR-0009).

At enrollment the collector makes an Ed25519 key pair and sends only the public key. After that
it signs every request: the signature covers the collector id, method, path, a hash of the body,
a timestamp and a nonce. The controller refuses a signature outside the clock window, a nonce it
has already seen, and any collector that is unknown or revoked. There is no mTLS, so this works
through TLS-inspecting proxies.

Keys travel as unpadded base64url of their 32 raw bytes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

SCHEME = "Cartio-Ed25519"
# How far a request's timestamp may be from the controller's clock, either way. Collectors run
# on customer VMs and laptops whose clocks drift, so this is minutes, not seconds.
CLOCK_WINDOW = 300
_NONCE_BYTES = 16


class SignatureError(ValueError):
    """A request's signature is missing, malformed, stale or wrong."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise SignatureError("not base64url") from exc


def new_private_key() -> str:
    """A new Ed25519 private key, for the collector to keep and never send."""
    return _b64encode(Ed25519PrivateKey.generate().private_bytes_raw())


def _private_key(text: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_b64decode(text))


def public_key_for(private_key: str) -> str:
    return _b64encode(_private_key(private_key).public_key().public_bytes_raw())


def check_public_key(text: str) -> str:
    """Return `text` if it is an Ed25519 public key, or raise SignatureError."""
    raw = _b64decode(text)
    if len(raw) != 32:
        raise SignatureError("an Ed25519 public key is 32 bytes")
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise SignatureError("not an Ed25519 public key") from exc
    return _b64encode(raw)


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _message(collector_id: str, method: str, path: str, body: bytes, ts: int, nonce: str) -> bytes:
    return "\n".join(
        ["cartio-v1", collector_id, method.upper(), path, body_hash(body), str(ts), nonce]
    ).encode()


@dataclass(frozen=True)
class Signed:
    """The parts of an Authorization header carrying a signed request."""

    collector_id: str
    ts: int
    nonce: str
    signature: str

    def header(self) -> str:
        return (
            f"{SCHEME} id={self.collector_id},ts={self.ts},nonce={self.nonce},sig={self.signature}"
        )


def sign(
    private_key: str,
    collector_id: str,
    method: str,
    path: str,
    body: bytes,
    *,
    now: float | None = None,
    nonce: str | None = None,
) -> str:
    """The Authorization header value for one request. `path` includes any query string."""
    ts = int(time.time() if now is None else now)
    nonce = nonce or secrets.token_hex(_NONCE_BYTES)
    signature = _private_key(private_key).sign(
        _message(collector_id, method, path, body, ts, nonce)
    )
    return Signed(collector_id, ts, nonce, _b64encode(signature)).header()


def parse(header: str) -> Signed:
    """Read an Authorization header. Raises SignatureError if it isn't a signed request."""
    scheme, _, rest = header.partition(" ")
    if scheme != SCHEME:
        raise SignatureError(f"expected a {SCHEME} signature")
    fields = dict(part.partition("=")[::2] for part in rest.split(","))
    try:
        signed = Signed(
            collector_id=fields["id"],
            ts=int(fields["ts"]),
            nonce=fields["nonce"],
            signature=fields["sig"],
        )
    except (KeyError, ValueError) as exc:
        raise SignatureError("the signature needs id, ts, nonce and sig") from exc
    if not signed.collector_id or not (8 <= len(signed.nonce) <= 64) or not signed.nonce.isalnum():
        raise SignatureError("bad collector id or nonce")
    return signed


def verify(
    public_key: str,
    signed: Signed,
    method: str,
    path: str,
    body: bytes,
    *,
    now: float | None = None,
) -> None:
    """Raise SignatureError unless `signed` is this key's signature of this request, made now.

    Checking the nonce hasn't been seen before is the caller's job: it needs storage.
    """
    now = time.time() if now is None else now
    if abs(now - signed.ts) > CLOCK_WINDOW:
        raise SignatureError("the request's timestamp is outside the clock window")
    key = Ed25519PublicKey.from_public_bytes(_b64decode(public_key))
    message = _message(signed.collector_id, method, path, body, signed.ts, signed.nonce)
    try:
        key.verify(_b64decode(signed.signature), message)
    except InvalidSignature as exc:
        raise SignatureError("the signature doesn't match the request") from exc


def spki_pin(der_certificate: bytes) -> str:
    """The pin of a TLS certificate's public key: base64 SHA-256 of its SubjectPublicKeyInfo."""
    from cryptography import x509

    spki = (
        x509.load_der_x509_certificate(der_certificate)
        .public_key()
        .public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")
