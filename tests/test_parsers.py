"""Parsers turn the sanitized lab fixtures into the normalized model (YAN-1743, ADR-0008)."""

from pathlib import Path

import pytest
from cartio_core.masking import Platform
from cartio_core.parsers import (
    COMMANDS,
    ParsedInterface,
    ParsedNeighbor,
    ParseError,
    expand_interface,
    parse,
)
from cartio_core.readonly import ALLOWED, check_command

FIXTURES = Path(__file__).parent / "fixtures" / "sanitized"

# The lab: access-sw1 (IOS) - core-sw1 (IOS-XE) - edge-fw1 (FortiOS).
LAB = {
    "access-sw1": Platform.IOS,
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
    device = parse("ios", outputs("access-sw1"))
    assert (device.name, device.platform, device.manufacturer) == ("access-sw1", "ios", "Cisco")
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


def test_fortios_model_comes_from_the_ttp_hardware_template():
    lab = outputs("edge-fw1")
    lab["get system status"] = lab["get system status"].replace("FortiGate-60F v", "unknown v")
    assert parse("fortios", lab).model == "FortiGate-60F"


def test_missing_outputs_leave_fields_empty():
    device = parse("ios", {"show version": outputs("access-sw1")["show version"]})
    assert device.name == "access-sw1"
    assert device.interfaces == device.neighbors == ()


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
