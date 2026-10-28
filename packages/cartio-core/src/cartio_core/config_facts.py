"""Read the facts the assessment checks need from a masked config (YAN-1665).

Each reader takes one masked config and returns what it says about management access, time,
logging and SNMP. They read text only, never a device. Secrets are already placeholders
(ADR-0003), so a community string can be counted and described but not judged here. The
collector judges it before masking and marks the placeholder of a default or weak one
(`WEAK_PLACEHOLDER`, YAN-1744).

IOS and IOS-XE share a reader. FortiOS configs are nested `config` / `edit` / `set` blocks, read
by `_forti_blocks`.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache

from .masking import WEAK_PLACEHOLDER, Platform

WEAK_COMMUNITY = "An SNMPv1/v2c community is a default or weak string."


@dataclass(frozen=True)
class ManagementAccess:
    # Each entry describes one problem, for a finding's evidence.
    telnet: tuple[str, ...] = ()
    http: tuple[str, ...] = ()
    snmp_communities: tuple[str, ...] = ()
    # Settings the config leaves to a version-dependent default.
    undetermined: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimeAndLogging:
    # None means the config doesn't say: unknown, not "none configured".
    ntp: frozenset[str] | None = frozenset()
    syslog: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Snmp:
    # One description per SNMPv1/v2c community, and per SNMPv3 user or group without auth.
    v1_v2c: tuple[str, ...] = ()
    v3_without_auth: tuple[str, ...] = ()
    v3: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.v1_v2c or self.v3_without_auth or self.v3)


@dataclass(frozen=True)
class ConfigFacts:
    management: ManagementAccess = field(default_factory=ManagementAccess)
    time_and_logging: TimeAndLogging = field(default_factory=TimeAndLogging)
    snmp: Snmp = field(default_factory=Snmp)


def read_config(platform: str, config: str) -> ConfigFacts:
    """The facts in one masked config. Raises ValueError for a platform it can't read."""
    return _read(Platform(platform), config)


@lru_cache(maxsize=256)
def _read(platform: Platform, config: str) -> ConfigFacts:
    # Cached: the NTP and syslog check reads every device's config for each device.
    config = config.replace("\r\n", "\n")
    if platform in (Platform.IOS, Platform.IOS_XE):
        return _read_ios(config)
    return _read_fortios(config)


# --- IOS and IOS-XE ---------------------------------------------------------------------------


def _ios_blocks(config: str) -> Iterator[tuple[str, list[str]]]:
    """Each top-level line with the indented lines under it."""
    header, children = "", []
    for raw in config.split("\n"):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("!"):
            continue
        if line[0] in " \t":
            children.append(line.strip())
            continue
        if header:
            yield header, children
        header, children = line, []
    if header:
        yield header, children


_COMMUNITY = re.compile(r"^snmp-server\s+community\s+(?:[08] )?(\S+)(?P<rest>.*)$", re.I)
_ACCESS = re.compile(
    r"^(?:view \S+ )?(?:(?P<mode>ro|rw)\b)?\s*(?:ipv6 \S+\s*)?(?P<acl>\S+)?$", re.I
)
_NTP = re.compile(r"^ntp server (?:vrf \S+ )?(?:ip |ipv6 )?(\S+)", re.I)
_LOGGING_HOST = re.compile(r"^logging host (?:ipv6 )?(?:vrf \S+ )?(\S+)", re.I)
_LOGGING_BARE = re.compile(r"^logging (\S+)$", re.I)
# IOS sends notifications as SNMPv1 when the host line gives no version.
_TRAP_HOST = re.compile(
    r"^snmp-server host (\S+)(?: vrf \S+)?(?: (?:traps|informs))?(?: version (1|2c|3))?", re.I
)
_V3_GROUP = re.compile(r"^snmp-server group (\S+) v3 (noauth|auth|priv)\b", re.I)
_OLD_GROUP = re.compile(r"^snmp-server group (\S+) (v1|v2c)\b", re.I)


