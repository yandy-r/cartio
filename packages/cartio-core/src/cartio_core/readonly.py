"""What a driver may send to a customer device: read operations only (ADR-0004).

Every driver goes through `ReadOnlyDriver.run()`, which checks the whole command against the
platform's allow-list before anything is sent:

- The command must match an allow-listed read command exactly, word for word, with arguments
  only where the entry has a placeholder. A prefix such as `show` is never enough, because a
  write can hide in an argument or modifier.
- Pipes, redirection, chained commands and control characters are refused outright, even after
  an allowed command. The collector filters output itself.
- Abbreviations (`sh run`, `conf t`) are refused: entries are spelled out in full.

SNMP and HTTP have the same rule in `check_snmp()` and `check_http()`: gets and walks, GET and
HEAD, nothing else. Errors name the platform and why, and never echo more of the command than
the engineer typed.

Adding a platform: add it to `masking.Platform` and its masking rules, then its read commands to
`ALLOWED`, with a refusal test for each config-mode and write command the platform has. See
docs/drivers/adding-a-platform.md.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping

from .masking import Platform


class CommandRefused(ValueError):
    """A command, SNMP operation or HTTP method that isn't an allow-listed read."""


# What a placeholder in an allow-list entry may stand for. One word, no shell or CLI syntax.
PLACEHOLDERS: Mapping[str, re.Pattern[str]] = {
    # GigabitEthernet1/0/1, Port-channel10, Vlan20, port1, wan1, internal1.
    "{interface}": re.compile(r"[A-Za-z][A-Za-z0-9/.:_-]{0,63}"),
}

_IOS = (
    "show version",
    "show inventory",
    "show running-config",
    # Below privilege 15 the plain form shows only what the level could configure.
    "show running-config view full",
    "show startup-config",
    "show interfaces",
    "show interfaces {interface}",
    "show interfaces status",
    "show interfaces description",
    "show ip interface brief",
    "show ipv6 interface brief",
    "show cdp neighbors detail",
    "show lldp neighbors detail",
    "show vlan brief",
    "show ip route",
    "show mac address-table",
    "show etherchannel summary",
    "show spanning-tree",
)

_FORTIOS = (
    "get system status",
    "get hardware status",
    "get system interface",
    "get system interface physical",
    "get system arp",
    "get system ha status",
    "get router info routing-table all",
    "show full-configuration",
    "show system interface",
    "show system interface {interface}",
)

# The read commands of each platform, word for word.
ALLOWED: Mapping[Platform, tuple[str, ...]] = {
    Platform.IOS: _IOS,
    Platform.IOS_XE: _IOS,
    Platform.FORTIOS: _FORTIOS,
}

# Anything that pipes, redirects, chains or escapes, on any platform.
_SYNTAX = re.compile(r"[|><;&`$\\!#]|[\x00-\x1f\x7f]")


def check_command(platform: Platform | str, command: str) -> str:
    """Return `command` normalized for sending, or raise `CommandRefused`."""
    platform = _platform(platform)
    if _SYNTAX.search(command):
        raise CommandRefused(
            f"Refused on {platform}: pipes, redirection, chained commands and control "
            "characters aren't allowed."
        )
    words = command.split()
    if not words:
        raise CommandRefused(f"Refused on {platform}: the command is empty.")
    for entry in ALLOWED[platform]:
        sent = _match(entry.split(), words)
        if sent is not None:
            return " ".join(sent)
    raise CommandRefused(
        f"Refused on {platform}: {' '.join(words)!r} isn't an allow-listed read command."
    )


def _match(entry: list[str], words: list[str]) -> list[str] | None:
    """The words to send if `words` match `entry`: keywords as listed, arguments as typed."""
    if len(entry) != len(words):
        return None
    sent = []
    for expected, word in zip(entry, words, strict=True):
        pattern = PLACEHOLDERS.get(expected)
        if pattern is not None:
            if not pattern.fullmatch(word):
                return None
            sent.append(word)
        # CLI keywords aren't case-sensitive; arguments keep their case.
        elif expected == word.lower():
            sent.append(expected)
        else:
            return None
    return sent


def _platform(platform: Platform | str) -> Platform:
    try:
        return Platform(platform)
    except ValueError:
        raise CommandRefused(f"Refused: {platform!r} isn't a supported platform.") from None


SNMP_READS = frozenset({"get", "getnext", "getbulk", "walk"})
HTTP_READS = frozenset({"GET", "HEAD"})


def check_snmp(operation: str) -> str:
    """Return the SNMP operation if it's a read (get, getnext, getbulk, walk)."""
    if operation.lower() not in SNMP_READS:
        raise CommandRefused(f"Refused: SNMP {operation!r} isn't a read. Only gets and walks are.")
    return operation.lower()


def check_http(method: str) -> str:
    """Return the HTTP method if it's a read (GET or HEAD)."""
    if method.upper() not in HTTP_READS:
        raise CommandRefused(f"Refused: HTTP {method!r} isn't a read. Only GET and HEAD are.")
    return method.upper()


class ReadOnlyDriver(ABC):
    """Base for every SSH driver: `run()` checks the allow-list, `_send()` talks to the device.

    A transport (scrapli, Netmiko) is wrapped in a subclass that implements `_send()` only.
    `run()` is the one way in: a subclass that replaces it, directly or through a mixin, is
    refused when it's defined. Nothing outside the driver calls `_send()`. No write operation
    (config mode, commit, file copy) exists on this class or may be added.
    """

    def __init__(self, platform: Platform | str) -> None:
        self.platform = _platform(platform)

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.run is not ReadOnlyDriver.run:
            raise TypeError(f"{cls.__name__} can't override run(): it enforces the allow-list.")

    def run(self, command: str) -> str:
        return self._send(check_command(self.platform, command))

    @abstractmethod
    def _send(self, command: str) -> str:
        """Send one allow-listed command and return its raw output."""
