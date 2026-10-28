# Adding a platform

A platform is a device OS that Cartio can collect from, such as Cisco IOS or FortiOS. Adding one
touches four places, mostly in `cartio-core` so the collector and the controller agree. Nothing
here may add a way to change a device (ADR-0004).

## 1. Name it and mask its secrets

Add the platform to `Platform` in `packages/cartio-core/src/cartio_core/masking.py`, with
masking rules for every kind of secret its configs hold, and fixtures for each in
`tests/test_masking.py`. Configs and outputs are masked on the collector before they leave it
(ADR-0003), so a platform without masking rules can't be collected.

## 2. List its read commands

Add the platform's read commands to `ALLOWED` in
`packages/cartio-core/src/cartio_core/readonly.py`. The allow-list is strict on purpose:

- Each entry is a whole command, spelled out in full: `show running-config view full`, never
  `show` or `sh run`. A command matches only if every word matches.
- An argument is allowed only where the entry has a placeholder, such as `{interface}`, and
  only in the form `PLACEHOLDERS` allows. Add a placeholder only for a value that can't carry
  CLI syntax.
- Pipes, redirection, chaining and control characters are refused on every platform. Filter
  output on the collector instead.
- No config-mode, file, debug, clear, reload or execute commands. On FortiOS that includes
  `config global` and `config vdom`: VDOM context needs its own reviewed design.
- Session setup a transport needs (for example `terminal length 0`) belongs in the transport's
  connection options, not in the allow-list.

Then add refusal tests to `tests/test_readonly.py`: the platform's config-mode command, its save
and reboot commands, and a write hidden after an allowed read. The test that every platform has
an allow-list fails until the new one has entries.

## 3. Collect through a driver

Drivers subclass `ReadOnlyDriver` and implement `_send()` only. `run()` checks the allow-list
before `_send()` is called and can't be overridden. SNMP and HTTP collection call `check_snmp()`
and `check_http()` before each request.

The SSH driver is `ScrapliDriver` in `packages/cartio-collector/src/cartio_collector/ssh.py`. It
uses scrapli's classic (pure Python) line, `scrapli<2026`: the 2026 rewrite has no Windows build,
and the laptop collector runs on Windows, where the driver uses scrapli's paramiko transport.
Session setup is the transport's job, never an allow-list entry:

- IOS and IOS-XE use scrapli's `IOSXEDriver`, which enters privileged exec and turns paging off
  (`terminal length 0`, `terminal width 512`) when it opens.
- FortiOS uses scrapli's `GenericDriver` with no setup commands. FortiOS can only turn paging off
  in config mode, so the read-only account's console must already use standard output
  (`config system console` / `set output standard`). The customer sets this during onboarding;
  Cartio never does.

The driver checks host keys against a known-hosts file unless told not to. A new platform picks
its scrapli driver class in `ScrapliDriver.open()`.

## 4. Parse its outputs

Parsers live in `packages/cartio-core/src/cartio_core/parsers.py`, so the collector and the
controller share them. They use ntc-templates (TextFSM) where a template exists, and TTP where
it doesn't or doesn't fit (FortiOS `get hardware status`, `get router info ospf neighbor` and
`get router info bgp summary`). For a new platform:

- List the commands its parser reads in `COMMANDS`. Each must be on its allow-list (a test
  checks), and `collect()` on the collector runs exactly these, masking each output.
- Add a `_parse_<platform>()` that returns a `ParsedDevice` with its interfaces (and their IPv4
  addresses), its CDP/LLDP neighbors and its OSPF/BGP routing neighbors. Leave a field empty when
  the output doesn't say; never guess. A command for a protocol the device doesn't run (IOS
  answers "% BGP not active") parses to nothing.
- Add sanitized fixtures in `tests/fixtures/sanitized/<platform>/<host>/`, one file per command
  named after it (`show_version.txt`). Use documentation addresses (192.0.2.0/24,
  198.51.100.0/24, 203.0.113.0/24), `example.net` names and made-up serials, and no secrets.
  Add tests to `tests/test_parsers.py`, and add the host to the lab in `tests/test_inventory.py`
  when it has neighbors there.

On the controller, `collectors/inventory.py` stores one device's masked outputs as `RawOutput`s
and parses them into `Device`, `Interface`, `Link` and `RoutingNeighbor` records
(`store_outputs()`). A routing neighbor's far device is the engagement's device that owns the
neighbor's address, once it's collected. It refuses
output that still holds a secret. `reparse()` runs the parsers again on a `Source`'s stored
outputs after a parser fix, and gives the same records whatever order the devices arrived in.
Both are audit-logged.

On site customers keep network data in their own store (ADR-0002), so the device-model tables
refuse their records (`OnSiteDataError`), whatever the write path.

## The device model

The normalized model lives in `cartio_controller/collectors/models.py`. Every table is
customer-owned and belongs to one engagement, so each assessment keeps its own copy of the
network:

| Model | Holds |
| -- | -- |
| `Source` | How data arrived: a live run, an uploaded file or a customer tool export |
| `Device` | Name, platform, manufacturer, model, serial, OS version, management IP, last seen |
| `Interface` | A device's ports and logical interfaces, with the primary IPv4 address |
| `Link` | An L2 neighbor relation (CDP/LLDP), to another interface or to a named device not in scope |
| `RoutingNeighbor` | An L3 relation (OSPF neighbor or BGP peer), with its subnet, and the far device once collected |
| `RawOutput` | One command's masked output, kept for re-parsing |
| `ConfigSnapshot` | A device's masked config, with its SHA-256 |

`Source`, `RawOutput` and `ConfigSnapshot` are append-only. A record can't point at another
customer's or another engagement's records.
