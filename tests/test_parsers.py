"""Parsers turn the sanitized lab fixtures into the normalized model (YAN-1743, ADR-0008)."""

from pathlib import Path

import pytest
from cartio_core.masking import Platform
from cartio_core.parsers import (
    COMMANDS,
    ParsedInterface,
    ParsedNeighbor,
    ParsedRoutingNeighbor,
    ParseError,
    expand_interface,
    parse,
)
from cartio_core.readonly import ALLOWED, check_command

FIXTURES = Path(__file__).parent / "fixtures" / "sanitized"

# The lab: floor-sw1 (IOS) - core-sw1 (IOS-XE) - edge-fw1 (FortiOS).
LAB = {
    "floor-sw1": Platform.IOS,
    "core-sw1": Platform.IOS_XE,
    "edge-fw1": Platform.FORTIOS,
}


def outputs(name: str) -> dict[str, str]:
    """A lab device's outputs by command. Files are named after the command."""
    folder = FIXTURES / LAB[name].value / name
    return {
        path.stem.replace("_", " "): path.read_text(encoding="utf-8").replace("\r\n", "\n")
        for path in sorted(folder.glob("*.txt"))
    }


@pytest.mark.parametrize("platform", list(Platform))
def test_parsers_only_read_allow_listed_commands(platform):
    assert COMMANDS[platform]
    for command in COMMANDS[platform]:
        assert command in ALLOWED[platform]
        assert check_command(platform, command) == command


@pytest.mark.parametrize("name", list(LAB))
def test_every_fixture_is_a_command_the_parser_reads(name):
    assert set(outputs(name)) == set(COMMANDS[LAB[name]])


def test_ios_switch():
    device = parse("ios", outputs("floor-sw1"))
    assert (device.name, device.platform, device.manufacturer) == ("floor-sw1", "ios", "Cisco")
    assert (device.model, device.serial, device.os_version) == (
        "WS-C2960X-48FPD-L",
        "FOC0000X0AA",
        "15.2(7)E8",
    )
    assert [i.name for i in device.interfaces] == [
        "Vlan10",
        "GigabitEthernet1/0/1",
        "GigabitEthernet1/0/2",
        "GigabitEthernet1/0/49",
    ]
    assert device.interfaces[2] == ParsedInterface(
        name="GigabitEthernet1/0/2",
        enabled=False,
        mac_address="00:00:5e:00:53:04",
        mtu=1500,
        speed=10000,
    )
    assert device.interfaces[3].description == "Uplink to core-sw1"
    assert device.interfaces[3].speed == 1_000_000
    # LLDP's short local name is expanded to the device's own interface name.
    assert device.neighbors == (
        ParsedNeighbor("GigabitEthernet1/0/49", "core-sw1", "TenGigabitEthernet1/1/1", "cdp"),
        ParsedNeighbor("GigabitEthernet1/0/49", "core-sw1", "Te1/1/1", "lldp"),
    )


def test_ios_xe_switch_takes_the_chassis_from_the_inventory():
    device = parse("ios-xe", outputs("core-sw1"))
    assert (device.name, device.platform) == ("core-sw1", Platform.IOS_XE)
    assert (device.model, device.serial, device.os_version) == (
        "C9300-48P",
        "FOC0000Y0BB",
        "17.9.4a",
    )
    by_name = {i.name: i for i in device.interfaces}
    assert by_name["TenGigabitEthernet1/1/1"].speed == 10_000_000
    assert by_name["TenGigabitEthernet1/1/2"].enabled is False
    assert ParsedNeighbor("GigabitEthernet1/0/48", "edge-fw1", "port1", "lldp") in (
        device.neighbors
    )


def test_fortios_firewall():
    device = parse("fortios", outputs("edge-fw1"))
    assert (device.name, device.manufacturer, device.model) == (
        "edge-fw1",
        "Fortinet",
        "FortiGate-60F",
    )
    assert (device.serial, device.os_version) == ("FGT60F0000000001", "7.2.8")
    assert [(i.name, i.enabled) for i in device.interfaces] == [
        ("fortilink", True),
        ("internal1", True),
        ("port1", True),
        ("wan1", True),
        ("wan2", False),
    ]
    assert device.neighbors == ()


def test_interface_addresses():
    core = {i.name: i for i in parse("ios-xe", outputs("core-sw1")).interfaces}
    assert (core["Vlan10"].ip_address, core["Vlan10"].prefix_length) == ("192.0.2.10", 24)
    assert (
        core["GigabitEthernet1/0/48"].ip_address,
        core["GigabitEthernet1/0/48"].prefix_length,
    ) == (
        "",
        None,
    )
    edge = {i.name: i for i in parse("fortios", outputs("edge-fw1")).interfaces}
    assert (edge["port1"].ip_address, edge["port1"].prefix_length) == ("192.0.2.1", 24)
    # FortiOS shows an unset address as 0.0.0.0 0.0.0.0.
    assert (edge["wan2"].ip_address, edge["wan2"].prefix_length) == ("", None)


