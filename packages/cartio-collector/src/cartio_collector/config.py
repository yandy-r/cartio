"""Where the collector keeps its identity after enrolling."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_config_dir


def default_path() -> Path:
    return Path(user_config_dir("cartio-collector", appauthor=False)) / "collector.json"


@dataclass(frozen=True)
class CollectorConfig:
    controller_url: str
    collector_id: str
    secret: str
    customer: str

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the file readable by its owner only; it holds the collector secret. The mode
        # given to open only applies to a new file, so tighten an existing one too.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.chmod(path, 0o600)
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> CollectorConfig:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**data)