def _read_ios(config: str) -> ConfigFacts:
    telnet, http, communities, undetermined = [], [], [], []
    ntp, syslog = set(), set()
    v1_v2c, v3_without_auth, v3 = [], [], []
    vty_seen = False
    for header, children in _ios_blocks(config):
        lower = header.lower()
        if lower.startswith("line vty"):
            vty_seen = True
            transports = [
                c.split()[2:] for c in children if c.lower().startswith("transport input")
            ]
            if not transports:
                undetermined.append(f"{header} doesn't set transport input.")
            elif any(t.lower() in ("telnet", "all") for words in transports for t in words):
                telnet.append(f"{header} accepts Telnet.")
        elif lower == "ip http server":
            http.append("The HTTP server is on (ip http server).")
        elif match := _COMMUNITY.match(header):
            access = _ACCESS.match(match["rest"].strip())
            mode = (access["mode"] or "ro").upper() if access else "RO"
            v1_v2c.append(f"SNMP community with {mode} access.")
            if mode == "RW":
                communities.append("An SNMPv1/v2c community has write (RW) access.")
            if not access or not access["acl"]:
                communities.append("An SNMPv1/v2c community has no access list.")
            if WEAK_PLACEHOLDER.fullmatch(match[1]):
                communities.append(WEAK_COMMUNITY)
        elif match := _NTP.match(header):
            ntp.add(match[1].lower())
        elif match := _LOGGING_HOST.match(header):
            syslog.add(match[1].lower())
        elif (match := _LOGGING_BARE.match(header)) and _is_address(match[1]):
            syslog.add(match[1].lower())
        elif match := _TRAP_HOST.match(header):
            if (match[2] or "1") != "3":
                v1_v2c.append(f"SNMP notifications to {match[1]} use v{match[2] or '1'}.")
                if WEAK_PLACEHOLDER.search(header):
                    communities.append(
                        f"SNMP notifications to {match[1]} use a default or weak community."
                    )
        elif match := _V3_GROUP.match(header):
            if match[2].lower() == "noauth":
                v3_without_auth.append(f"SNMPv3 group {match[1]} uses noauth.")
            else:
                v3.append(f"SNMPv3 group {match[1]} uses {match[2].lower()}.")
        elif match := _OLD_GROUP.match(header):
            v1_v2c.append(f"SNMP group {match[1]} uses {match[2].lower()}.")
    if not vty_seen:
        undetermined.append("The config has no line vty settings.")
    return ConfigFacts(
        ManagementAccess(tuple(telnet), tuple(http), tuple(communities), tuple(undetermined)),
        TimeAndLogging(frozenset(ntp), frozenset(syslog)),
        Snmp(tuple(v1_v2c), tuple(v3_without_auth), tuple(v3)),
    )


