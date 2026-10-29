"""What an On site collector keeps for itself: device credentials, targets and the masking key.

For an On site customer (ADR-0002, ADR-0003) none of this reaches the controller. An engineer
enters credentials and targets on the collector, and they are kept in one file encrypted with
AES-GCM under a key made on this host, which never leaves it. The masking key is made here too,
so configs are masked with a key held where the credentials are.

A secret can be replaced or removed, never read back: listings show names and kinds only. Every
change, and every use by a job, is appended to a local audit log with who and when.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cartio_core.masking import MaskingKey
from cartio_core.protocol import COLLECT_PLATFORMS
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# The kinds of credential an On site collector can log in with, and their secret fields:
# (required, optional). The same as a Hosted credential's SSH kinds on the controller.
SECRET_FIELDS = {
    "ssh_password": (("password",), ()),
    "ssh_key": (("private_key",), ("passphrase",)),
}
# The version in every placeholder this collector makes. A Hosted key's is "m1".
MASKING_KEY_VERSION = "site1"
_KEY_BYTES = 32
_NONCE_BYTES = 12
_CONTEXT = b"cartio on-site secrets v1"


class SiteError(ValueError):
    """The on-site secrets can't be used or changed as asked. The message is for people."""


@dataclass(frozen=True)
class SitePaths:
    """Where the on-site secrets of the collector identity at `config` are kept."""

    config: Path

    @property
    def secrets(self) -> Path:
        return self.config.with_name(self.config.stem + ".site")

    @property
    def key(self) -> Path:
        return self.config.with_name(self.config.stem + ".site-key")

    @property
    def audit(self) -> Path:
        return self.config.with_name(self.config.stem + ".site-audit.log")


def _write_private(path: Path, data: bytes) -> None:
    """Write `data` readable by its owner only, whole: a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Site:
    """The on-site secrets, read and written whole."""

    def __init__(self, paths: SitePaths) -> None:
        self.paths = paths

    @classmethod
    def for_config(cls, config: Path) -> Site:
        return cls(SitePaths(config))

    def _key(self, create: bool) -> bytes:
        try:
            key = base64.b64decode(self.paths.key.read_text(encoding="ascii"))
        except FileNotFoundError:
            if not create:
                raise SiteError(
                    "This collector has no on-site secrets yet. Add a credential first."
                ) from None
            key = AESGCM.generate_key(bit_length=8 * _KEY_BYTES)
            _write_private(self.paths.key, base64.b64encode(key))
        if len(key) != _KEY_BYTES:
            raise SiteError(f"{self.paths.key} isn't an on-site secrets key.")
        return key

    def load(self) -> dict[str, Any]:
        if not self.paths.secrets.exists():
            return {"credentials": {}, "targets": [], "masking_key": None}
        raw = self.paths.secrets.read_bytes()
        try:
            data = AESGCM(self._key(create=False)).decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _CONTEXT
            )
        except InvalidTag as exc:
            raise SiteError(
                f"{self.paths.secrets} can't be opened with {self.paths.key}. Was the key replaced?"
            ) from exc
        return json.loads(data)

    def _save(self, data: dict[str, Any]) -> None:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = AESGCM(self._key(create=True)).encrypt(nonce, json.dumps(data).encode(), _CONTEXT)
        _write_private(self.paths.secrets, nonce + sealed)

    def audit(self, action: str, **detail: Any) -> None:
        """Append one event. Names and kinds only, never a secret."""
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001 (no login name, such as in a container)
            user = str(os.getuid()) if hasattr(os, "getuid") else ""
        event = {"at": datetime.now(UTC).isoformat(), "action": action, "user": user, **detail}
        self.paths.audit.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.paths.audit, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    # Credentials

    def set_credential(self, name: str, kind: str, username: str, secret: dict[str, str]) -> bool:
        """Add a credential, or replace one with this name. Returns True if it replaced one."""
        if not name or name != name.strip() or len(name) > 200:
            raise SiteError("A credential needs a name of up to 200 characters.")
        if kind not in SECRET_FIELDS:
            raise SiteError(f"The kind must be one of {', '.join(SECRET_FIELDS)}.")
        if not username:
            raise SiteError("An SSH credential needs a user name.")
        required, optional = SECRET_FIELDS[kind]
        missing = [field for field in required if not secret.get(field)]
        unknown = set(secret) - set(required) - set(optional)
        if missing or unknown:
            raise SiteError(f"Secret fields: missing {missing}, unknown {sorted(unknown)}.")
        data = self.load()
        replaced = name in data["credentials"]
        data["credentials"][name] = {
            "kind": kind,
            "username": username,
            **{k: v for k, v in secret.items() if v},
        }
        self._save(data)
        self.audit("credential.updated" if replaced else "credential.created", name=name, kind=kind)
        return replaced

    def remove_credential(self, name: str) -> None:
        data = self.load()
        if name not in data["credentials"]:
            raise SiteError(f"There's no credential named {name!r}.")
        if used := [t["host"] for t in data["targets"] if t["credential"] == name]:
            raise SiteError(f"Targets still use {name!r}: {', '.join(used)}. Remove them first.")
        kind = data["credentials"].pop(name)["kind"]
        self._save(data)
        self.audit("credential.deleted", name=name, kind=kind)

    def credential_list(self) -> list[dict[str, str]]:
        """Names, kinds and user names: never a secret."""
        return [
            {"name": name, "kind": c["kind"], "username": c["username"]}
            for name, c in sorted(self.load()["credentials"].items())
        ]

    # Targets

    def add_target(self, host: str, platform: str, credential: str, port: int = 22) -> None:
        if not host or host != host.strip() or len(host) > 253:
            raise SiteError("A target needs a host name or address.")
        # A leading "-" would reach the ssh command line as an option.
        if host.startswith("-") or any(c.isspace() or c in "@/;" for c in host):
            raise SiteError(f"{host!r} isn't a host name or address.")
        if platform not in COLLECT_PLATFORMS:
            raise SiteError(f"The platform must be one of {', '.join(COLLECT_PLATFORMS)}.")
        if not 0 < port < 65536:
            raise SiteError("A port is 1 to 65535.")
        data = self.load()
        if credential not in data["credentials"]:
            raise SiteError(f"There's no credential named {credential!r}.")
        data["targets"] = [t for t in data["targets"] if t["host"] != host]
        data["targets"].append(
            {"host": host, "platform": platform, "port": port, "credential": credential}
        )
        self._save(data)
        self.audit("target.set", host=host, platform=platform, credential=credential)

    def remove_target(self, host: str) -> None:
        data = self.load()
        kept = [t for t in data["targets"] if t["host"] != host]
        if len(kept) == len(data["targets"]):
            raise SiteError(f"There's no target {host!r}.")
        data["targets"] = kept
        self._save(data)
        self.audit("target.deleted", host=host)

    def targets(self) -> list[dict[str, Any]]:
        return list(self.load()["targets"])

    # Collecting

    def for_collect(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], MaskingKey]:
        """The targets, their credentials and the masking key, made on first use."""
        data = self.load()
        if data["masking_key"] is None:
            data["masking_key"] = {
                "version": MASKING_KEY_VERSION,
                "secret": base64.b64encode(secrets.token_bytes(_KEY_BYTES)).decode(),
            }
            self._save(data)
            self.audit("masking_key.created", version=MASKING_KEY_VERSION)
        stored = data["masking_key"]
        key = MaskingKey(stored["version"], base64.b64decode(stored["secret"]))
        return data["targets"], data["credentials"], key
