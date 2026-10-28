"""The audit log (YAN-1654): writing events, recording sign-ins, exporting a customer's log.

Events are written in the same transaction as the change they describe, so there is no change
without a record of it.
"""

import csv
import io
import json

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
    for event in events.iterator():
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


def export_events(customer, actor: str, fmt: str) -> str:
    """`customer`'s whole audit log as CSV or JSON, oldest first. The export is logged too."""
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
    if fmt == "json":
        return json.dumps(list(_rows(customer)), cls=DjangoJSONEncoder, indent=2)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    for row in _rows(customer):
        row["detail"] = json.dumps(row["detail"], sort_keys=True)
        writer.writerow({name: _spreadsheet_safe(value) for name, value in row.items()})
    return out.getvalue()


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
    # Only the username. Django masks the password already, but there's no reason to keep it.
    username = credentials.get("username", "")
    _login_event(LoginEvent.Kind.FAILED, request, username if isinstance(username, str) else "")
