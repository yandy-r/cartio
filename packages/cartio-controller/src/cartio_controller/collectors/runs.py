"""Collection runs (YAN-1654): one append-only record of each job a collector runs.

The job channel (YAN-1651) calls `start_run` when it hands a job to a collector and
`finish_run` when the collector reports back. Both write to the customer's audit log in the same
transaction.
"""

from django.db import transaction

from . import audit
from .models import (
    AuditEvent,
    CollectionRun,
    CollectionRunResult,
    Collector,
    Customer,
    Engagement,
)


class RunNotAllowed(ValueError):
    """The run can't start: the customer or engagement is archived, or the collector isn't ready."""


def start_run(
    collector: Collector, job_type: str, started_by: str = "", engagement: Engagement | None = None
) -> CollectionRun:
    with transaction.atomic():
        # Lock the customer and engagement, so neither is archived while the run starts.
        if collector.enrolled_at is None:
            raise RunNotAllowed("The collector hasn't enrolled.")
        # Read under a lock, so a revoke that lands meanwhile wins (operations.revoke locks too).
        locked = Collector.all_customers.select_for_update().get(pk=collector.pk)
        if locked.revoked_at is not None:
            raise RunNotAllowed("The collector was revoked.")
        if Customer.objects.select_for_update().get(pk=collector.customer_id).is_archived:
            raise RunNotAllowed("This customer is archived.")
        if engagement is not None:
            locked = Engagement.all_customers.select_for_update().filter(pk=engagement.pk).first()
            if locked is None or locked.is_archived:
                raise RunNotAllowed("This engagement is archived.")
        # Scoped to the collector's customer, so another customer's engagement is refused.
        run = collector.scoped(CollectionRun).create(
            job_type=job_type, collector=collector, engagement=engagement, started_by=started_by
        )
        audit.record(
            run.customer_id,
            AuditEvent.Action.RUN_STARTED,
            actor=started_by,
            collector=collector,
            object_type="collection_run",
            object_id=run.pk,
            object_name=job_type,
            engagement=engagement.pk if engagement else None,
        )
    return run


def finish_run(
    run: CollectionRun, outcome: str, device_count: int = 0, error_summary: str = ""
) -> CollectionRunResult:
    """Record how `run` ended. A run finishes once; a second call raises `IntegrityError`."""
    if outcome not in CollectionRunResult.Outcome.values:
        raise ValueError(f"Unknown outcome {outcome!r}.")
    if not 0 <= device_count <= CollectionRunResult.MAX_DEVICE_COUNT:
        raise ValueError("The device count is out of range.")
    # PostgreSQL text can't hold NUL characters.
    error_summary = error_summary.replace("\x00", "")
    with transaction.atomic():
        result = CollectionRunResult.objects.for_customer(run.customer_id).create(
            run=run,
            outcome=outcome,
            device_count=device_count,
            error_summary=error_summary[: CollectionRunResult.MAX_ERROR_SUMMARY],
        )
        audit.record(
            run.customer_id,
            AuditEvent.Action.RUN_FINISHED,
            collector=run.collector,
            object_type="collection_run",
            object_id=run.pk,
            object_name=run.job_type,
            outcome=outcome,
            device_count=device_count,
        )
    return result
