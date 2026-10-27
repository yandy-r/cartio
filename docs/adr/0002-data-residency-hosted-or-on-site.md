# ADR-0002 Data residency per customer: Hosted or On site

* **Status:** Proposed. The need for an on-site option is decided by Yandy; the two-mode design is proposed.
* **Date:** 2026-10-27
* **Deciders:** Yandy
* **Source:** [Linear](https://linear.app/yandy-r/document/adr-0002-data-residency-per-customer-hosted-or-on-site-d030b51bc563)

## Context

Yandy: some customers, especially under HIPAA and PCI, may require collected data to stay on site, in a customer database, folder or something they choose. Keep the choices few.

## Decision

* **Decided by Yandy:** offer a per-customer option to keep data on site, with minimal choices.
* **Proposed:** exactly **two modes**, set when a customer is onboarded:

| Mode | Where network data is stored | What the controller keeps | How engineers see data |
| -- | -- | -- | -- |
| Hosted (default) | Controller database, separated per customer | Inventory, configs with secrets masked, check results, reports | In the controller with one login |
| On site | A customer-chosen store on their network: a database (proposed: PostgreSQL) or a folder on a file share (SMB/NFS) | Only collector health and job status; no network data | Live view through the controller with nothing stored, or from the on-site collector through the customer's access (both, decided in ADR-0005) |

* **Proposed:** in On site mode, device credentials are also stored on site, never on the controller.
* **Proposed:** the mode is a property of the customer, not of a collector or engagement, so all of a customer's data follows one rule.
* **Proposed:** changing mode is an explicit admin action that is audit-logged; moving from Hosted to On site deletes that customer's network data from the controller after export. **Proposed (from review):** deletion waits until the on-site collector confirms that it wrote the export to the on-site store and the export's checksums match. If the export or the check fails, the controller keeps its copy and the change can be retried.

## Consequences

* Two code paths for storage; keep them behind one storage interface so drivers and checks don't care.
* On site customers need a reachable store and a collector with write access to it; the onboarding checklist must cover that.
* Reports for On site customers are generated on the collector side or in the browser from on-site data, not stored centrally.
* Compliance answers become simple to state: "On site mode: your network data is stored only in your network, and credentials never leave it. With live view on, data passes through our controller in memory and is never stored there (ADR-0005)." (Corrected after review: the earlier wording said no network data leaves the network, which live view contradicts.)

## Alternatives considered

* **Hosted only:** simplest, but rules out HIPAA/PCI customers Yandy named.
* **Many storage targets (S3, Azure Blob, any SQL):** more flexible, contradicts "not too many choices".
* **Per-engagement mode:** more granular, more confusing; can be added later if needed.

## Open

* Settled: On site allows both a live, non-stored view through the controller and inside access (ADR-0005, Accepted).
