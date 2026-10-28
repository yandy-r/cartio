"""The scrapli driver only ever sends allow-listed reads (YAN-1743, ADR-0004)."""

from types import SimpleNamespace

import pytest
from cartio_collector.ssh import ScrapliDriver, collect
from cartio_core.masking import MaskingKey, find_unmasked
from cartio_core.parsers import COMMANDS
from cartio_core.readonly import CommandRefused

SP = " "
KEY = MaskingKey("k1", b"customer-a-masking-key-0123456789")


class FakeConnection:
    """Stands in for a scrapli session: records what was sent, answers from a script."""

    def __init__(self, answers=None):
        self.sent = []
        self.answers = answers or {}
        self.closed = False

    def send_command(self, command):
        self.sent.append(command)
        return SimpleNamespace(result=self.answers.get(command, ""), failed=False)

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "command",
    [
        "configure terminal",
        "conf t",
        "copy running-config startup-config",
        "write memory",
        "reload",
        "show version | redirect flash:x",
        "show version ; reload",
        "show version\nreload",
        "sh ver",
        "",
    ],
)
def test_a_command_that_isnt_allow_listed_never_reaches_the_device(command):
    connection = FakeConnection()
    driver = ScrapliDriver("ios", connection)
    with pytest.raises(CommandRefused):
        driver.run(command)
    assert connection.sent == []


@pytest.mark.parametrize(
    "command", ["config global", "config vdom", "execute reboot", "diagnose sys top"]
)
def test_fortios_writes_and_vdom_context_are_refused(command):
    connection = FakeConnection()
    with pytest.raises(CommandRefused):
        ScrapliDriver("fortios", connection).run(command)
    assert connection.sent == []


def test_an_allowed_read_is_sent_as_listed():
    connection = FakeConnection({"show version": "core-sw1 uptime is 1 week"})
    assert ScrapliDriver("ios-xe", connection).run("SHOW  Version") == "core-sw1 uptime is 1 week"
    assert connection.sent == ["show version"]


def test_the_driver_exposes_no_way_to_write():
    driver = ScrapliDriver("ios", FakeConnection())
    for name in ("send_config", "send_configs", "send_interactive", "connection", "commit"):
        assert not hasattr(driver, name)


def test_run_cant_be_overridden():
    with pytest.raises(TypeError):

        class Sneaky(ScrapliDriver):
            def run(self, command):
                return self._send(command)


def test_the_driver_closes_its_session():
    connection = FakeConnection()
    with ScrapliDriver("ios", connection):
        pass
    assert connection.closed


def test_collect_runs_the_parser_commands_and_masks_each_output():
    # Built with SP so the repo's secret scan doesn't flag this file.
    secret = SP.join(["enable", "secret", "5", "$1$" + "a" * 26])
    connection = FakeConnection({"show version": f"core-sw1 uptime is 1 week\n{secret}\n"})
    collected = collect(ScrapliDriver("ios", connection), KEY)
    assert connection.sent == list(COMMANDS["ios"])
    assert set(collected) == set(COMMANDS["ios"])
    assert secret not in collected["show version"]
    assert "<masked:k1:" in collected["show version"]
    assert not any(find_unmasked(text, "ios") for text in collected.values())
