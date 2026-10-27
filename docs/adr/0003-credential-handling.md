# ADR-0003 Credential handling

* **Status:** Proposed.
* **Date:** 2026-10-27
* **Deciders:** Yandy
* **Source:** [Linear](https://linear.app/yandy-r/document/adr-0003-credential-handling-5fe2a2e49919)

## Context

Collectors need device credentials (SSH, SNMPv2c communities, SNMPv3 users, API tokens). We hold credentials for many customers; a leak across customers, or of customer secrets from configs, would be serious. Some customers are HIPAA/PCI.

## Decision (proposed)

1. **Scope:** every credential belongs to exactly one customer and can only be delivered to that customer's enrolled collectors.
2. **Hosted mode:** credentials are encrypted at rest on the controller with envelope encryption (a data key per customer, wrapped by a master key held outside the database, for example a KMS or a sealed key file). They are delivered to a collector only inside a job, over the mTLS channel, and kept in collector memory for the job's duration.
3. **On site mode:** credentials are entered on, and stored by, the on-site collector or the customer's store, encrypted with a key that never leaves the customer network. The controller only references them by name.
4. **Never displayed back:** after entry, a secret can be replaced or deleted, never read back in the UI or API.
5. **Config masking:** collectors mask secrets in captured configs (passwords, pre-shared keys, SNMP communities, private keys, API tokens) **before** data leaves the collector. Masking keeps a stable keyed digest per secret (proposed: HMAC-SHA256 with a key per customer, held where that customer's credentials are), so diffs still show "changed" without revealing values. Added after review: a plain hash would let anyone holding masked configs guess short passwords and SNMP communities, and a shared key would show when two customers use the same secret. Each digest records its key version, and the first collection after a key rotation is marked as a rotation so it isn't reported as a config change.
6. **Least privilege:** onboarding docs ask customers for read-only accounts (FortiGate read-only admin profile/API user, Cisco privilege level for show commands, SNMPv3 authPriv read views).
7. **Audit:** every credential create, update, use and delete is recorded in the collection run / audit log with who and which collector.

## Consequences

* Need a secrets module on both controller and collector, plus vendor-specific masking rules with tests.
* Losing the master key means re-entering credentials; document backup of the key.
* Read-only accounts reinforce ADR-0004.

## Alternatives considered

* **Integrate an external vault (HashiCorp Vault, Azure Key Vault) from day one:** stronger but heavier for v1; keep the secrets interface pluggable so it can come later.
* **Engineers type credentials per run, nothing stored:** safest, too much friction for repeat assessments.
