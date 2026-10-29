"""Read-only SSH collection from customer devices, on scrapli (ADR-0004, ADR-0008).

`ScrapliDriver` wraps a scrapli connection in `ReadOnlyDriver`, so every command goes through the
allow-list before it is sent. The connection is never handed out: nothing here can enter config
mode, commit or copy files.

Session setup lives in the transport, not the allow-list. On IOS and IOS-XE scrapli turns paging
off (`terminal length 0`, `terminal width 512`) when it opens the session. FortiOS has no
session-only way to do that, so the read-only account's console must already be set to standard
output; see docs/drivers/adding-a-platform.md.

`collect()` runs the commands the parsers need and masks each output before it is returned, so
nothing unmasked leaves the collector (ADR-0003).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any, Protocol

from cartio_core.masking import MaskedConfig, MaskingKey, Platform, mask_config
from cartio_core.parsers import COMMANDS
from cartio_core.readonly import ReadOnlyDriver

# The command that shows a device's whole running config, by platform.
CONFIG_COMMANDS = {
    Platform.IOS: "show running-config",
    Platform.IOS_XE: "show running-config",
    Platform.FORTIOS: "show full-configuration",
}

# FortiOS prompts: "edge-fw1 # ", or "edge-fw1 (root) # " inside a VDOM.
_FORTIOS_PROMPT = r"^[\w.-]{1,64}(?: \([\w.-]{1,32}\))? [#$]\s*$"


class Connection(Protocol):
    """The part of a scrapli driver that `ScrapliDriver` uses."""

    def send_command(self, command: str) -> Any: ...

    def close(self) -> None: ...


class ScrapliDriver(ReadOnlyDriver):
    """An allow-listed SSH session to one device. Use `open()`, or pass a connection to test."""

    def __init__(self, platform: Platform | str, connection: Connection) -> None:
        super().__init__(platform)
        self._connection = connection

    @classmethod
    def open(
        cls,
        platform: Platform | str,
        host: str,
        *,
        username: str,
        password: str = "",
        private_key: str = "",
        port: int = 22,
        known_hosts_file: str | bool = True,
        timeout: float = 30.0,
    ) -> ScrapliDriver:
        """Connect to `host`. Its host key must be known, unless `known_hosts_file` is False."""
        platform = Platform(platform)
        options: dict[str, Any] = {
            "host": host,
            "port": port,
            "auth_username": username,
            "auth_password": password,
            "auth_private_key": private_key,
            "auth_strict_key": known_hosts_file is not False,
            "ssh_known_hosts_file": known_hosts_file,
            "timeout_ops": timeout,
            # The system ssh binary isn't there on the Windows laptop collector.
            "transport": "paramiko" if sys.platform == "win32" else "system",
        }
        if platform is Platform.FORTIOS:
            from scrapli.driver import GenericDriver

            connection = GenericDriver(
                comms_prompt_pattern=_FORTIOS_PROMPT, on_open=None, **options
            )
        else:
            # Opens in privileged exec and turns paging off (`terminal length 0`).
            from scrapli.driver.core import IOSXEDriver

            connection = IOSXEDriver(**options)
        connection.open()
        return cls(platform, connection)

    def _send(self, command: str) -> str:
        return self._connection.send_command(command).result

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ScrapliDriver:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def collect(driver: ReadOnlyDriver, key: MaskingKey) -> Mapping[str, str]:
    """Run the commands the parsers read and return each one's masked output, by command."""
    return {
        command: mask_config(driver.run(command), driver.platform, key).text
        for command in COMMANDS[driver.platform]
    }


def capture_config(driver: ReadOnlyDriver, key: MaskingKey) -> MaskedConfig:
    """The device's running config, masked with the customer's key before it goes anywhere."""
    return mask_config(driver.run(CONFIG_COMMANDS[driver.platform]), driver.platform, key)
