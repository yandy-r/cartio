"""The collector-facing API. Collectors start every exchange (ADR-0001)."""

import asyncio
import contextlib
import json

from asgiref.sync import sync_to_async
from cartio_core.protocol import (
    MAX_WAIT,
    CheckIn,
    CheckInResponse,
    EnrollRequest,
    EnrollResponse,
    JobPoll,
    JobProgress,
    JobResult,
    JobStatus,
    ProtocolError,
)
from cartio_core.sealing import SealError
from cartio_core.sealing import check_public_key as check_seal_key
from cartio_core.signing import SignatureError, check_public_key
from django.conf import settings
from django.db import connection, transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import audit, jobs
from .auth import NotAuthenticated, signed_collector
from .models import AuditEvent, Collector, OnSiteDataError
from .models import CheckIn as CheckInRecord


def _error(status: int, message: str) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _json_body(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("body must be JSON") from exc
    if not isinstance(data, dict):
        raise ProtocolError("body must be a JSON object")
    return data


@csrf_exempt
@require_POST
def enroll(request: HttpRequest) -> JsonResponse:
    try:
        message = EnrollRequest.from_dict(_json_body(request))
        public_key = check_public_key(message.public_key)
        seal_key = check_seal_key(message.seal_key)
    except (ProtocolError, SignatureError, SealError) as exc:
        return _error(400, str(exc))

    with transaction.atomic():
        collector = Collector.for_enrollment_token(message.token)
        if collector is None:
            return _error(403, "unknown, used or expired enrollment token")
        # Lock the row and check again, so two enrollments with one token can't both win.
        collector = Collector.all_customers.select_for_update().get(pk=collector.pk)
        if not collector.enrollment_open():
            return _error(403, "unknown, used or expired enrollment token")
        collector.enrollment_token_hash = ""
        collector.enrollment_expires_at = None
        collector.public_key = public_key
        collector.seal_key = seal_key
        collector.enrolled_at = timezone.now()
        collector.hostname = message.hostname
        collector.save()
        audit.record(
            collector.customer_id,
            AuditEvent.Action.COLLECTOR_ENROLLED,
            collector=collector,
            object_type="collector",
            object_id=collector.pk,
            object_name=collector.name,
            hostname=message.hostname,
            public_key=public_key,
            seal_key=seal_key,
        )

    response = EnrollResponse(
        collector_id=str(collector.id),
        customer=collector.customer.name,
        tls_pins=list(settings.TLS_PINS),
    )
    return JsonResponse(response.to_dict(), status=201)


@csrf_exempt
@require_POST
def checkin(request: HttpRequest) -> JsonResponse:
    try:
        collector = signed_collector(request)
    except NotAuthenticated as exc:
        return _error(401, str(exc))
    try:
        message = CheckIn.from_dict(_json_body(request))
    except ProtocolError as exc:
        return _error(400, str(exc))

    # From here on, everything goes through the collector's own customer.
    now = timezone.now()
    collector.scoped(CheckInRecord).create(
        collector=collector,
        hostname=message.hostname,
        version=message.version,
        platform=message.platform,
        tls_inspected=message.tls_inspected,
    )
    collector.scoped(Collector).filter(pk=collector.pk).update(
        hostname=message.hostname,
        version=message.version,
        platform=message.platform,
        tls_inspected=message.tls_inspected,
        last_check_in=now,
    )
    return JsonResponse(CheckInResponse(status="ok").to_dict())


def _closed(job) -> JsonResponse:
    """The job ended on the controller (cancelled, most often), so the collector stops."""
    return JsonResponse(
        {
            "error": f"this job is {job.status}",
            "status": job.status,
            "cancelled": job.status == "cancelled",
        },
        status=409,
    )


@csrf_exempt
@require_POST
async def jobs_next(request: HttpRequest) -> JsonResponse:
    """Hand the collector its next job, holding the request open up to `wait` seconds for one.

    A queued job wakes the waiting poll through PostgreSQL LISTEN/NOTIFY, so no broker is
    needed (ADR-0009). Revocation is checked on every poll, and a poll lasts at most MAX_WAIT.
    """
    try:
        collector = await sync_to_async(signed_collector)(request)
    except NotAuthenticated as exc:
        return _error(401, str(exc))
    try:
        message = JobPoll.from_dict(_json_body(request))
    except ProtocolError as exc:
        return _error(400, str(exc))
    require = await sync_to_async(lambda: collector.customer.require_uninspected_path)()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(message.wait, MAX_WAIT)
    async with _wake_ups(collector.pk, listen=message.wait > 0) as wait_for_wake_up:
        while True:
            job = await sync_to_async(jobs.next_job)(collector, message.tls_inspected)
            remaining = deadline - loop.time()
            if job is not None or remaining <= 0:
                break
            await wait_for_wake_up(remaining)
    found = [job] if job is not None else []
    return JsonResponse({"jobs": found, "require_uninspected_path": require})


@contextlib.asynccontextmanager
async def _wake_ups(collector_id, listen=True):
    """Yields `wait(seconds)`, which returns when a job may be waiting for this collector."""
    if not listen or connection.vendor != "postgresql":
        # Local development on SQLite: look again every second.
        async def poll(seconds):
            await asyncio.sleep(min(seconds, 1))

        yield poll
        return

    import psycopg

    db = connection.settings_dict
    params = {
        "dbname": db["NAME"],
        "user": db.get("USER") or None,
        "password": db.get("PASSWORD") or None,
        "host": db.get("HOST") or None,
        "port": db.get("PORT") or None,
    }
    params.update(
        {
            k: v
            for k, v in db.get("OPTIONS", {}).items()
            if k in {"sslmode", "sslrootcert", "sslcert", "sslkey", "options"}
        }
    )
    params = {k: v for k, v in params.items() if v is not None}
    async with await psycopg.AsyncConnection.connect(autocommit=True, **params) as listener:
        # Listen before the first look for a job, so one queued in between still wakes us.
        await listener.execute(f'LISTEN "{jobs.channel(collector_id)}"')

        async def wait(seconds):
            async for _ in listener.notifies(timeout=seconds, stop_after=1):
                pass

        yield wait


def _job_request(request: HttpRequest, job_id: str, kind=None):
    """The signed collector's own job and the parsed message, or an error response."""
    try:
        collector = signed_collector(request)
    except NotAuthenticated as exc:
        return None, None, _error(401, str(exc))
    job = jobs.collector_job(collector, job_id)
    if job is None:
        return None, None, _error(404, "no such job for this collector")
    if kind is None:
        return job, None, None
    try:
        return job, kind.from_dict(_json_body(request)), None
    except ProtocolError as exc:
        return None, None, _error(400, str(exc))


@csrf_exempt
@require_POST
def job_progress(request: HttpRequest, job_id: str) -> JsonResponse:
    job, message, error = _job_request(request, job_id, JobProgress)
    if error:
        return error
    try:
        open_ = jobs.record_progress(job, message.message, message.percent)
    except jobs.JobClosed as exc:
        return _closed(exc.job)
    return JsonResponse(JobStatus(cancelled=not open_).to_dict())


@csrf_exempt
@require_POST
def job_chunk(request: HttpRequest, job_id: str, index: int) -> JsonResponse:
    """One chunk of a job's result, as the raw request body."""
    job, _, error = _job_request(request, job_id)
    if error:
        return error
    try:
        digest = jobs.store_chunk(job, index, request.body)
    except jobs.JobClosed as exc:
        return _closed(exc.job)
    except jobs.ChunkConflict as exc:
        return _error(409, str(exc))
    except jobs.ResultIncomplete as exc:
        return _error(400, str(exc))
    except OnSiteDataError as exc:
        return _error(409, str(exc))
    return JsonResponse({"index": index, "sha256": digest})


@csrf_exempt
@require_POST
def job_result(request: HttpRequest, job_id: str) -> JsonResponse:
    job, message, error = _job_request(request, job_id, JobResult)
    if error:
        return error
    try:
        job = jobs.finish_job(job, message)
    except jobs.JobClosed as exc:
        return _closed(exc.job)
    except jobs.ResultIncomplete as exc:
        return JsonResponse({"error": str(exc), "missing": exc.missing}, status=409)
    return JsonResponse({"status": job.status, "result_id": str(job.result_id)})
