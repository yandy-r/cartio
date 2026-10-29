"""Collect jobs (YAN-1753): what a collector is asked to collect, and storing what it sends back.

A collect job names SSH targets (host, platform, credential) in an engagement. The collector
masks every output and config with the customer's masking key, which reaches it only sealed
inside the job, and uploads one `CollectResult`.

When the job finishes, everything in the result is scanned before anything is stored. A secret
that wasn't masked, or a placeholder from another key version, refuses the whole result: the
refusal is audit-logged with line numbers and kinds only, and the job fails. Otherwise the
devices, outputs and configs are stored under one live-run `Source`, in the transaction that
ends the job, so a result is stored whole or not at all.
"""

from __future__ import annotations

import ipaddress
import json
import uuid

from cartio_core.masking import find_unmasked, placeholder_versions
from cartio_core.protocol import (
    COLLECT_PLATFORMS,
    MAX_COLLECT_TARGETS,
    CollectResult,
    ProtocolError,
)
from django.db import transaction

from . import audit
from .inventory import store_config, store_outputs
from .models import (
    MASKING_KEY_VERSION,
    AuditEvent,
    Credential,
    Customer,
    Job,
    Source,
)

# The credentials a collect job can log in with.
SSH_KINDS = (Credential.Kind.SSH_PASSWORD, Credential.Kind.SSH_KEY)


class ResultRefused(ValueError):
    """The result can't be stored. The message is for people: never device output."""


def collect_params(customer: Customer, engagement, params: dict) -> dict:
    """Check a collect job's settings and return them cleaned. Raises ValueError."""
    if engagement is None:
        raise ValueError("A collect job needs an engagement.")
    if customer.data_mode == Customer.DataMode.ON_SITE:
        raise ValueError(
            "This customer is On site: its collector stores device data in the customer's own "
            "store, not on the controller."
        )
    targets = params.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_COLLECT_TARGETS:
        raise ValueError(f"A collect job needs 1 to {MAX_COLLECT_TARGETS} targets.")
    cleaned = []
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("Each target needs a host, a platform and a credential.")
        host = target.get("host")
        if not isinstance(host, str) or not 0 < len(host) <= 253 or host != host.strip():
            raise ValueError("Each target needs a host name or address.")
        if any(c.isspace() or c in "@/;" for c in host):
            raise ValueError(f"{host!r} isn't a host name or address.")
        platform = target.get("platform")
        if platform not in COLLECT_PLATFORMS:
            raise ValueError(f"The platform must be one of {', '.join(COLLECT_PLATFORMS)}.")
        port = target.get("port", 22)
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            raise ValueError("A port is 1 to 65535.")
        try:
            credential = str(uuid.UUID(str(target.get("credential"))))
        except ValueError as exc:
            raise ValueError("Each target needs a credential.") from exc
        cleaned.append({"host": host, "platform": platform, "port": port, "credential": credential})
    verify = params.get("verify_host_keys", True)
    if not isinstance(verify, bool):
        raise ValueError("verify_host_keys is true or false.")
    return {"targets": cleaned, "verify_host_keys": verify}


def target_credentials(customer: Customer, params: dict) -> list[str]:
    """The credential ids a collect job's targets use. All must be the customer's SSH ones."""
    ids = list(dict.fromkeys(t["credential"] for t in params["targets"]))
    found = Credential.objects.for_customer(customer).filter(pk__in=ids, kind__in=SSH_KINDS)
    if found.count() != len(ids):
        # Another customer's credential looks the same as a made-up one.
        raise ValueError("One or more credentials aren't available SSH credentials.")
    return ids


def read_result(data: bytes) -> CollectResult:
    try:
        return CollectResult.from_dict(json.loads(data))
    except (ValueError, ProtocolError, TypeError) as exc:
        raise ResultRefused("The collect result isn't readable.") from exc


def check_result(job: Job, result: CollectResult) -> None:
    """Refuse a result with anything unmasked, or masked with another key. Logs a refusal.

    Call inside the transaction that ends the job, so the refusal is logged with it.
    """
    if result.key_version != MASKING_KEY_VERSION:
        raise ResultRefused("The result was masked with another masking key version.")
    for device in result.devices:
        texts = [(command, text) for command, text in device.outputs.items()]
        texts.append(("", device.config))
        for command, text in texts:
            if found := find_unmasked(text, device.platform):
                action = (
                    AuditEvent.Action.OUTPUTS_REJECTED
                    if command
                    else AuditEvent.Action.CONFIG_REJECTED
                )
                # Line numbers and kinds only: the line itself would put the secret in the log.
                audit.record(
                    job.customer_id,
                    action,
                    collector=job.collector,
                    object_type="job",
                    object_id=job.pk,
                    object_name=device.host,
                    lines=[f.line for f in found],
                    kinds=sorted({f.kind for f in found}),
                    **({"command": command} if command else {}),
                )
                raise ResultRefused(f"The result for {device.host} has an unmasked secret.")
            if placeholder_versions(text) - {result.key_version}:
                raise ResultRefused(
                    f"The result for {device.host} has placeholders from another masking key "
                    "version."
                )


def store_result(job: Job, result: CollectResult) -> int:
    """Store a checked result under one live-run source. Returns how many devices it stored."""
    with transaction.atomic():
        source = Source.objects.for_customer(job.customer_id).create(
            engagement=job.engagement,
            kind=Source.Kind.LIVE_RUN,
            run=job.run,
            name=f"Collect job {job.pk}",
            added_by=job.created_by,
        )
        for collected in result.devices:
            device = store_outputs(
                source,
                collected.platform,
                collected.outputs,
                management_ip=_address(collected.host),
                actor=job.created_by,
            )
            store_config(
                source,
                device,
                collected.platform,
                collected.config,
                key_version=result.key_version,
                actor=job.created_by,
            )
    return len(result.devices)


def _address(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None
