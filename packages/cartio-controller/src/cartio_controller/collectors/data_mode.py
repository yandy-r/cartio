"""Changing a customer's data mode (YAN-1712, ADR-0002): the one way it changes after creation.

Hosted to On site moves the customer's network data off the controller in three steps:

1. `start_on_site` builds an export of the network data the controller holds for the customer,
   takes its checksum and queues an `export` job for one of the customer's collectors.
2. The collector fetches the export (`export_bytes`, checked against that checksum), writes it
   to the on-site store, reads it back and reports the store's checksum.
3. `finish_export` runs in the transaction that ends the job. It builds the export again from
   what the controller holds now; only if the store's checksum matches both that and the one the
   job was queued with does it switch the customer to On site and delete the controller's copy.
   Anything else (a store error, a mismatch, data that changed meanwhile) keeps every row, logs
   why, and fails the job, so the change can simply be started again.

Credentials and the customer's data key are deleted too, not exported: an On site customer's
credentials live on its collector, added there by an engineer (ADR-0003). Jobs, collection runs,
engagements, sites, collectors and the audit log stay: they hold no network data.

On site to Hosted moves nothing. The network data stays in the customer's store, and only what
is collected after the change reaches the controller. The customer has to agree to that, so the
action names who at the customer approved it, and the audit log keeps it.
"""

from __future__ import annotations

import hashlib
import json

from cartio_core.protocol import ExportReceipt, ProtocolError, canonical_json
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, models, transaction

from . import audit, jobs
from .db_guards import PURGE_FUNCTION, PURGEABLE_TABLES
from .models import (
    AssessmentRun,
    AuditEvent,
    CheckResult,
    CollectionRunResult,
    Collector,
    ConfigSnapshot,
    Credential,
    Customer,
    CustomerDataKey,
    Device,
    Engagement,
    Finding,
    Interface,
    Job,
    JobChunk,
    Link,
    RawOutput,
    RoutingNeighbor,
    Source,
)

EXPORT_FORMAT = "cartio-export/1"
# The network data the controller holds for a Hosted customer, in the order it is deleted:
# every record before the records it points at. The export lists them the other way round.
NETWORK_MODELS = (
    CheckResult,
    AssessmentRun,
    Finding,
    ConfigSnapshot,
    RawOutput,
    Link,
    RoutingNeighbor,
    Interface,
    Device,
    Source,
)
# Deleted, never exported: they are secrets, or only guard secrets (ADR-0003).
SECRET_MODELS = (Credential, CustomerDataKey)
# Job results that are the collector's own health or a store's receipt, not network data.
KEPT_RESULTS = (Job.Type.HEALTH, Job.Type.EXPORT)
MAX_CONFIRMED_BY = 150


class DataModeError(ValueError):
    """The change can't be made as asked. The message is for people."""


def _plain(value):
    if value is None or isinstance(value, str | bool | int | float | list | dict):
        return value
    return DjangoJSONEncoder().default(value)


def export_document(customer: Customer) -> dict:
    """Every network data record the controller holds for `customer`, as plain JSON."""
    tables = {}
    for model in reversed(NETWORK_MODELS):
        fields = [f for f in model._meta.concrete_fields if f.name != "customer"]
        rows = (
            model.objects.for_customer(customer)
            .order_by("pk")
            .values_list(*[f.attname for f in fields])
        )
        tables[model._meta.model_name] = [
            {f.attname: _plain(v) for f, v in zip(fields, row, strict=True)}
            for row in rows.iterator(chunk_size=2000)
        ]
    engagements = Engagement.objects.for_customer(customer).order_by("pk").values("id", "name")
    return {
        "format": EXPORT_FORMAT,
        "customer": {"id": customer.pk, "name": customer.name},
        "engagements": list(engagements),
        "tables": tables,
    }


def export_bytes(customer: Customer) -> bytes:
    """The export as the collector receives it. Its SHA-256 is the export's checksum."""
    return canonical_json(export_document(customer))


def _record(customer: Customer, action: str, actor: str, collector=None, **detail) -> AuditEvent:
    return audit.record(
        customer,
        action,
        actor=actor,
        collector=collector,
        object_type="customer",
        object_id=customer.pk,
        object_name=customer.name,
        **detail,
    )


def open_export(customer: Customer) -> Job | None:
    return (
        Job.objects.for_customer(customer)
        .filter(type=Job.Type.EXPORT, status__in=Job.OPEN)
        .order_by("created_at")
        .first()
    )


def start_on_site(
    customer: Customer, collector: Collector, actor: str, *, live_view_allowed: bool = False
) -> Job:
    """Start moving a Hosted customer On site: queue the export to `collector`'s store.

    Raises DataModeError, or `runs.RunNotAllowed` for a revoked collector or archived customer.
    """
    if collector.customer_id != customer.pk:
        raise DataModeError("No such collector for this customer.")
    with transaction.atomic():
        customer = Customer.objects.select_for_update().get(pk=customer.pk)
        if customer.is_on_site:
            raise DataModeError("This customer is already On site.")
        if open_export(customer) is not None:
            raise DataModeError(
                "A move On site is already under way. Wait for it, or cancel its export job."
            )
        if collector.enrolled_at is None:
            raise DataModeError("The collector hasn't enrolled yet.")
        data = export_bytes(customer)
        digest = hashlib.sha256(data).hexdigest()
        params = {
            "to": Customer.DataMode.ON_SITE,
            "sha256": digest,
            "size": len(data),
            "live_view_allowed": bool(live_view_allowed),
        }
        job = jobs.queue_job(collector, Job.Type.EXPORT, actor, params=params)
        _record(
            customer,
            AuditEvent.Action.DATA_MODE_CHANGE_STARTED,
            actor,
            collector,
            job=str(job.pk),
            sha256=digest,
            size=len(data),
            live_view_allowed=params["live_view_allowed"],
            **{"from": customer.data_mode, "to": Customer.DataMode.ON_SITE},
        )
    return job


