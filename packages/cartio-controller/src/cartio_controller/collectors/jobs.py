"""The job channel (YAN-1651, ADR-0009): queue work for a collector, hand it over, take results.

A job gets its collection run when it is queued, and the run's result when it ends, so every
job leaves a run record and audit events whatever happens to it. Results are idempotent: a
chunk index and a result id are each stored once, so a collector that replays after a dropped
connection never duplicates anything.
"""

import hashlib
import json
import uuid

from cartio_core import sealing
from cartio_core.protocol import MAX_CHUNK_BYTES, MAX_CHUNKS, job_seal_context
from cartio_core.protocol import Job as JobMessage
from cartio_core.protocol import JobResult as ResultMessage
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from . import audit
from .credentials import CredentialNotAvailable, deliver_credentials
from .models import (
    AuditEvent,
    CollectionRunResult,
    Collector,
    Credential,
    Engagement,
    Job,
    JobChunk,
)
from .runs import finish_run, start_run
from .vault import VaultError


class JobError(ValueError):
    """The job can't be queued as asked."""


class JobClosed(Exception):
    """The job already ended (done, failed or cancelled), so the request no longer applies."""

    def __init__(self, job: Job) -> None:
        super().__init__(f"This job is {job.get_status_display().lower()}.")
        self.job = job


class ChunkConflict(Exception):
    """A different chunk was already stored at this index."""


class ResultIncomplete(Exception):
    """The result names chunks the controller doesn't have, or doesn't match their digest."""

    def __init__(self, message: str, missing: list[int] | None = None) -> None:
        super().__init__(message)
        self.missing = missing or []


def channel(collector_id) -> str:
    """The PostgreSQL notification channel a collector's job poll listens on."""
    return f"cartio_jobs_{uuid.UUID(str(collector_id)).hex}"


def _notify(collector_id) -> None:
    # Sent at commit, so the waiting poll sees the job. SQLite polls instead.
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_notify(%s, '')", [channel(collector_id)])


def queue_job(
    collector: Collector,
    job_type: str,
    actor: str,
    *,
    engagement: Engagement | None = None,
    params: dict | None = None,
    credential_ids=(),
) -> Job:
    """Queue a job for `collector`. Raises JobError, or `runs.RunNotAllowed`."""
    if job_type not in Job.Type.values:
        raise JobError(f"Unknown job type {job_type!r}.")
    params = params or {}
    if len(json.dumps(params)) > Job.MAX_PARAMS_BYTES:
        raise JobError("The job's settings are too large.")
    try:
        ids = list(dict.fromkeys(str(uuid.UUID(str(c))) for c in credential_ids))
    except ValueError as exc:
        raise JobError("One or more credentials aren't available.") from exc
    with transaction.atomic():
        # Another customer's credential looks the same as a made-up one.
        if collector.scoped(Credential).filter(pk__in=ids).count() != len(ids):
            raise JobError("One or more credentials aren't available.")
        run = start_run(collector, job_type, started_by=actor, engagement=engagement)
        job = collector.scoped(Job).create(
            type=job_type,
            collector=collector,
            engagement=engagement,
            run=run,
            params=params,
            credential_ids=ids,
            created_by=actor,
        )
        _notify(collector.pk)
    return job


def _end(job: Job, status: str, outcome: str, error: str = "", device_count: int = 0) -> None:
    """Close `job` and record its run's result. Call inside a transaction, with `job` locked."""
    job.status = status
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at"])
    finish_run(job.run, outcome, device_count=device_count, error_summary=error)


def cancel_job(job: Job, actor: str) -> Job:
    """Cancel a queued or running job. A running collector stops at its next report."""
    with transaction.atomic():
        job = _locked(job)
        if not job.is_open:
            raise JobClosed(job)
        _end(
            job,
            Job.Status.CANCELLED,
            CollectionRunResult.Outcome.CANCELLED,
            f"Cancelled by {actor}.",
        )
        audit.record(
            job.customer_id,
            AuditEvent.Action.JOB_CANCELLED,
            actor=actor,
            collector=job.collector,
            object_type="job",
            object_id=job.pk,
            object_name=job.type,
        )
        _notify(job.collector_id)
    return job


def _locked(job: Job) -> Job:
    return (
        Job.objects.for_customer(job.customer_id)
        .select_for_update()
        .select_related("collector", "run")
        .get(pk=job.pk)
    )


