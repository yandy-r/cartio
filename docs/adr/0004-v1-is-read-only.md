# ADR-0004 v1 is read-only

* **Status:** Accepted (decided by Yandy).
* **Date:** 2026-10-27
* **Deciders:** Yandy
* **Source:** [Linear](https://linear.app/yandy-r/document/adr-0004-v1-is-read-only-18c19594b046)

## Context

Yandy: v1 is mostly network assessments and discovery, to propose solutions, fixes and enhancements to customers' network teams. Changes come later.

## Decision

* **Decided by Yandy:** v1 discovers, records, compares and reports. It never changes customer devices.
* **Proposed enforcement:**
  * Vendor drivers in v1 expose only read operations (show commands, GET API calls, SNMP get/walk). No config-mode commands, no POST/PUT/DELETE to device APIs, no SNMP set.
  * SSH drivers check each whole command against a per-vendor allow-list of exact read commands and their allowed arguments, not just the first word, and reject anything else, including output redirection, pipes to anything that writes, and chained commands. Negative tests cover each rejected form. (Tightened after review: a prefix list such as `show` or `diagnose` would let a write slip through as an argument or modifier.)
  * Onboarding asks customers for read-only accounts (ADR-0003).

## Consequences

* Much lower risk for pilots and easier customer approval.
* Recommendations in reports are text and suggested config snippets only; nothing is pushed.
* When automation arrives, it needs its own ADR covering approvals, change windows and rollback, and a separate write-capable driver layer.

## Alternatives considered

* **Allow "safe" writes (for example enabling LLDP for discovery):** tempting, but breaks the clear read-only promise; recommend instead.
