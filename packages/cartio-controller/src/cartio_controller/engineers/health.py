"""Collector health as the engineer UI shows it: online, offline, never checked in, not enrolled,
revoked."""

from datetime import timedelta

from django.utils import timezone

from cartio_controller.collectors.models import Collector

# Collectors check in every minute by default. Three missed check-ins and it shows as offline.
ONLINE_WITHIN = timedelta(minutes=3)


def collector_health(collector, now=None) -> str:
    if collector.revoked_at is not None:
        return "revoked"
    if collector.enrolled_at is None:
        return "not enrolled"
    if collector.last_check_in is None:
        return "never checked in"
    now = now or timezone.now()
    return "online" if now - collector.last_check_in <= ONLINE_WITHIN else "offline"


def collectors(customer, now=None, *, revoked=False) -> list:
    """The customer's collectors by name, each with its `health`. Revoked ones only if asked."""
    rows = Collector.objects.for_customer(customer).select_related("site").order_by("name")
    if not revoked:
        rows = rows.filter(revoked_at__isnull=True)
    rows = list(rows)
    now = now or timezone.now()
    for collector in rows:
        collector.health = collector_health(collector, now)
    return rows
