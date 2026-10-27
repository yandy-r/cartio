# ADR-0006 Tech stack

* **Status:** Accepted by Yandy on 2026-10-27 (Python for both)
* **Date:** 2026-10-27
* **Source:** [Linear](https://linear.app/yandy-r/document/adr-0006-tech-stack-cfa09035fedb)

Legend: **[Decided]** = Yandy said so. **[Leaning]** = Yandy's stated preference, not yet final. **[Proposed]** = a recommendation for Yandy to accept or change.

## Context

What is already settled and shapes the stack:

* **[Decided]** Collectors run per customer and link back to one central controller. An engineer connects once from a laptop (ADR-0001).
* **[Decided]** v1 is assessments and discovery. Changes come later.
* **[Decided]** On site mode keeps data at the customer. Engineers get both live view through the controller (nothing stored there) and access from inside the customer network (ADR-0005).
* **[Decided]** Collectors usually run on a customer VM, with an engineer's laptop (often Windows) as the fallback.
* **[Decided]** Main inputs are config files and show outputs, plus live read access when granted. So most of the work is parsing text offline, and the same parsers must run on imported files and on live output.
* **[Decided]** Cartio is for our team's commercial use only, and open-source libraries are fine. **[Proposed]** Favour permissive licences (MIT, BSD, Apache) in the collector, since it runs on customer machines.
* **[Decided]** The controller starts on our own infrastructure, with a possible move later if scale needs it.
* **[Leaning]** Yandy leans to Python for network work because of its maturity.
* **[Proposed, ADR-0002/0003]** Secrets are masked at the collector. Drivers are read-only, with a command allow-list.

Because of On site mode, the collector must collect, parse, mask and run checks by itself.

## Research (checked 2026-10-27 on PyPI and the Go module proxy)

Both ecosystems are actively maintained. The difference is breadth, mainly in parsing, which is most of v1's work.

| Need | Python | Go |
| -- | -- | -- |
| SSH to devices | scrapli 2026.10.14 (MIT), Netmiko 4.8.0 (MIT, 175 platform types incl. Fortinet) | scrapligo v1.4.3 (Oct 2026), x/crypto ssh |
| Parse show output | ntc-templates 9.3.0 (Apache-2.0, 979 templates: 143 Cisco IOS/IOS-XE, 82 NX-OS, 20 Fortinet) with TextFSM 2.1.0; TTP 0.10.1 (MIT); Genie 26.9 (Apache-2.0, Linux and macOS only) | gotextfsm v1.2.0 (Aug 2026) can load ntc-templates; how fully it matches Python TextFSM is unproven |
| Parse whole configs for checks | ciscoconfparse2 (GPL-3.0); pybatfish (Apache-2.0) as a later option; TTP | Nothing comparable found |
| SNMP v2c/v3 | pysnmp 7.1.30 (lextudio, maintained) | gosnmp v1.47.0 (Oct 2026) |
| FortiOS REST API | fortigate-api 2.0.8 (Apache-2.0), or plain HTTP | Plain HTTP |
| Multi-vendor getters | NAPALM 5.2.0, Nornir 3.6.0 (both Apache-2.0) | None |
| Packaging | Container or uv install on the Linux VM; PyInstaller one-file exe for Windows | One static binary everywhere |

What this means:

* For config files and show outputs, Python has ready parsers and config-analysis tools. In Go we would write and maintain much of that ourselves.
* Go's real advantage is packaging. The VM-first decision shrinks it, since a container or a uv-managed install on a Linux VM is routine. The Windows laptop fallback is the one place packaging needs proving.
* Two Python libraries carry caveats. Genie doesn't run natively on Windows, so it can't be core parsing. ciscoconfparse2 is GPL-3.0, so under the permissive-licence proposal it stays out of the collector or is replaced by TTP rules.

## Decision

**[Decided] Option A, Python for both collector and controller.** Yandy accepted this on 2026-10-27, after the research above. The library and framework picks below remain proposals the prototype can revise.

* **Collector (Python 3.12+):**
  * scrapli for SSH, with Netmiko as fallback for odd platforms.
  * FortiOS REST over HTTPS (fortigate-api or plain httpx).
  * pysnmp for SNMP.
  * ntc-templates with TextFSM, and TTP, for parsing.
  * Masking and checks run locally.
  * Packaged as a container or uv install for the Linux VM, and a PyInstaller exe for the Windows laptop.
* **Shared parsing package:** one Python package of parsers, masking rules and checks, used by the collector and by the controller's import. A fix lands once.
* **Controller:** Django with PostgreSQL.
  * Why Django: mature auth, admin, ORM and migrations for customers, engagements and audit.
  * It's the same framework as NetBox, so the NetBox plugin route in [YAN-1642](https://linear.app/yandy-r/issue/YAN-1642/spike-build-on-open-source-netbox-and-similar-or-write-in-house) stays open.
  * The collector channel (ADR-0001, [YAN-1640](https://linear.app/yandy-r/issue/YAN-1640/spike-outbound-transport-and-proxy-compatibility)) runs on the controller side as an async endpoint or a small companion service. Collectors open the connection to it, and the controller never connects out to them.
  * FastAPI is the alternative if Django feels heavy.
* **Engineer UI:** server-rendered Django templates with htmx for v1, with no separate front-end build.
* **Rejected for now:**
  * Go everywhere: thinner parsing and no config-analysis tools.
  * Go collector with a Python controller: it either sends raw output through the controller, which works against On site mode, or keeps two parser stacks.
  * Go stays an option for a thin collector later if packaging becomes a problem.

## Consequences

* The prototype must prove three things:
  1. A PyInstaller build of the collector runs on Windows and reaches devices over SSH. scrapli's default system transport may not work on Windows, so test its asyncssh or paramiko transport.
  2. The collector runs on a Linux VM as a container or uv install.
  3. It parses show version, show running-config and show inventory with ntc-templates.
* Check every collector dependency's licence before adding it. Prefer MIT, BSD or Apache. asyncssh is EPL-2.0 or GPL-2.0, and paramiko is LGPL-2.1: both are acceptable but should be recorded.
* Updates to collectors (packaging and auto-update) belong to [YAN-1641](https://linear.app/yandy-r/issue/YAN-1641/spike-where-collectors-run-and-how-they-are-packaged).

## Open

* The hello-world collector landed in yandy-r/cartio#1 ([YAN-1646](https://linear.app/yandy-r/issue/YAN-1646/create-the-repo-ci-and-project-skeleton)).
* **[Decided]** Yandy created the repo as yandy-r/cartio, new and named for Cartio, not built on path-finder.
