# ADR-0001 Hub and spoke with outbound-only collectors

* **Status:** Proposed. Core shape decided by Yandy; transport details decided in
  [ADR-0009](0009-collector-transport.md) (2026-10-28).
* **Date:** 2026-10-27
* **Deciders:** Yandy
* **Source:** [Linear](https://linear.app/yandy-r/document/adr-0001-hub-and-spoke-with-outbound-only-collectors-8762f4fc1c85)

## Context

Customer access varies: on site with customer tools such as NetBrain, or remote over SSH, VPN, Arc or VMs. Engineers today connect separately into each environment. Yandy wants collectors, one or more per customer, that link back to one main controller so the whole system is one logical unit, and an engineer can connect once from a laptop and see everything without connecting to each collector.

Customer security teams generally refuse new inbound firewall rules, and many sites sit behind NAT or proxies.

## Decision

* **Decided by Yandy:** distributed collection, centralized management. One central controller; one or more collectors per customer; engineers work only through the controller.
* **Proposed:** collectors **only make outbound connections** to the controller (HTTPS on 443, through a proxy if required). The controller never connects into a customer network.
* **Superseded by [ADR-0009](0009-collector-transport.md):** ~~each collector authenticates with a per-collector client certificate (mTLS) issued at enrollment from a one-time token.~~ Each collector enrolls a key pair from a one-time token and signs every request with it; there is no mTLS. Collectors can be revoked from the controller.
* **Decided in [ADR-0009](0009-collector-transport.md):** the collector pulls jobs by long-poll over HTTPS and pushes results. Results are queued locally when offline and synced later, idempotently.
* **Proposed (from review):** the offline replay contract. Every job has an id from the controller. Every result carries its job id, the collector id and a result id that the collector makes once. The controller acknowledges each result by its result id, and the collector keeps a result until it is acknowledged. A result id the controller already holds is acknowledged again and not stored twice. The check-in protocol in the repo doesn't do this yet.
* **Proposed:** each collector belongs to exactly one customer and can only receive that customer's jobs and credentials.

## Consequences

* No inbound rules to negotiate with customers; works behind NAT and most proxies.
* Collector must handle reconnects, offline queueing and replay safely.
* The controller is a single point of management: its availability and security matter a lot (see controller hosting spike).
* TLS-inspecting customer proxies break mTLS, so ADR-0009 uses signed requests instead, with pinning to detect inspection and credentials sealed to the collector.
* Revoking a collector is central. The controller checks revocation on every request, and a long-poll is a new request at least every 25 seconds, so revocation takes effect within seconds (ADR-0009).

## Alternatives considered

* **Controller connects into each customer (VPN, SSH tunnels):** matches today's practice but needs inbound access or per-customer VPN setup, and does not give "connect once".
* **Fully independent collectors with their own UI:** simplest to build, but contradicts Yandy's requirement of one logical unit.
* **Message broker in the middle (MQTT, NATS):** good fit later for always-on collectors; adds an extra service to run for v1.

## Follow-ups

* Spike: collector host and packaging.
* Spike: transport and proxy compatibility test (YAN-1640, decided in ADR-0009).