def _is_address(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


# --- FortiOS ----------------------------------------------------------------------------------


@dataclass
class _Block:
    # The `config` names from the outermost in, and the `edit` names along the way.
    path: tuple[str, ...]
    edits: tuple[str, ...]
    settings: dict[str, list[str]] = field(default_factory=dict)


def _split(line: str) -> list[str]:
    try:
        return shlex.split(line)
    except ValueError:
        return line.split()


def _forti_blocks(config: str) -> list[_Block]:
    blocks: dict[tuple, _Block] = {}
    path: list[str] = []
    edits: list[str] = []
    # What each open `config` or `edit` was, so `end` and `next` close the right one.
    stack: list[str] = []
    for raw in config.split("\n"):
        words = _split(raw.strip())
        if not words:
            continue
        keyword = words[0]
        if keyword == "config":
            path.append(" ".join(words[1:]))
            stack.append("config")
        elif keyword == "edit":
            edits.append(" ".join(words[1:]))
            stack.append("edit")
        elif keyword in ("next", "end"):
            # Close up to and including the nearest block of that kind.
            want = "edit" if keyword == "next" else "config"
            while stack:
                kind = stack.pop()
                (edits if kind == "edit" else path).pop()
                if kind == want:
                    break
        elif keyword in ("set", "unset") and path:
            # Multi-VDOM configs wrap sections in `config global` and `config vdom` / `edit
            # <vdom>`. The sections read the same either way.
            key = (tuple(p for p in path if p not in ("global", "vdom")), tuple(edits))
            block = blocks.setdefault(key, _Block(*key))
            if keyword == "set" and len(words) > 1:
                block.settings[words[1]] = words[2:]
    return list(blocks.values())


def _blocks_at(blocks: list[_Block], *path: str) -> list[_Block]:
    return [b for b in blocks if b.path == path]


def _setting(block: _Block, key: str, default: str = "") -> str:
    return " ".join(block.settings.get(key, [default])).lower()


def _read_fortios(config: str) -> ConfigFacts:
    blocks = _forti_blocks(config)
    telnet, http, communities, undetermined = [], [], [], []
    interfaces = _blocks_at(blocks, "system interface")
    if not interfaces:
        undetermined.append("The config has no interface settings.")
    for block in _blocks_at(blocks, "system global"):
        if _setting(block, "admin-telnet") == "enable":
            telnet.append("Telnet administration is on (admin-telnet).")
    for block in interfaces:
        name = block.edits[-1] if block.edits else "An interface"
        access = _setting(block, "allowaccess").split()
        if "telnet" in access:
            telnet.append(f"{name} allows Telnet admin access.")
        if "http" in access:
            http.append(f"{name} allows HTTP admin access.")

    ntp: frozenset[str] | None
    settings = _blocks_at(blocks, "system ntp")
    if not settings:
        ntp = None
    elif _setting(settings[0], "ntpsync", "enable") != "enable":
        ntp = frozenset()
    elif _setting(settings[0], "type", "fortiguard") == "fortiguard":
        ntp = frozenset({"fortiguard"})
    else:
        ntp = frozenset(
            _setting(b, "server") for b in _blocks_at(blocks, "system ntp", "ntpserver")
        ) - {""}

    syslog = set()
    for block in blocks:
        if len(block.path) != 1 or _setting(block, "status") != "enable":
            continue
        if re.fullmatch(r"log syslogd\d? setting", block.path[0]):
            syslog.add(_setting(block, "server"))
        elif re.fullmatch(r"log fortianalyzer\d?(?:-cloud)? setting", block.path[0]):
            syslog.add(f"fortianalyzer {_setting(block, 'server')}".strip())
    syslog.discard("")

    v1_v2c, v3_without_auth, v3 = [], [], []
    for block in _blocks_at(blocks, "system snmp community"):
        if _setting(block, "status", "enable") == "enable":
            v1_v2c.append(f"SNMP community {block.edits[-1] if block.edits else ''}".strip() + ".")
            if WEAK_PLACEHOLDER.fullmatch(" ".join(block.settings.get("name", []))):
                communities.append(WEAK_COMMUNITY)
    for block in _blocks_at(blocks, "system snmp user"):
        name = block.edits[-1] if block.edits else "An SNMPv3 user"
        if _setting(block, "status", "enable") != "enable":
            continue
        level = _setting(block, "security-level", "no-auth-no-priv")
        if level == "no-auth-no-priv":
            v3_without_auth.append(f"SNMPv3 user {name} uses no-auth-no-priv.")
        else:
            v3.append(f"SNMPv3 user {name} uses {level}.")
    return ConfigFacts(
        ManagementAccess(tuple(telnet), tuple(http), tuple(communities), tuple(undetermined)),
        TimeAndLogging(ntp, frozenset(syslog)),
        Snmp(tuple(v1_v2c), tuple(v3_without_auth), tuple(v3)),
    )
