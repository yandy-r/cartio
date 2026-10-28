"""The audit log (YAN-1654): writing events, recording sign-ins, exporting a customer's log.

Events are written in the same transaction as the change they describe, so there is no change
without a record of it.
"""

import csv
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.core.serializers.json import DjangoJSONEncoder
from django.dispatch import receiver

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


def _spreadsheet_safe(value: str) -> str:
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
        yield writer.writerow([_spreadsheet_safe(row[name]) for name in EXPORT_FIELDS])


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
    _login_event(LoginEvent.Kind.LOGGED_OUT, request, user.get_username() if user else "")


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
