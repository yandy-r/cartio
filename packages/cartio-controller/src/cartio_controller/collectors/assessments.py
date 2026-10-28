"""Run the assessment checks over one engagement's devices and keep the results (YAN-1664).

The checks read the engagement's stored inventory, latest configs and latest outputs, so they
can run again at any time without collecting again. Each run is a new `AssessmentRun` with one
`CheckResult` per device and check, written with its audit event in one transaction.

Hosted customers only. An On site customer's network data, results included, stays in their own
store, and the models refuse it too (`OnSiteDataError`).
"""

from __future__ import annotations

from collections.abc import Iterable

from cartio_core.checks import CHECKS, Check, DeviceFacts, run_checks
from django.db import transaction
from django.db.models import Max

from . import audit
from .models import (
    AssessmentRun,
    AuditEvent,
    CheckResult,
    ConfigSnapshot,
    Customer,
    Device,
    Engagement,
    OnSiteDataError,
    RawOutput,
)


class AssessmentNotAllowed(ValueError):
    """The checks can't run: the customer or engagement is archived."""


def run_assessment(
    engagement: Engagement, started_by: str = "", checks: Iterable[Check] | None = None
) -> AssessmentRun:
    checks = list(CHECKS.values() if checks is None else checks)
    if len({check.id for check in checks}) != len(checks):
        raise ValueError("Each check may run only once.")
    with transaction.atomic():
        # Lock both, so neither is archived or changes mode while the run is written.
        customer = Customer.objects.select_for_update().get(pk=engagement.customer_id)
        if customer.data_mode == Customer.DataMode.ON_SITE:
            raise OnSiteDataError(
                "This customer is On site: checks run next to the customer's own store."
            )
        if customer.is_archived:
            raise AssessmentNotAllowed("This customer is archived.")
        engagement = (
            Engagement.objects.for_customer(customer).select_for_update().get(pk=engagement.pk)
        )
        if engagement.is_archived:
            raise AssessmentNotAllowed("This engagement is archived.")

        devices = list(Device.objects.for_customer(customer).filter(engagement=engagement))
        facts = _facts(customer, devices)
        run = AssessmentRun.objects.for_customer(customer).create(
            engagement=engagement, started_by=started_by, check_ids=[c.id for c in checks]
        )
        by_name = {device.name: device for device in devices}
        results = run_checks(facts, checks)
        CheckResult.objects.for_customer(customer).bulk_create(
            CheckResult(
                run=run,
                device=by_name[result.device],
                check_id=result.check.id,
                severity=result.check.severity,
                status=result.outcome.status,
                evidence=list(result.outcome.evidence),
            )
            for result in results
        )
        counts = {status: 0 for status in CheckResult.Status.values}
        for result in results:
            counts[result.outcome.status] += 1
        audit.record(
            customer,
            AuditEvent.Action.ASSESSMENT_RUN,
            actor=started_by,
            object_type="assessment_run",
            object_id=run.pk,
            object_name=engagement.name,
            engagement=engagement.pk,
            checks=run.check_ids,
            device_count=len(devices),
            results=counts,
        )
    return run


def _facts(customer: Customer, devices: list[Device]) -> list[DeviceFacts]:
    """Each device as the checks see it: its fields, latest config and latest outputs."""
    # Only the latest of each, chosen in the database: history grows with every collection.
    snapshots = ConfigSnapshot.objects.for_customer(customer).filter(device__in=devices)
    latest = snapshots.values("device_id").annotate(latest=Max("pk")).values("latest")
    configs = dict(snapshots.filter(pk__in=latest).values_list("device_id", "content"))
    raw = RawOutput.objects.for_customer(customer).filter(device__in=devices)
    latest = raw.values("device_id", "command").annotate(latest=Max("pk")).values("latest")
    outputs: dict = {}
    for device_id, command, text in raw.filter(pk__in=latest).values_list(
        "device_id", "command", "output"
    ):
        outputs.setdefault(device_id, {})[command] = text
    return [
        DeviceFacts(
            name=device.name,
            platform=device.platform,
            manufacturer=device.manufacturer,
            model=device.model,
            os_version=device.os_version,
            config=configs.get(device.pk),
            outputs=outputs.get(device.pk, {}),
        )
        for device in devices
    ]
