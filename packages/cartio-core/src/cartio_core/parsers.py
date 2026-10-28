"""Turn read-only command output into the normalized device model (ADR-0008).

Parsing reuses ntc-templates (TextFSM) wherever it has a template, and TTP for the rest. It
runs on masked output, on the collector or the controller, and never talks to a device, so the
same `RawOutput`s always parse to the same records.

`parse()` takes one device's outputs, keyed by the allow-listed command, and returns plain
records that the controller stores as a Device, its Interfaces and its Links. A command that's
missing or doesn't parse leaves its fields empty rather than guessed.

Adding a platform: list the commands its parsers read in `COMMANDS`, add a `_parse_<platform>`
function, and add sanitized fixtures under tests/fixtures/sanitized/<platform>/.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ntc_templates.parse import parse_output
from ttp import ttp

from .masking import Platform


class ParseError(ValueError):
    """Output that can't be turned into a device, such as one without a host name."""


@dataclass(frozen=True)
class ParsedInterface:
    name: str
    description: str = ""
    # None when the output doesn't say.
    enabled: bool | None = None
    # Lower case and colon separated: 00:00:5e:00:53:01.
    mac_address: str = ""
    mtu: int | None = None
    # In kbit/s, like Interface.speed.
    speed: int | None = None


@dataclass(frozen=True)
class ParsedNeighbor:
    """A neighbor a device announces on one of its interfaces, by CDP or LLDP."""

    local_interface: str
    device_name: str
    interface_name: str
    protocol: str


@dataclass(frozen=True)
class ParsedDevice:
    name: str
    platform: Platform
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    os_version: str = ""
    interfaces: tuple[ParsedInterface, ...] = field(default_factory=tuple)
    neighbors: tuple[ParsedNeighbor, ...] = field(default_factory=tuple)


_IOS_COMMANDS = (
    "show version",
    "show inventory",
    "show interfaces",
    "show cdp neighbors detail",
    "show lldp neighbors detail",
)

# The commands each platform's parser reads. Every one is on the platform's allow-list.
COMMANDS: Mapping[Platform, tuple[str, ...]] = {
    Platform.IOS: _IOS_COMMANDS,
    Platform.IOS_XE: _IOS_COMMANDS,
    Platform.FORTIOS: (
        "get system status",
        "get hardware status",
        "get system interface",
    ),
}


def parse(platform: Platform | str, outputs: Mapping[str, str]) -> ParsedDevice:
    """Parse one device's outputs, keyed by command. Raises `ParseError` without a host name."""
    platform = Platform(platform)
    outputs = {" ".join(command.split()): text for command, text in outputs.items()}
    if platform is Platform.FORTIOS:
        return _parse_fortios(outputs)
    return _parse_ios(platform, outputs)


def _ntc(platform: str, command: str, outputs: Mapping[str, str]) -> list[dict]:
    text = outputs.get(command, "")
    if not text.strip():
        return []
    return parse_output(platform=platform, command=command, data=text)


def _parse_ios(platform: Platform, outputs: Mapping[str, str]) -> ParsedDevice:
    version = next(iter(_ntc("cisco_ios", "show version", outputs)), {})
    name = version.get("hostname", "")
    if not name:
        raise ParseError(f"No host name in the {platform} output: 'show version' is needed.")
    model = next(iter(version.get("hardware") or []), "")
    serial = next(iter(version.get("serial") or []), "")
    # The first inventory entry is the chassis (or the stack): more reliable than show version.
    chassis = next(iter(_ntc("cisco_ios", "show inventory", outputs)), {})
    model = chassis.get("pid", "").strip() or model
    serial = chassis.get("sn", "").strip() or serial

    interfaces = tuple(
        ParsedInterface(
            name=row["interface"],
            description=row.get("description", ""),
            enabled="administratively down" not in row.get("link_status", ""),
            mac_address=_mac(row.get("mac_address", "")),
            mtu=_int(row.get("mtu")),
            speed=_kbit(row.get("bandwidth", "")),
        )
        for row in _ntc("cisco_ios", "show interfaces", outputs)
    )
    known = [interface.name for interface in interfaces]

    neighbors = [
        ParsedNeighbor(
            local_interface=expand_interface(row["local_interface"], known),
            device_name=_host(row["neighbor_name"]),
            interface_name=row.get("neighbor_interface", ""),
            protocol="cdp",
        )
        for row in _ntc("cisco_ios", "show cdp neighbors detail", outputs)
        if row.get("local_interface")
    ]
    neighbors += [
        ParsedNeighbor(
            local_interface=expand_interface(row["local_interface"], known),
            device_name=_host(row.get("neighbor_name") or row.get("chassis_id", "")),
            # The port ID: the "neighbor interface" column is the port's description.
            interface_name=row.get("neighbor_port_id", ""),
            protocol="lldp",
        )
        for row in _ntc("cisco_ios", "show lldp neighbors detail", outputs)
        if row.get("local_interface")
    ]
    return ParsedDevice(
        name=name,
        platform=platform,
        manufacturer="Cisco",
        model=model,
        serial=serial,
        os_version=version.get("version", ""),
        interfaces=interfaces,
        neighbors=tuple(neighbors),
    )


