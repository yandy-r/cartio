"""The collector-facing API. Collectors start every exchange (ADR-0001)."""

import json

from cartio_core.protocol import (
    CheckIn,
    CheckInResponse,
    EnrollRequest,
    EnrollResponse,
    ProtocolError,
)
from cartio_core.signing import SignatureError, check_public_key
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import audit
from .auth import NotAuthenticated, signed_collector
from .models import AuditEvent, Collector
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
    except (ProtocolError, SignatureError) as exc:
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
