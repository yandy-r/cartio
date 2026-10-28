"""End-of-life data for the eol-eos check, from `lifecycle.toml` next to this file (YAN-1665).

The data is a versioned file in the repo, so a report can say which version it used and an
update is a reviewed change. See the file's header for how to update it.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path

DATA_FILE = Path(__file__).with_name("lifecycle.toml")


@dataclass(frozen=True)
class Lifecycle:
    """The milestones of one model family or software train."""

    name: str
    source: str
    checked: date
    end_of_sale: date | None = None
    end_of_security_fixes: date | None = None
    last_date_of_support: date | None = None


@dataclass(frozen=True)
class LifecycleData:
    version: str
    hardware: tuple[tuple[tuple[str, ...], Lifecycle], ...]
    software: tuple[tuple[str, tuple[str, ...], Lifecycle], ...]

    def for_model(self, model: str) -> Lifecycle | None:
        model = model.upper()
        for patterns, lifecycle in self.hardware:
            if any(fnmatchcase(model, pattern.upper()) for pattern in patterns):
                return lifecycle
        return None

    def for_software(self, platform: str, os_version: str) -> Lifecycle | None:
        train = release_train(os_version)
        for entry_platform, trains, lifecycle in self.software:
            if entry_platform == platform and train in trains:
                return lifecycle
        return None


_CLASSIC = re.compile(r"^(\d+\.\d+\(\d+\)[A-Z]+)")
_DOTTED = re.compile(r"^v?(\d+)\.(\d+)")


def release_train(os_version: str) -> str:
    """'17.9' for 17.09.04a, '7.2' for 7.2.8, '15.2(7)E' for 15.2(7)E8. Blank if unrecognised."""
    version = os_version.strip()
    if match := _CLASSIC.match(version):
        return match[1]
    if match := _DOTTED.match(version):
        return f"{int(match[1])}.{int(match[2])}"
    return ""


def _lifecycle(entry: dict, name: str) -> Lifecycle:
    return Lifecycle(
        name=name,
        source=entry["source"],
        checked=entry["checked"],
        end_of_sale=entry.get("end_of_sale"),
        end_of_security_fixes=entry.get("end_of_security_fixes"),
        last_date_of_support=entry.get("last_date_of_support"),
    )


def parse(text: str) -> LifecycleData:
    data = tomllib.loads(text)
    hardware = tuple(
        (tuple(e["models"]), _lifecycle(e, f"{e['manufacturer']} {' / '.join(e['models'])}"))
        for e in data.get("hardware", [])
    )
    software = tuple(
        (e["platform"], tuple(e["trains"]), _lifecycle(e, f"{e['platform']} {e['trains'][0]}"))
        for e in data.get("software", [])
    )
    return LifecycleData(str(data["version"]), hardware, software)


@cache
def load() -> LifecycleData:
    return parse(DATA_FILE.read_text(encoding="utf-8"))
