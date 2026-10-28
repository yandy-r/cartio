# ADR-0008 In-house device model, reusing open-source collection and parsing

* **Status:** Accepted by Yandy on 2026-10-28
* **Date:** 2026-10-28
* **Deciders:** Yandy
* **Source:** [Linear](https://linear.app/yandy-r/issue/YAN-1642/spike-build-on-open-source-netbox-and-similar-or-write-in-house) (spike YAN-1642)

## Context

Yandy decided to build Cartio in-house. This spike decides which building blocks to reuse, in
particular whether the normalized device model (Device, Interface, Link, Config snapshot, Source;
YAN-1656) sits on NetBox or Nautobot or is written in Cartio.

What is already settled and shapes the choice:

* Every controller table is `CustomerOwned`: queries must name a customer, the database refuses
  cross-customer links, and a test tries to cross customers for each table.
* Credentials sit in a per-customer encrypted vault, and sensitive actions write to an
  append-only audit log.
* On site customers keep network data in their own store, a PostgreSQL database or an SMB/NFS
  folder, and the controller stores none of it (ADR-0002, ADR-0005).
* Python and Django for collector and controller (ADR-0006). Licences don't block reuse; prefer
  permissive ones in the collector.

## Options considered

1. **Cartio as a NetBox (or Nautobot) plugin.** Gives an inventory UI and API for free, but
   means rebuilding the shipped controller on NetBox's terms. NetBox's Tenant is a label, not an
   isolation boundary: separation would rest on per-user object permissions, and the REST API,
   global search and GraphQL make cross-customer leaks easy. NetBox holds current state only, with
   no per-engagement snapshots, config snapshots or raw outputs, and it requires Site, DeviceType
   and Role before a device exists, which doesn't fit discovery from configs.
2. **NetBox as a separate inventory service.** Keeps our controller, but every write becomes an
   API sync and customer data splits across two databases. On site customers would each need to
   run NetBox (PostgreSQL, Redis, workers), and NetBox can't use the folder store from ADR-0002.
3. **In-house model, reusing collection and parsing libraries.** Chosen.

NetBox and Nautobot are both Apache-2.0, so licensing doesn't separate the options.

## Decision

**[Decided]** Write the normalized device model in Cartio, reuse open-source libraries for
collection and parsing, and treat NetBox as an integration.

| Layer | Choice | Why |
| -- | -- | -- |
| Inventory / source of truth | **Build:** Device, Interface, Link, ConfigSnapshot, Source as `CustomerOwned` Django models, versioned per engagement | Inherits isolation, DB guards and audit; same schema serves the on-site store |
| SSH collection | **Wrap:** scrapli, with Netmiko as fallback, behind Cartio's read-only driver and command allow-list | Mature transports; the allow-list stays ours (ADR-0004) |
| Show-output parsing | **Reuse:** ntc-templates with TextFSM, and TTP | Broad template coverage, permissive licences, run in collector and controller |
| Multi-vendor getters | **Don't depend on NAPALM** in v1 | Its driver API includes config-push methods, which works against a read-only driver interface |
| Genie | **Don't use** as core parsing | Doesn't run natively on Windows (laptop collector) |
| Topology | **Build** the LLDP/CDP crawl on our parsed neighbour data; existing crawl tools are reference only | Small, and it must respect scope and the allow-list |
| NetBox / Nautobot | **Integrate later** as import/export (DESIGN.md integrations catalogue) | Customers who run NetBox get data in and out without it being our store |

* **[Proposed]** Name fields the way NetBox does where it fits (for example `name`, `serial`,
  `platform`, interface `type`), so a later NetBox export maps cleanly.

## Consequences

* YAN-1656 is unblocked and builds the model on the existing `CustomerOwned` patterns, including
  isolation tests and DB guards for each new table.
* No extra service to run at On site customers.
* We own the inventory schema and its migrations, and maintain the topology crawl ourselves.
* NetBox import/export needs its own Linear issue when it is planned.
