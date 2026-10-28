# Adding a platform

A platform is a device OS that Cartio can collect from, such as Cisco IOS or FortiOS. Adding one
touches three places, all in `cartio-core` so the collector and the controller agree. Nothing
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

Drivers subclass `ReadOnlyDriver` and implement `_send()` only, wrapping scrapli (or Netmiko as
the fallback, ADR-0008). `run()` checks the allow-list before `_send()` is called and can't be
overridden. SNMP and HTTP collection call `check_snmp()` and `check_http()` before each request.

Each command's output is stored as a `RawOutput` next to the parsed `Device`, `Interface` and
`Link` records, so parsers can be re-run on the same data. Parsers use ntc-templates (TextFSM)
or TTP and are tested against sanitized fixtures in `tests/fixtures/sanitized/`.

## The device model

The normalized model lives in `cartio_controller/collectors/models.py`. Every table is
customer-owned and belongs to one engagement, so each assessment keeps its own copy of the
network:

| Model | Holds |
| -- | -- |
| `Source` | How data arrived: a live run, an uploaded file or a customer tool export |
| `Device` | Name, platform, manufacturer, model, serial, OS version, management IP, last seen |
| `Interface` | A device's ports and logical interfaces |
| `Link` | A neighbor relation, to another interface or to a named device not in scope |
| `RawOutput` | One command's masked output, kept for re-parsing |
| `ConfigSnapshot` | A device's masked config, with its SHA-256 |

`Source`, `RawOutput` and `ConfigSnapshot` are append-only. A record can't point at another
customer's or another engagement's records.
