"""Drivers send allow-listed read commands only, and nothing else reaches a device (ADR-0004)."""

import pytest
from cartio_core.masking import Platform
from cartio_core.readonly import (
    ALLOWED,
    CommandRefused,
    ReadOnlyDriver,
    check_command,
    check_http,
    check_snmp,
)


class RecordingDriver(ReadOnlyDriver):
    """A driver whose transport only records what it was asked to send."""

    def __init__(self, platform):
        super().__init__(platform)
        self.sent = []

    def _send(self, command):
        self.sent.append(command)
        return "output"


@pytest.mark.parametrize("platform", list(Platform))
def test_every_platform_has_an_allow_list(platform):
    assert ALLOWED[platform]
    for entry in ALLOWED[platform]:
        assert entry.split()[0] in {"show", "get"}, entry


@pytest.mark.parametrize(
    "platform, command, sent",
    [
        ("ios", "show version", "show version"),
        ("ios-xe", "  show   running-config  view full ", "show running-config view full"),
        ("ios", "SHOW IP INTERFACE BRIEF", "show ip interface brief"),
        ("ios", "show interfaces GigabitEthernet1/0/1", "show interfaces GigabitEthernet1/0/1"),
        ("fortios", "get system status", "get system status"),
        ("fortios", "show full-configuration", "show full-configuration"),
    ],
)
def test_an_allow_listed_read_is_sent(platform, command, sent):
    driver = RecordingDriver(platform)
    assert driver.run(command) == "output"
    assert driver.sent == [sent]


REFUSED = {
    "ios": [
        "configure terminal",
        "conf t",
        "config t",
        "write memory",
        "copy running-config startup-config",
        "reload",
        "clear counters",
        "debug all",
        "terminal length 0",
        "enable",
        # Abbreviations and prefixes of allowed commands aren't allowed either.
        "sh run",
        "show",
        "show running",
        "show running-config all",
        "show versionx",
        # A write hidden behind an allowed read.
        "show running-config | redirect flash:x",
        "show running-config | tee flash:x",
        "show running-config | append flash:x",
        "show version | include IOS",
        "show running-config > flash:x",
        "show version; reload",
        "show version && reload",
        "show version\nconfigure terminal",
        "show version\rreload",
        "show interfaces Gi1/0/1 | redirect x",
        "show interfaces `reload`",
        "show interfaces $(reload)",
        "show interfaces Gi1/0/1 extra",
        "",
        "   ",
    ],
    "fortios": [
        "config system interface",
        "config global",
        "config vdom",
        "edit port1",
        "set allowaccess ssh",
        "execute reboot",
        "execute factoryreset",
        "diagnose sys kill 11 1",
        "show",
        "get",
        "show full-configuration | grep admin",
        "get system status; execute reboot",
    ],
}


@pytest.mark.parametrize(
    "platform, command",
    [(platform, command) for platform, commands in REFUSED.items() for command in commands],
)
def test_anything_else_is_refused_before_it_reaches_the_device(platform, command):
    driver = RecordingDriver(platform)
    with pytest.raises(CommandRefused):
        driver.run(command)
    assert driver.sent == []


def test_an_unknown_platform_is_refused():
    with pytest.raises(CommandRefused):
        check_command("junos", "show version")
    with pytest.raises(CommandRefused):
        RecordingDriver("junos")


def test_a_driver_cant_replace_the_checked_entry_point():
    with pytest.raises(TypeError):

        class Unchecked(ReadOnlyDriver):
            def run(self, command):
                return self._send(command)

            def _send(self, command):
                return ""


@pytest.mark.parametrize("operation", ["get", "getnext", "GETBULK", "walk"])
def test_snmp_reads_are_allowed(operation):
    assert check_snmp(operation) == operation.lower()


@pytest.mark.parametrize("operation", ["set", "SET", "trap", "inform", "", "get; set"])
def test_snmp_writes_are_refused(operation):
    with pytest.raises(CommandRefused):
        check_snmp(operation)


@pytest.mark.parametrize("method", ["GET", "head"])
def test_http_reads_are_allowed(method):
    assert check_http(method) == method.upper()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "OPTIONS", ""])
def test_http_writes_are_refused(method):
    with pytest.raises(CommandRefused):
        check_http(method)
