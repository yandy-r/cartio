"""The audit log (YAN-1654): writing events, recording sign-ins, exporting a customer's log.

Events are written in the same transaction as the change they describe, so there is no change
without a record of it.
"""

import csv
import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.dispatch import receiver
from django.utils import timezone

from .models import AuditEvent, LoginEvent

EXPORT_FIELDS = [
    "at",
    "action",
    "actor",
    "collector",
    "object_type",
    "object_id",
    "object_name",
    "detail",
]


def record(
    customer,
    action: str,
    *,
    object_type: str,
    object_id,
    object_name: str = "",
    actor: str = "",
    collector=None,
    **detail,
) -> AuditEvent:
    """Add an event to `customer`'s audit log. `detail` must never hold a secret."""
    return AuditEvent.objects.for_customer(customer).create(
        action=action,
        actor=actor,
        collector=collector,
        object_type=object_type,
        object_id=str(object_id),
        object_name=object_name[:200],
        detail=detail,
    )


def _rows(customer):
    events = AuditEvent.objects.for_customer(customer).order_by("at", "pk")
    for event in events.iterator(chunk_size=2000):
        yield {
            "at": event.at.isoformat(),
            "action": event.action,
            "actor": event.actor,
            "collector": str(event.collector_id or ""),
            "object_type": event.object_type,
            "object_id": event.object_id,
            "object_name": event.object_name,
            "detail": event.detail,
        }


def spreadsheet_safe(value: str) -> str:
    # A cell starting with one of these runs as a formula in a spreadsheet (CSV injection).
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


class _Line:
    """A file-like target for csv.writer that hands back each line instead of storing it."""

    def write(self, value: str) -> str:
        return value


def _csv_lines(customer):
    writer = csv.writer(_Line())
    yield writer.writerow(EXPORT_FIELDS)
    for row in _rows(customer):
        row["detail"] = json.dumps(row["detail"], sort_keys=True)
        yield writer.writerow([spreadsheet_safe(row[name]) for name in EXPORT_FIELDS])


def _json_items(customer):
    encoder = DjangoJSONEncoder()
    yield "["
    for index, row in enumerate(_rows(customer)):
        yield ("," if index else "") + "\n  " + encoder.encode(row)
    yield "\n]\n"


def export_events(customer, actor: str, fmt: str):
    """`customer`'s whole audit log as CSV or JSON, oldest first, streamed in chunks.

    The export itself is logged first, so it appears in its own file.
    """
    if fmt not in ("csv", "json"):
        raise ValueError("Export as csv or json.")
    record(
        customer,
        AuditEvent.Action.AUDIT_EXPORTED,
        actor=actor,
        object_type="customer",
        object_id=customer.pk,
        object_name=customer.name,
        format=fmt,
    )
    return _csv_lines(customer) if fmt == "csv" else _json_items(customer)


def streamed(request, chunks):
    """`chunks` in the form the server streams without reading them all into memory first.

    Under ASGI (uvicorn), Django would consume a plain iterator whole before sending it, so it
    gets an async one that pulls each chunk in the thread the database work runs in.
    """
    from asgiref.sync import sync_to_async
    from django.core.handlers.asgi import ASGIRequest

    if not isinstance(getattr(request, "_request", request), ASGIRequest):
        return chunks
    iterator = iter(chunks)
    pull = sync_to_async(next, thread_sensitive=True)

    async def stream():
        while (chunk := await pull(iterator, None)) is not None:
            yield chunk

    return stream()


def _ip(request) -> str | None:
    if request is None:
        return None
    # The direct peer. Behind a proxy this is the proxy's address.
    return request.META.get("REMOTE_ADDR") or None


def _login_event(kind: str, request, username: str) -> None:
    LoginEvent.objects.create(kind=kind, username=username[:150], ip_address=_ip(request))


@receiver(user_logged_in, dispatch_uid="cartio_audit_logged_in")
def _logged_in(sender, request, user, **kwargs):
    _login_event(LoginEvent.Kind.LOGGED_IN, request, user.get_username())


@receiver(user_logged_out, dispatch_uid="cartio_audit_logged_out")
def _logged_out(sender, request, user, **kwargs):
    # Signing out when nobody is signed in is a no-op, not an event: anyone can post it.
    if user is None:
        return
    _login_event(LoginEvent.Kind.LOGGED_OUT, request, user.get_username())


@receiver(user_login_failed, dispatch_uid="cartio_audit_login_failed")
def _login_failed(sender, credentials, request=None, **kwargs):
    # The username only when it names a real account. People sometimes type their password
    # into the username box, and this record can never be deleted.
    username = credentials.get(get_user_model().USERNAME_FIELD, "")
    if (
        not isinstance(username, str)
        or not (
            get_user_model()._default_manager.filter(**{get_user_model().USERNAME_FIELD: username})
        ).exists()
    ):
        username = ""
    _login_event(LoginEvent.Kind.FAILED, request, username)


def too_many_failed_sign_ins(request, username: str) -> bool:
    """Whether this account or client address used up its failed sign-ins for now.

    Counted from the sign-in log over the last SIGN_IN_LOCKOUT_MINUTES, so the limit holds across
    controller processes and lifts on its own. Failures are logged with a username only when it
    names a real account, so a made-up username is limited by its address alone.
    """
    failed = LoginEvent.objects.filter(
        kind=LoginEvent.Kind.FAILED,
        at__gte=timezone.now() - timedelta(minutes=settings.SIGN_IN_LOCKOUT_MINUTES),
    )
    per_account = settings.SIGN_IN_MAX_FAILURES
    if per_account > 0 and username and failed.filter(username=username).count() >= per_account:
        return True
    per_address = settings.SIGN_IN_MAX_FAILURES_PER_ADDRESS
    address = _ip(request)
    return (
        per_address > 0
        and address is not None
        and failed.filter(ip_address=address).count() >= per_address
    )


def purge_login_events(older_than_days: int) -> int:
    """Delete sign-in log entries older than the given number of days. Returns how many.

    The sign-in log is append-only through the ORM. Retention is the one way out of it, and it
    only ever takes whole days off the old end, never anything the sign-in limits still count.
    """
    keep = timedelta(days=older_than_days)
    if older_than_days < 1 or keep < timedelta(minutes=settings.SIGN_IN_LOCKOUT_MINUTES):
        raise ValueError("Keep at least a day of the sign-in log, and no less than the lockout.")
    with transaction.atomic():
        # A plain queryset, past the append-only one that refuses deletes.
        deleted, _ = models.QuerySet(LoginEvent).filter(at__lt=timezone.now() - keep).delete()
    return deleted