def finish_export(job: Job, payload: bytes, outcome: str, error: str) -> tuple[str, str]:
    """Switch the customer On site and delete the controller's copy, if the store confirmed it.

    Called by `jobs.finish_job` in the transaction that ends the export job, with the job
    locked. Returns the outcome and error to record for the job.
    """
    customer = Customer.objects.select_for_update().get(pk=job.customer_id)
    modes = {"from": Customer.DataMode.HOSTED, "to": Customer.DataMode.ON_SITE}

    def fail(reason: str) -> tuple[str, str]:
        reason = f"{reason} The controller kept its copy; start the change again to retry."
        _record(
            customer,
            AuditEvent.Action.DATA_MODE_CHANGE_FAILED,
            job.created_by,
            job.collector,
            job=str(job.pk),
            reason=reason,
            **modes,
        )
        return CollectionRunResult.Outcome.FAILED, reason[: CollectionRunResult.MAX_ERROR_SUMMARY]

    if outcome != CollectionRunResult.Outcome.SUCCEEDED:
        return fail(error or "The collector couldn't store the export.")
    if customer.is_on_site:
        return fail("The customer is already On site.")
    try:
        receipt = ExportReceipt.from_dict(json.loads(payload))
    except (ValueError, ProtocolError, TypeError):
        return fail("The collector's receipt isn't readable.")
    if receipt.sha256 != job.params.get("sha256"):
        return fail("The on-site store's checksum doesn't match the export.")
    if hashlib.sha256(export_bytes(customer)).hexdigest() != receipt.sha256:
        return fail("The customer's data changed on the controller after the export was made.")

    counts = _counts(customer)
    customer._set_data_mode(
        Customer.DataMode.ON_SITE, live_view_allowed=job.params.get("live_view_allowed", False)
    )
    event = _record(
        customer,
        AuditEvent.Action.DATA_MODE_CHANGED,
        job.created_by,
        job.collector,
        job=str(job.pk),
        sha256=receipt.sha256,
        live_view_allowed=customer.live_view_allowed,
        deleted=counts,
        **modes,
    )
    _purge(customer, event)
    return CollectionRunResult.Outcome.SUCCEEDED, ""


def _chunks(customer: Customer):
    return JobChunk.objects.for_customer(customer).exclude(job__type__in=KEPT_RESULTS)


def _counts(customer: Customer) -> dict[str, int]:
    counts = {
        model._meta.model_name: model.objects.for_customer(customer).count()
        for model in NETWORK_MODELS + SECRET_MODELS
    }
    counts["jobchunk"] = _chunks(customer).count()
    return {name: count for name, count in counts.items() if count}


def _purge(customer: Customer, event: AuditEvent) -> None:
    """Delete the customer's network data, credentials and data key. Call in the transaction
    that switched the customer On site and logged `event`."""
    for model in NETWORK_MODELS:
        table = model._meta.db_table
        if model.append_only and connection.vendor == "postgresql":
            if table not in PURGEABLE_TABLES:
                raise RuntimeError(f"{table} needs adding to db_guards.PURGEABLE_TABLES.")
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {PURGE_FUNCTION}(%s, %s, %s)", [table, customer.pk, event.pk]
                )
        else:
            # A plain queryset, past the customer-owned one that refuses append-only deletes.
            # PostgreSQL refuses those itself; SQLite is for local development only.
            models.QuerySet(model).filter(customer=customer).delete()
    _chunks(customer).delete()
    for model in SECRET_MODELS:
        model.objects.for_customer(customer).delete()


def move_hosted(customer: Customer, actor: str, confirmed_by: str) -> Customer:
    """Switch an On site customer to Hosted. Nothing moves: see the module docstring."""
    confirmed_by = confirmed_by.strip()
    if not confirmed_by:
        raise DataModeError(
            "Name who at the customer approved keeping their network data on the controller."
        )
    if len(confirmed_by) > MAX_CONFIRMED_BY:
        raise DataModeError(f"Keep the approver to {MAX_CONFIRMED_BY} characters.")
    with transaction.atomic():
        customer = Customer.objects.select_for_update().get(pk=customer.pk)
        if not customer.is_on_site:
            raise DataModeError("This customer is already Hosted.")
        customer._set_data_mode(Customer.DataMode.HOSTED, live_view_allowed=False)
        _record(
            customer,
            AuditEvent.Action.DATA_MODE_CHANGED,
            actor,
            confirmed_by=confirmed_by,
            moved="nothing: data collected while On site stays in the customer's store",
            **{"from": Customer.DataMode.ON_SITE, "to": Customer.DataMode.HOSTED},
        )
    return customer