def next_job(collector: Collector, tls_inspected: bool = False) -> dict | None:
    """The next job message for `collector`, or None.

    A running job comes back again until it ends: if the answer that carried it was lost, the
    collector gets it on its next poll, and its journal keeps it from running a job twice.
    """
    if tls_inspected and collector.customer.require_uninspected_path:
        # The collector refuses too; holding the job back keeps it queued, not failed.
        return None
    while True:
        with transaction.atomic():
            job = (
                collector.scoped(Job)
                .select_for_update(skip_locked=True)
                .filter(collector=collector, status__in=Job.OPEN)
                .select_related("run")
                .order_by("created_at")
                .first()
            )
            if job is None:
                return None
            if job.status == Job.Status.QUEUED:
                job.status = Job.Status.RUNNING
                job.started_at = timezone.now()
                job.save(update_fields=["status", "started_at"])
            try:
                return _message(collector, job)
            except (CredentialNotAvailable, VaultError, ImproperlyConfigured) as exc:
                error = str(exc) if isinstance(exc, CredentialNotAvailable) else ""
                _end(
                    job,
                    Job.Status.FAILED,
                    CollectionRunResult.Outcome.FAILED,
                    error or "The controller couldn't decrypt the job's credentials.",
                )
        # That job failed; try the next one.


@sensitive_variables("delivered")
def _message(collector: Collector, job: Job) -> dict:
    sealed = ""
    if job.credential_ids:
        if not collector.seal_key:
            raise CredentialNotAvailable(
                "The collector enrolled without a sealing key. Enroll it again to send it "
                "credentials."
            )
        delivered = deliver_credentials(collector, job.credential_ids, job=str(job.pk))
        sealed = sealing.seal(
            collector.seal_key, json.dumps(delivered).encode(), job_seal_context(str(job.pk))
        )
    return JobMessage(
        id=str(job.pk),
        type=job.type,
        engagement=job.engagement_id,
        params=job.params,
        credentials=sealed,
    ).to_dict()


def collector_job(collector: Collector, job_id: str) -> Job | None:
    """`collector`'s own job with this id. Another collector's or customer's looks missing."""
    try:
        pk = uuid.UUID(job_id)
    except ValueError:
        return None
    return collector.scoped(Job).filter(pk=pk, collector=collector).first()


def record_progress(job: Job, message: str, percent: int | None) -> bool:
    """Store progress. Returns False if an engineer cancelled the job meanwhile."""
    with transaction.atomic():
        job = _locked(job)
        if not job.is_open:
            if job.status == Job.Status.CANCELLED:
                return False
            raise JobClosed(job)
        job.progress_message = message.replace("\x00", "")[:200]
        job.progress_percent = percent
        job.save(update_fields=["progress_message", "progress_percent"])
    return True


def store_chunk(job: Job, index: int, data: bytes) -> str:
    """Store one chunk of `job`'s result and return its digest. Storing it again is a no-op."""
    if not 0 <= index < MAX_CHUNKS:
        raise ResultIncomplete(f"A chunk index is 0 to {MAX_CHUNKS - 1}.")
    if len(data) > MAX_CHUNK_BYTES:
        raise ResultIncomplete(f"A chunk is at most {MAX_CHUNK_BYTES} bytes.")
    digest = hashlib.sha256(data).hexdigest()
    with transaction.atomic():
        job = _locked(job)
        chunks = JobChunk.objects.for_customer(job.customer_id)
        stored = chunks.filter(job=job, index=index).values_list("sha256", flat=True).first()
        if stored is not None:
            if stored != digest:
                raise ChunkConflict(f"Chunk {index} was already stored with other content.")
            return digest
        if not job.is_open:
            raise JobClosed(job)
        try:
            with transaction.atomic():
                chunks.create(job=job, index=index, sha256=digest, data=data)
        except IntegrityError as exc:
            raise ChunkConflict(f"Chunk {index} was stored meanwhile.") from exc
    return digest


def finish_job(job: Job, result: ResultMessage) -> Job:
    """Record how `job` ended. Sending the same result id again is a no-op."""
    try:
        result_id = uuid.UUID(result.result_id)
    except ValueError as exc:
        raise ResultIncomplete("The result id isn't a UUID.") from exc
    with transaction.atomic():
        job = _locked(job)
        if job.result_id is not None:
            if job.result_id == result_id:
                return job
            raise JobClosed(job)
        if not job.is_open:
            raise JobClosed(job)
        chunks = list(
            JobChunk.objects.for_customer(job.customer_id)
            .filter(job=job)
            .order_by("index")
            .values_list("index", "sha256", "data")
        )
        indexes = {index for index, _, _ in chunks}
        missing = [i for i in range(result.chunks) if i not in indexes]
        if missing:
            raise ResultIncomplete("Some chunks of the result haven't arrived.", missing)
        if len(chunks) != result.chunks:
            raise ResultIncomplete("The controller has more chunks than the result names.")
        whole = hashlib.sha256()
        size = 0
        for _, _, data in chunks:
            whole.update(bytes(data))
            size += len(data)
        if result.chunks and whole.hexdigest() != result.sha256:
            raise ResultIncomplete("The result doesn't match the chunks that arrived.")
        job.result_id = result_id
        job.result_sha256 = whole.hexdigest() if result.chunks else ""
        job.result_size = size
        job.save(update_fields=["result_id", "result_sha256", "result_size"])
        failed = result.outcome == CollectionRunResult.Outcome.FAILED
        _end(
            job,
            Job.Status.FAILED if failed else Job.Status.DONE,
            result.outcome,
            result.error_summary,
            result.device_count,
        )
    return job
