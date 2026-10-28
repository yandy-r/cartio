# ADR-0009 Collector transport: HTTPS long-poll and signed requests

* **Status:** Accepted by Yandy on 2026-10-28. The two choices below are decided; the details
  under them are proposed and the prototype can revise them.
* **Date:** 2026-10-28
* **Deciders:** Yandy
* **Source:** spike [YAN-1640](https://linear.app/yandy-r/issue/YAN-1640/spike-outbound-transport-and-proxy-compatibility).
  Options write-up: "Collector transport: options (YAN-1640)".
* **Updates:** [ADR-0001](0001-hub-and-spoke-outbound-collectors.md). Its transport and
  certificate bullets are replaced by this ADR.

## Context

ADR-0001 settles that collectors only connect out, over HTTPS on 443, and proposed mTLS with a
per-collector client certificate, with jobs delivered by long-poll or websocket. Before
enrollment (YAN-1650) and the job channel (YAN-1651) are built, two things had to be decided:
how jobs reach the collector, and how the collector proves who it is.

What the channel carries:

* Batch jobs (discover, collect, import-parse, assess, health), with results uploaded in chunks
  that survive a dropped connection without being stored or run twice.
* Interactive requests: Connect to device (YAN-1739) and On site live view (YAN-1671). An
  engineer is waiting, so a request should reach the collector in about a second, and live view
  answers are relayed in memory only (ADR-0005).
* Device credentials inside jobs, in Hosted mode (ADR-0003).

What breaks at customer edges:

* Explicit proxies often close idle connections after 30 to 120 seconds, handle WebSocket
  upgrades badly or not at all, and some require NTLM or Kerberos auth.
* TLS-inspecting proxies (FortiGate deep inspection, Palo Alto decryption, Zscaler) end the TLS
  session and open a new one with their own certificate. **A client certificate can't pass
  through them, so mTLS fails under inspection** unless the customer exempts our hostname. Many
  also downgrade HTTP/2 to HTTP/1.1, which breaks gRPC.

## Decision

### 1. Jobs are delivered by long-poll over HTTPS (decided by Yandy)

* The collector asks for work with a request the controller holds open until a job is queued, or
  answers empty after at most 25 seconds (proposed), shorter than common proxy idle timeouts.
  The collector asks again at once.
* Progress, results and live view answers are ordinary HTTPS POSTs from the collector.
* Everything is plain HTTP/1.1 request and response, so it works through explicit and
  TLS-inspecting proxies with one code path.
* **Proposed:** on the controller, an async Django view served by an ASGI server (uvicorn). A
  queued job wakes the waiting request through PostgreSQL `LISTEN`/`NOTIFY`, so no new service
  (Redis, broker) is needed.
* **Proposed:** WebSocket may be added later as an optional faster path for clean networks,
  behind the same job API. It is not part of v1.

### 2. Collectors sign every request; no mTLS (decided by Yandy)

* At enrollment the collector makes an Ed25519 key pair and sends only the public key with its
  one-time token. The private key never leaves the collector host.
* Every request carries a short-lived token signed with that key. **Proposed:** it covers the
  collector id, method, path, a hash of the body, a timestamp and a nonce. The controller rejects
  tokens outside a short clock window and nonces it has already seen.
* The controller checks that the collector isn't revoked on every request. A long-poll is a new
  request at least every 25 seconds, so revoking a collector cuts it off within seconds, with no
  certificate revocation list.
* Normal server-authenticated TLS stays underneath. The collector trusts the operating system's
  certificate store (proposed: the `truststore` library, MIT), so a customer's inspection CA
  that's already installed on the VM or laptop works.
* **Proposed, pinning detects inspection:** the collector pins the controller's server key. If it
  sees a different certificate it keeps working but reports the path as TLS-inspected at
  check-in, and the Collectors screen shows it.
* **Proposed, credentials are sealed to the collector:** credentials in a job are encrypted to
  the collector's public key, so an inspecting proxy never sees a device password. Configs are
  already masked before they leave the collector (ADR-0003).
* **Proposed:** a per-customer setting, "Require uninspected path", makes that customer's
  collectors refuse to run jobs when they detect inspection.

### Proxy support

* An explicit proxy is set in the collector config or read from `HTTPS_PROXY`. Basic proxy auth
  works.
* NTLM and Kerberos proxy auth are not in v1. Customers allowlist the collector host instead,
  and the onboarding checklist says so. It gets its own Linear issue if a pilot customer needs it.

### Replay and reconnect

ADR-0001's offline replay contract stands: the controller gives every job an id, every result
carries its job id, the collector id and a result id the collector makes once, and the
controller acknowledges by result id and never stores one twice. **Proposed:** the collector
keeps a local journal of job ids it has started, so a job redelivered after a reconnect isn't run
again. Uploads are chunked, and each chunk has an index and a digest, so an upload resumes from
the last acknowledged chunk.

## How it is proven

* **In CI:** the prototype collector reaches the controller directly, through an explicit proxy,
  and through a TLS-inspecting proxy (mitmproxy in regular mode, and with its own CA). A
  reconnect test drops the connection mid-job and mid-upload and checks that the job runs once
  and the result is stored once.
* **In the lab (YAN-1647):** one manual run through a FortiGate with deep inspection, recorded on
  YAN-1640.

## Consequences

* YAN-1650 enrolls a collector key pair instead of issuing a client certificate. "Private key
  never leaves the collector host" and "revoking cuts it off at the next connection" still hold.
* The bearer secret in `cartio_core/protocol.py` is replaced by signed requests in YAN-1650.
* The controller runs under an ASGI server for the long-poll endpoint.
* We own a small request-signing scheme instead of leaning on nginx client-certificate checks.
  It needs careful tests: replayed tokens, wrong body hash, expired timestamps, revoked
  collectors and another customer's collector.
* Under TLS inspection the customer's proxy can read job metadata and masked results, but not
  credentials. The customer-facing wording says so, and "Require uninspected path" is the answer
  for customers who object.

## Alternatives considered

* **WebSocket with long-poll fallback:** lowest latency, but two transports to build and test,
  and it needs Django Channels plus Redis or a companion service.
* **gRPC bidirectional stream:** lowest latency, but breaks under most TLS inspection and adds a
  gRPC server and a protobuf toolchain.
* **mTLS only:** standard, but every customer with TLS inspection has to exempt our hostname
  before the collector works, and revocation needs a CRL at the TLS terminator.
* **mTLS with a signed-token fallback:** works everywhere, but two auth paths to build and secure,
  and the fallback is the one inspected customers actually use.
