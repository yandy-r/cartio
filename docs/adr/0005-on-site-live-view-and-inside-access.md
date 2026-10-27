# ADR-0005 On site mode: live view and inside access

* **Status:** Accepted (decided by Yandy, 2026-10-27). Details of how the two paths are controlled are proposed.
* **Date:** 2026-10-27
* **Deciders:** Yandy
* **Source:** [Linear](https://linear.app/yandy-r/document/adr-0005-on-site-mode-live-view-and-inside-access-c01b13775c6c)

## Context

In On site mode (ADR-0002) network data stays in a customer-chosen store. The question was: may the controller show an engineer that customer's data **live, without storing it**, or must the data only be opened from inside the customer network?

## Decision

**Decided by Yandy: both.** On site customers get both ways to see their data:

1. **Live view through the controller, nothing stored.** The engineer opens the customer in the controller UI; the controller asks the on-site collector for the view over its outbound channel and streams it to the browser. No network data is written to the controller's disk, database, logs or caches.
2. **Access from inside the customer network.** The engineer reaches a UI served by the on-site collector through the customer's own access (VPN, jump host, on site).

**Proposed details:**

* Both paths are available by default for every On site customer, so engineers keep "connect once" where the customer allows it.
* One per-customer switch, **Live view allowed** (on by default), lets a customer who requires it limit access to inside-only. This is the only extra setting, to keep choices few.
* Turning the switch on or off is audit-logged.
* Reports for On site customers are generated from on-site data and saved to the on-site store, whichever path the engineer used.

## Consequences

* The collector ships a small local UI (or the engineer UI runs in a local mode pointed at the collector) for the inside path.
* The controller needs a relay for live views with strict no-persist guarantees: no payload logging, no caching, memory-only buffers, and a test that proves nothing lands on disk.
* Customer-facing wording is needed for the live path: data passes through our controller in memory and is never stored there.
* If the live view switch is off, the controller shows only the customer and collector health for that customer.

## Alternatives considered

* **Live view only:** easiest for engineers, but some HIPAA/PCI customers may not accept any transit through our infrastructure.
* **Inside-only:** strictest, but breaks "connect once" for every On site customer.

## Follow-ups

* [YAN-1671](https://linear.app/yandy-r/issue/YAN-1671/on-site-view-for-engineers-per-adr-0005-decision) On site view for engineers covers building both paths.
* 01 v1 scope and 02 Architecture overview reflect this decision.
