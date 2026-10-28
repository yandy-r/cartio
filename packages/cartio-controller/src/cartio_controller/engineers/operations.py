"""Collectors, jobs and audit (DESIGN.md section 4): adding and revoking collectors, the audit tabs.

Every query goes through the current customer, so a collector id or a filter from another
customer's page finds nothing here.
"""

from django.db import transaction
from django.utils import timezone

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import AuditEvent, Collector, Site

MAX_NAME = Collector._meta.get_field("name").max_length
AUDIT_SHOWN = 200

# The audit tab's filters: a kind and the actions it covers.
_A = AuditEvent.Action
AUDIT_KINDS = {
    "jobs": (
        "Jobs",
        [_A.RUN_STARTED, _A.RUN_FINISHED, _A.OUTPUTS_STORED, _A.SOURCE_REPARSED],
    ),
    "collectors": (
        "Collectors",
        [_A.COLLECTOR_CREATED, _A.COLLECTOR_ENROLLED, _A.COLLECTOR_REVOKED],
    ),
    "credentials": (
        "Credentials",
        [_A.CREDENTIAL_CREATED, _A.CREDENTIAL_UPDATED, _A.CREDENTIAL_DELETED, _A.CREDENTIAL_USED],
    ),
    "admin": (
        "Admin",
        [
            _A.RECORD_CREATED,
            _A.RECORD_UPDATED,
            _A.RECORD_ARCHIVED,
            _A.RECORD_RESTORED,
            _A.AUDIT_EXPORTED,
            _A.MASTER_KEY_ROTATED,
            _A.INVENTORY_EXPORTED,
        ],
    ),
}
_KIND_OF = {action: key for key, (_, actions) in AUDIT_KINDS.items() for action in actions}


def sites(customer) -> list:
    return list(
        Site.objects.for_customer(customer).filter(archived_at__isnull=True).order_by("name")
    )


def find_collector(customer, collector_id):
    """One of this customer's collectors, or None. Another customer's id finds nothing."""
    return (
        Collector.objects.for_customer(customer).filter(pk=collector_id).first()
        if collector_id
        else None
    )


def add_errors(customer, name: str, site_id: str) -> dict:
    errors = {}
    if not name:
        errors["name"] = "Give the collector a name."
    elif len(name) > MAX_NAME:
        errors["name"] = f"Use at most {MAX_NAME} characters."
    elif Collector.objects.for_customer(customer).filter(name__iexact=name).exists():
        errors["name"] = "This customer already has a collector with this name."
    if site_id and site_from(customer, site_id) is None:
        errors["site"] = "Pick one of this customer's sites."
    return errors


def site_from(customer, site_id: str):
    if not site_id.isdecimal() or len(site_id) > 18:
        return None
    return (
        Site.objects.for_customer(customer)
        .filter(pk=int(site_id), archived_at__isnull=True)
        .first()
    )


def add(customer, name: str, site, actor: str) -> tuple[Collector, str]:
    """Create a collector waiting to enroll, audit-logged. Returns it and its one-time token."""
    with transaction.atomic():
        collector = Collector(customer=customer, name=name, site=site)
        token = collector.issue_enrollment_token()
        collector.save()
        audit.record(
            customer,
            AuditEvent.Action.COLLECTOR_CREATED,
            actor=actor,
            collector=collector,
            object_type="collector",
            object_id=collector.pk,
            object_name=collector.name,
            site=site.name if site else "",
            via="collectors",
        )
    return collector, token


def revoke(customer, collector_id, actor: str) -> Collector | None:
    """Revoke one of the customer's collectors, audit-logged. None if there's nothing to revoke."""
    with transaction.atomic():
        collector = (
            Collector.objects.for_customer(customer)
            # No ordering, so the lock doesn't join (and lock) the customer row too.
            .order_by()
            .select_for_update(of=("self",))
            .filter(pk=collector_id, revoked_at__isnull=True)
            .first()
        )
        if collector is None:
            return None
        enrolled = collector.enrolled_at is not None
        collector.revoke(timezone.now())
        collector.save()
        audit.record(
            customer,
            AuditEvent.Action.COLLECTOR_REVOKED,
            actor=actor,
            collector=collector,
            object_type="collector",
            object_id=collector.pk,
            object_name=collector.name,
            # A collector that never enrolled only loses its unused token.
            was_enrolled=enrolled,
        )
    return collector


def audit_events(customer, kind: str) -> list:
    events = AuditEvent.objects.for_customer(customer).select_related("collector")
    if kind in AUDIT_KINDS:
        events = events.filter(action__in=AUDIT_KINDS[kind][1])
    rows = list(events.order_by("-at", "-pk")[:AUDIT_SHOWN])
    for event in rows:
        event.kind = AUDIT_KINDS[_KIND_OF[event.action]][0] if event.action in _KIND_OF else ""
        event.summary = _summary(event.detail)
    return rows


def _summary(detail: dict) -> str:
    # The detail never holds a secret (audit.record); show it as short "key: value" pairs.
    parts = []
    for key, value in (detail or {}).items():
        if isinstance(value, (dict, list)):
            continue
        parts.append(f"{key.replace('_', ' ')}: {value}")
    return " · ".join(parts)[:300]
