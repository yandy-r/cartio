"""Time-based one-time passwords (RFC 6238), the second factor for engineer sign-in.

Standard 6-digit, 30-second, HMAC-SHA1 codes, so any authenticator app works.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from django.views.decorators.debug import sensitive_variables

STEP_SECONDS = 30
DIGITS = 6
# Accept the code one step either side of now, for clock drift and slow typing.
DRIFT_STEPS = 1
SECRET_BYTES = 20


def new_secret() -> bytes:
    return secrets.token_bytes(SECRET_BYTES)


def to_base32(secret: bytes) -> str:
    """The key as an authenticator app takes it when typed in."""
    return base64.b32encode(secret).decode().rstrip("=")


def step_at(when: float | None = None) -> int:
    return int((time.time() if when is None else when) // STEP_SECONDS)


@sensitive_variables("secret")
def code_at(secret: bytes, step: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


@sensitive_variables("secret", "code")
def matching_step(secret: bytes, code: str, when: float | None = None) -> int | None:
    """The time step `code` is valid for, or None. Callers refuse a step already used."""
    code = "".join(code.split())
    if len(code) != DIGITS or not (code.isascii() and code.isdigit()):
        return None
    now = step_at(when)
    match = None
    # Check every candidate, so the time taken doesn't say which step matched.
    for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            match = step
    return match


@sensitive_variables("secret")
def provisioning_uri(secret: bytes, account: str, issuer: str = "Cartio") -> str:
    """The otpauth:// link authenticator apps read to add the account."""
    label = quote(f"{issuer}:{account}")
    query = urlencode({"secret": to_base32(secret), "issuer": issuer, "digits": DIGITS})
    return f"otpauth://totp/{label}?{query}"
