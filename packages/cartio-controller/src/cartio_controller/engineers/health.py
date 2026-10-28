"""Collector health as the engineer UI shows it: online, offline, never checked in, not enrolled."""

from datetime import timedelta

from django.utils import timezone

from cartio_controller.collectors.models import Collector

# Collectors check in every minute by default. Three missed check-ins and it shows as offline.
ONLINE_WITHIN = timedelta(minutes=3)


def collector_health(collector, now=None) -> str:
    if collector.enrolled_at is None:
        return "not enrolled"
    if collector.last_check_in is None:
        return "never checked in"
    now = now or timezone.now()
    return "online" if now - collector.last_check_in <= ONLINE_WITHIN else "offline"


def collectors(customer, now=None) -> list:
    """The customer's collectors by name, each with its `health`."""
    rows = list(Collector.objects.for_customer(customer).order_by("name"))
    now = now or timezone.now()
    for collector in rows:
        collector.health = collector_health(collector, now)
    return rows
