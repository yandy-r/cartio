"""Collection runs (YAN-1654): one append-only record of each job a collector runs.

The job channel (YAN-1651) calls `start_run` when it hands a job to a collector and
`finish_run` when the collector reports back. Both write to the customer's audit log in the same
transaction.
"""

from django.db import transaction

from . import audit
from .models import AuditEvent, CollectionRun, CollectionRunResult, Collector, Engagement


class RunNotAllowed(ValueError):
    """The run can't start: the customer or engagement is archived, or the collector isn't ready."""


def start_run(
    collector: Collector, job_type: str, started_by: str = "", engagement: Engagement | None = None
) -> CollectionRun:
    if collector.enrolled_at is None:
        raise RunNotAllowed("The collector hasn't enrolled.")
    if collector.customer.is_archived:
        raise RunNotAllowed("This customer is archived.")
    if engagement is not None and engagement.is_archived:
        raise RunNotAllowed("This engagement is archived.")
    with transaction.atomic():
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
    if device_count < 0:
        raise ValueError("The device count can't be negative.")
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
