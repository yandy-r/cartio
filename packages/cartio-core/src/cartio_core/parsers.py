"""Turn read-only command output into the normalized device model (ADR-0008).

Parsing reuses ntc-templates (TextFSM) wherever it has a template, and TTP for the rest. It
runs on masked output, on the collector or the controller, and never talks to a device, so the
same `RawOutput`s always parse to the same records.

`parse()` takes one device's outputs, keyed by the allow-listed command, and returns plain
records that the controller stores as a Device, its Interfaces, its Links (L2, from CDP and LLDP)
and its RoutingNeighbors (L3, from OSPF and BGP). A command that's
missing or doesn't parse leaves its fields empty rather than guessed.

Adding a platform: list the commands its parsers read in `COMMANDS`, add a `_parse_<platform>`
function, and add sanitized fixtures under tests/fixtures/sanitized/<platform>/.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

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
    # The primary IPv4 address and its prefix length; empty when unnumbered or unset.
    ip_address: str = ""
    prefix_length: int | None = None


@dataclass(frozen=True)
class ParsedNeighbor:
    """A neighbor a device announces on one of its interfaces, by CDP or LLDP."""

    local_interface: str
    device_name: str
    interface_name: str
    protocol: str


@dataclass(frozen=True)
class ParsedRoutingNeighbor:
    """A routing protocol peer of the device: an OSPF neighbor or a BGP peer."""

    protocol: str
    address: str
    # The device's interface toward the neighbor, when the output names it or an interface
    # address covers the neighbor's; empty for a peer that isn't directly connected.
    local_interface: str = ""
    router_id: str = ""
    remote_as: int | None = None
    state: str = ""


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
    routing_neighbors: tuple[ParsedRoutingNeighbor, ...] = field(default_factory=tuple)


_IOS_COMMANDS = (
    "show version",
    "show inventory",
    "show interfaces",
    "show cdp neighbors detail",
    "show lldp neighbors detail",
    "show ip ospf neighbor",
    "show ip bgp summary",
)

# The commands each platform's parser reads. Every one is on the platform's allow-list.
COMMANDS: Mapping[Platform, tuple[str, ...]] = {
    Platform.IOS: _IOS_COMMANDS,
    Platform.IOS_XE: _IOS_COMMANDS,
    Platform.FORTIOS: (
        "get system status",
        "get hardware status",
        "get system interface",
        "get router info ospf neighbor",
        "get router info bgp summary",
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
    if not text.strip() or _not_running(text):
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
            **_address(row.get("ip_address", ""), row.get("prefix_length", "")),
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
    routing = [
        ParsedRoutingNeighbor(
            protocol="ospf",
            address=row["ip_address"],
            local_interface=expand_interface(row.get("interface", ""), known),
            router_id=row.get("neighbor_id", ""),
            state=row.get("state", ""),
        )
        for row in _ntc("cisco_ios", "show ip ospf neighbor", outputs)
        if row.get("ip_address")
    ]
    routing += [
        _bgp_peer(
            row["bgp_neighbor"], row.get("neighbor_as"), row.get("state_or_prefixes_received")
        )
        for row in _ntc("cisco_ios", "show ip bgp summary", outputs)
        if row.get("bgp_neighbor")
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
        routing_neighbors=_with_local_interfaces(routing, interfaces),
    )


# `get hardware status` and `get router info ospf neighbor` have no ntc-template, and the one for
# `get router info bgp summary` refuses the "VRF 0" header of FortiOS 7.
_FORTIOS_HARDWARE = """
<group name="hardware">
Model name: {{ model | ORPHRASE }}
</group>
"""


def _row_template(group: str, *columns: str) -> str:
    """A TTP template matching one table row per result, columns separated by spaces."""
    row = " ".join("{{ " + column + " }}" for column in columns)
    return f'\n<group name="{group}*">\n{row}\n</group>\n'


# "192.0.2.10   1   Full/Backup   00:00:33   192.0.2.10   port1"
_FORTIOS_OSPF = _row_template(
    "neighbors",
    "router_id | IP",
    "priority | DIGIT",
    "state",
    "dead_time",
    "address | IP",
    "interface",
)
# "203.0.113.1   4   64500   1442   1438   2   0   0   23:58:11   12"
_FORTIOS_BGP = _row_template(
    "peers",
    "address | IP",
    "version | DIGIT",
    "remote_as | DIGIT",
    "received | DIGIT",
    "sent | DIGIT",
    "table | DIGIT",
    "in_q | DIGIT",
    "out_q | DIGIT",
    "up_down",
    "state",
)


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
        ParsedInterface(
            name=row["name"],
            enabled=_fortios_status(row.get("status", "")),
            **_address(row.get("ip_address", ""), _prefix(row.get("netmask", ""))),
        )
        for row in _ntc("fortinet", "get system interface", outputs)
    )
    # A point-to-point neighbor's state is "Full/ -": one column, not two.
    ospf_text = re.sub(r"/ -(?=\s)", "/-", outputs.get("get router info ospf neighbor", ""))
    ospf = _ttp(_FORTIOS_OSPF, ospf_text)
    routing = [
        ParsedRoutingNeighbor(
            protocol="ospf",
            address=row["address"],
            local_interface=row.get("interface", ""),
            router_id=row.get("router_id", ""),
            state=row.get("state", ""),
        )
        for row in _rows(ospf.get("neighbors"))
        if row.get("address")
    ]
    routing += [
        _bgp_peer(row["address"], row.get("remote_as"), row.get("state"))
        for row in _rows(
            _ttp(_FORTIOS_BGP, outputs.get("get router info bgp summary", "")).get("peers")
        )
        if row.get("address")
    ]
    return ParsedDevice(
        name=name,
        platform=Platform.FORTIOS,
        manufacturer="Fortinet",
        model=model,
        serial=status.get("serial_number", ""),
        os_version=version["os"] if version else "",
        interfaces=interfaces,
        routing_neighbors=_with_local_interfaces(routing, interfaces),
    )


def _bgp_peer(address: str, remote_as: str | None, state: str | None) -> ParsedRoutingNeighbor:
    # The last column is the prefix count once the session is up, and the state otherwise.
    state = (state or "").strip()
    return ParsedRoutingNeighbor(
        protocol="bgp",
        address=address,
        remote_as=_as_number(remote_as),
        state="Established" if state.isdigit() else state,
    )


def _as_number(value: str | None) -> int | None:
    """An AS number, also in asdot notation (65000.1 is 65000 * 65536 + 1)."""
    high, _, low = (value or "").partition(".")
    if low:
        high_number, low_number = _int(high), _int(low)
        if high_number is None or low_number is None:
            return None
        return high_number * 65536 + low_number
    return _int(high)


# RoutingNeighbor.state's length.
_STATE_LENGTH = 32


def _with_local_interfaces(
    neighbors: Sequence[ParsedRoutingNeighbor], interfaces: Sequence[ParsedInterface]
) -> tuple[ParsedRoutingNeighbor, ...]:
    """Fill in the local interface of a neighbor whose address is on one of the device's subnets.

    A peer listed again (another address family or VRF of the same summary) is kept once.
    """
    networks = [
        (
            interface.name,
            ipaddress.ip_interface(f"{interface.ip_address}/{interface.prefix_length}"),
        )
        for interface in interfaces
        if interface.ip_address and interface.prefix_length is not None
    ]
    filled = {}
    for neighbor in neighbors:
        if (neighbor.protocol, neighbor.address) in filled:
            continue
        if not neighbor.local_interface:
            address = ipaddress.ip_address(neighbor.address)
            local = next((name for name, net in networks if address in net.network), "")
            neighbor = replace(neighbor, local_interface=local)
        neighbor = replace(neighbor, state=neighbor.state[:_STATE_LENGTH])
        filled[neighbor.protocol, neighbor.address] = neighbor
    return tuple(filled.values())


def _address(ip_address: str, prefix_length) -> dict:
    """An interface's address fields, empty for an unset (0.0.0.0) or malformed address."""
    prefix = _int(prefix_length)
    try:
        address = ipaddress.ip_interface(f"{ip_address}/{prefix}")
    except ValueError:
        return {}
    if address.ip.is_unspecified or prefix == 0:
        return {}
    return {"ip_address": str(address.ip), "prefix_length": prefix}


def _prefix(netmask: str) -> int | None:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen
    except ValueError:
        return None


def _rows(value) -> list[dict]:
    # TTP gives a dict for one match and a list for several.
    if isinstance(value, dict):
        return [value]
    return [row for row in value or [] if isinstance(row, dict)]


# IOS answers a show command for a protocol that isn't configured with a "%" line, such as
# "% BGP not active" or "% OSPF: No router process".
def _not_running(text: str) -> bool:
    return text.strip().startswith("%")


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
