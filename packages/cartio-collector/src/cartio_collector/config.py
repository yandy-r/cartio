"""Where the collector keeps its identity after enrolling."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir


def default_path() -> Path:
    return Path(user_config_dir("cartio-collector", appauthor=False)) / "collector.json"


class ConfigError(RuntimeError):
    """The identity file can't be used, such as one from before key-pair enrollment."""


@dataclass(frozen=True)
class CollectorConfig:
    controller_url: str
    collector_id: str
    customer: str
    # The collector's Ed25519 private key. It is made on this host and never sent anywhere.
    private_key: str
    # Pins of the controller's TLS keys. A different key on the path means TLS inspection.
    tls_pins: list[str] = field(default_factory=list)
    # An explicit proxy URL, such as http://user:pass@proxy:3128. Empty: use HTTPS_PROXY.
    proxy: str = ""
    # A CA bundle to trust instead of the operating system's certificate store.
    ca_file: str = ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the file readable by its owner only; it holds the private key. The mode given
        # to open only applies to a new file, so tighten an existing one too.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.chmod(path, 0o600)
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> CollectorConfig:
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            return cls(**data)
        except TypeError as exc:
            raise ConfigError(
                f"{path} isn't a collector identity this version can use. Enroll again."
            ) from exc