def test_routing_neighbors():
    assert parse("ios-xe", outputs("core-sw1")).routing_neighbors == (
        ParsedRoutingNeighbor("ospf", "192.0.2.1", "Vlan10", "192.0.2.1", None, "FULL/DR"),
    )
    assert parse("fortios", outputs("edge-fw1")).routing_neighbors == (
        ParsedRoutingNeighbor("ospf", "192.0.2.10", "port1", "192.0.2.10", None, "Full/Backup"),
        # BGP doesn't name an interface: the peer's address is on wan1's subnet.
        ParsedRoutingNeighbor("bgp", "203.0.113.1", "wan1", "", 64500, "Established"),
    )
    # floor-sw1 runs neither protocol: "% BGP not active" and an empty OSPF table.
    assert parse("ios", outputs("floor-sw1")).routing_neighbors == ()


def test_a_bgp_peer_that_isnt_up_keeps_its_state_and_a_remote_peer_has_no_interface():
    lab = outputs("edge-fw1")
    lab["get router info bgp summary"] = lab["get router info bgp summary"].replace(
        "203.0.113.1     4      64500    1442    1438        2    0    0 23:58:11        12",
        "198.18.0.1      4      64501       0       0        0    0    0 never    Active",
    )
    bgp = [n for n in parse("fortios", lab).routing_neighbors if n.protocol == "bgp"]
    assert bgp == [ParsedRoutingNeighbor("bgp", "198.18.0.1", "", "", 64501, "Active")]


def test_fortios_point_to_point_ospf_neighbors_are_kept():
    lab = outputs("edge-fw1")
    lab["get router info ospf neighbor"] += (
        "198.51.100.2      1   Full/ -         00:00:31    198.51.100.2    internal1\n"
    )
    ospf = [n for n in parse("fortios", lab).routing_neighbors if n.protocol == "ospf"]
    assert ospf[-1] == ParsedRoutingNeighbor(
        "ospf", "198.51.100.2", "internal1", "198.51.100.2", None, "Full/-"
    )


def test_ios_bgp_peers():
    summary = "\n".join(
        [
            "BGP router identifier 192.0.2.10, local AS number 64512",
            "BGP table version is 5, main routing table version 5",
            "",
            "Neighbor        V    AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd",
            "192.0.2.1       4        64512     120     118        5    0    0 01:52:10        3",
            "198.18.0.1      4        64501       0       0        1    0    0 never    Idle",
            "198.18.0.9      4      65000.1       0       0        1    0    0 never    Active",
            # The same peer again, as in a second address family: kept once.
            "192.0.2.1       4        64512     120     118        5    0    0 01:52:10        3",
        ]
    )
    lab = {**outputs("core-sw1"), "show ip bgp summary": summary}
    bgp = [n for n in parse("ios-xe", lab).routing_neighbors if n.protocol == "bgp"]
    assert bgp == [
        ParsedRoutingNeighbor("bgp", "192.0.2.1", "Vlan10", "", 64512, "Established"),
        ParsedRoutingNeighbor("bgp", "198.18.0.1", "", "", 64501, "Idle"),
        # asdot 65000.1 is 65000 * 65536 + 1.
        ParsedRoutingNeighbor("bgp", "198.18.0.9", "", "", 4_259_840_001, "Active"),
    ]


def test_fortios_model_comes_from_the_ttp_hardware_template():
    lab = outputs("edge-fw1")
    lab["get system status"] = lab["get system status"].replace("FortiGate-60F v", "unknown v")
    assert parse("fortios", lab).model == "FortiGate-60F"


def test_missing_outputs_leave_fields_empty():
    device = parse("ios", {"show version": outputs("floor-sw1")["show version"]})
    assert device.name == "floor-sw1"
    assert device.interfaces == device.neighbors == device.routing_neighbors == ()


@pytest.mark.parametrize("platform", list(Platform))
def test_a_device_needs_a_host_name(platform):
    with pytest.raises(ParseError):
        parse(platform, {})


def test_commands_are_matched_whatever_the_spacing():
    lab = {
        f"  {command.replace(' ', '  ')} ": text for command, text in outputs("core-sw1").items()
    }
    assert parse("ios-xe", lab) == parse("ios-xe", outputs("core-sw1"))


@pytest.mark.parametrize(
    ("short", "known", "full"),
    [
        ("Gi1/0/1", [], "GigabitEthernet1/0/1"),
        ("Te1/1/1", [], "TenGigabitEthernet1/1/1"),
        ("Twe1/0/1", [], "TwentyFiveGigE1/0/1"),
        ("Po10", [], "Port-channel10"),
        ("Gi1/0/1", ["GigabitEthernet1/0/1"], "GigabitEthernet1/0/1"),
        # A name the device itself uses wins over the usual expansion.
        ("Et1/1", ["Ethernet1/1", "EthernetX1/1"], "Ethernet1/1"),
        ("port1", ["port1"], "port1"),
        ("port1", [], "port1"),
    ],
)
def test_expand_interface(short, known, full):
    assert expand_interface(short, known) == full