# `get hardware status` has no ntc-template.
_FORTIOS_HARDWARE = """
<group name="hardware">
Model name: {{ model | ORPHRASE }}
</group>
"""


def _parse_fortios(outputs: Mapping[str, str]) -> ParsedDevice:
    status = next(iter(_ntc("fortinet", "get system status", outputs)), {})
    name = status.get("hostname", "")
    if not name:
        raise ParseError("No host name in the fortios output: 'get system status' is needed.")
    # "FortiGate-60F v7.2.8,build1639,240313 (GA.M)"
    version = re.match(r"(?P<model>\S+) v(?P<os>\S+?),", status.get("version", ""))
    model = version["model"] if version else ""
    hardware = _ttp(_FORTIOS_HARDWARE, outputs.get("get hardware status", ""))
    model = hardware.get("hardware", {}).get("model", "") or model

    interfaces = tuple(
        ParsedInterface(name=row["name"], enabled=_fortios_status(row.get("status", "")))
        for row in _ntc("fortinet", "get system interface", outputs)
    )
    return ParsedDevice(
        name=name,
        platform=Platform.FORTIOS,
        manufacturer="Fortinet",
        model=model,
        serial=status.get("serial_number", ""),
        os_version=version["os"] if version else "",
        interfaces=interfaces,
    )


def _ttp(template: str, text: str) -> dict:
    if not text.strip():
        return {}
    parser = ttp(data=text, template=template)
    parser.parse()
    # One input, one template: [[{...}]].
    result = parser.result()[0][0]
    return result if isinstance(result, dict) else {}


def _fortios_status(status: str) -> bool | None:
    return {"up": True, "down": False}.get(status.lower())


# Cisco's short interface names, longest first so "Twe" wins over "Tw" and "Te".
_IOS_SHORT = sorted(
    {
        "Gi": "GigabitEthernet",
        "Fa": "FastEthernet",
        "Te": "TenGigabitEthernet",
        "Tw": "TwoGigabitEthernet",
        "Twe": "TwentyFiveGigE",
        "Fi": "FiveGigabitEthernet",
        "Fo": "FortyGigabitEthernet",
        "Hu": "HundredGigE",
        "Et": "Ethernet",
        "Po": "Port-channel",
        "Vl": "Vlan",
        "Lo": "Loopback",
        "Tu": "Tunnel",
        "Mg": "Management",
    }.items(),
    key=lambda item: -len(item[0]),
)
_NAME = re.compile(r"(?P<kind>[A-Za-z-]+)(?P<number>\d.*)")


def expand_interface(name: str, known: Sequence[str] = ()) -> str:
    """The full name of an interface given short, as LLDP does (Gi1/0/1 for GigabitEthernet1/0/1).

    A name in `known` wins; otherwise Cisco's usual abbreviations are expanded.
    """
    name = name.strip()
    if name in known:
        return name
    parts = _NAME.fullmatch(name)
    if not parts:
        return name
    kind, number = parts["kind"].lower(), parts["number"]
    for candidate in known:
        other = _NAME.fullmatch(candidate)
        if other and other["number"] == number and other["kind"].lower().startswith(kind):
            return candidate
    for short, full in _IOS_SHORT:
        if kind == short.lower():
            return full + number
    return name


_IP = re.compile(r"\d+\.\d+\.\d+\.\d+|[0-9A-Fa-f:]*:[0-9A-Fa-f:]*")


def _host(name: str) -> str:
    """A neighbor's host name without its domain, so it matches the name it reports itself."""
    name = name.strip()
    if _IP.fullmatch(name):
        return name
    return name.split(".", 1)[0]


def _mac(value: str) -> str:
    digits = re.sub(r"[^0-9A-Fa-f]", "", value).lower()
    if len(digits) != 12:
        return ""
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _kbit(bandwidth: str) -> int | None:
    # "1000000 Kbit"
    match = re.fullmatch(r"(\d+)\s*Kbit", bandwidth.strip())
    return int(match[1]) if match else None
