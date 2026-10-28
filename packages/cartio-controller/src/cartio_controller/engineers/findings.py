"""The findings screen (DESIGN.md section 7): check results as findings, and engineer edits.

A check finding is one check that failed on at least one device in the engagement's latest
assessment run. Its title, severity and recommendation come from the check, and the engineer's
changes are kept on top in a `Finding` row. Manual findings live in `Finding` rows only.

Every change is written with its audit event in one transaction, and `for_report()` is the
edited set the report prints (YAN-1666).
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

from cartio_core.checks import CHECKS
from django.db import transaction
from django.utils import timezone

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import (
    AssessmentRun,
    AuditEvent,
    CheckResult,
    Device,
    Finding,
)

# The five v1 checks, in the order the design lists them.
ORDER = ["eol-eos", "firmware-drift", "insecure-management", "ntp-syslog", "snmpv3"]

SEVERITIES = [value for value, _ in Finding.Severity.choices]
SEVERITY_LABELS = dict(Finding.Severity.choices)

MAX_TITLE = 200
MAX_TEXT = 10_000


def _severity(value: str) -> str:
    """A check's severity as the screen has it. Info shows as low."""
    return value if value in SEVERITY_LABELS else Finding.Severity.LOW


@dataclass
class Affected:
    device: Device
    status: str = CheckResult.Status.FAIL
    evidence: list = field(default_factory=list)


@dataclass
class Item:
    """One finding as the screen and the report show it."""

    key: str
    kind: str
    check_id: str
    title: str
    severity: str
    recommendation: str
    suggested_config: str = ""
    evidence: str = ""
    devices: list = field(default_factory=list)
    note: str = ""
    include_in_report: bool = True
    removed: bool = False
    removed_reason: str = ""
    # For a check finding, as the check has it, to show what the engineer changed.
    check_severity: str = ""
    severity_reason: str = ""
    edited: bool = False
    edited_by: str = ""
    row: Finding | None = None

    @property
    def manual(self) -> bool:
        return self.kind == Finding.Kind.MANUAL

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABELS[self.severity]

    @property
    def check_title(self) -> str:
        check = CHECKS.get(self.check_id)
        return check.title if check else "Engineer finding"

    @property
    def failing(self) -> list:
        return [a for a in self.devices if a.status == CheckResult.Status.FAIL]

    @property
    def severity_changed(self) -> bool:
        return bool(self.check_severity) and self.severity != self.check_severity


@dataclass
class CheckSummary:
    id: str
    title: str
    counts: dict

    @property
    def failed(self) -> int:
        return self.counts[CheckResult.Status.FAIL]


class Findings:
    """An engagement's findings: the latest run's check findings with edits, and manual ones."""

    def __init__(self, customer, engagement):
        self.customer = customer
        self.engagement = engagement
        self.run = (
            AssessmentRun.objects.for_customer(customer)
            .filter(engagement=engagement)
            .order_by("-started_at")
            .first()
        )
        # By id as text, the form a manual finding keeps them in.
        self.devices = {
            str(d.pk): d
            for d in Device.objects.for_customer(customer)
            .filter(engagement=engagement)
            .select_related("site")
            .order_by("name")
        }
        self.checks = self._checks()
        rows = list(Finding.objects.for_customer(customer).filter(engagement=engagement))
        edits = {r.check_id: r for r in rows if r.kind == Finding.Kind.CHECK}
        items = [self._check_item(c, edits.get(c.id)) for c in self.checks if c.failed]
        items += [self._manual_item(r) for r in rows if r.kind == Finding.Kind.MANUAL]
        items.sort(key=lambda i: (SEVERITIES.index(i.severity), i.title.casefold()))
        self.items = [i for i in items if not i.removed]
        self.removed = [i for i in items if i.removed]
        self._by_key = {i.key: i for i in items}

    def _checks(self) -> list:
        results = {}
        if self.run is not None:
            rows = CheckResult.objects.for_customer(self.customer).filter(run=self.run)
            for result in rows.order_by("device__name"):
                results.setdefault(result.check_id, []).append(result)
        self._results = results
        summaries = []
        for check_id in ORDER:
            counts = {status: 0 for status in CheckResult.Status.values}
            for result in results.get(check_id, []):
                counts[result.status] += 1
            summaries.append(CheckSummary(check_id, CHECKS[check_id].title, counts))
        return summaries

    def _check_item(self, summary, row) -> Item:
        check = CHECKS[summary.id]
        devices = [
            Affected(self.devices[str(r.device_id)], r.status, r.evidence)
            for r in self._results[summary.id]
            if r.status == CheckResult.Status.FAIL and str(r.device_id) in self.devices
        ]
        evidence = "\n".join(f"{a.device.name}: {line}" for a in devices for line in a.evidence)
        item = Item(
            key=check.id,
            kind=Finding.Kind.CHECK,
            check_id=check.id,
            title=check.title,
            severity=_severity(check.severity),
            check_severity=_severity(check.severity),
            recommendation=check.recommendation,
            suggested_config=check.suggested_config,
            evidence=evidence,
            devices=devices,
        )
        if row is not None:
            item.row = row
            item.title = row.title or item.title
            item.severity = row.severity or item.severity
            item.severity_reason = row.severity_reason
            item.recommendation = row.recommendation or item.recommendation
            item.suggested_config = row.suggested_config or item.suggested_config
            item.edited = bool(
                row.title or row.severity or row.recommendation or row.suggested_config
            )
            item.edited_by = row.updated_by
            self._overlay(item, row)
        return item

    def _manual_item(self, row) -> Item:
        item = Item(
            key=str(row.pk),
            kind=Finding.Kind.MANUAL,
            check_id=row.check_id,
            title=row.title,
            severity=_severity(row.severity),
            recommendation=row.recommendation,
            suggested_config=row.suggested_config,
            evidence=row.evidence,
            devices=[Affected(self.devices[pk]) for pk in row.device_ids if pk in self.devices],
            edited_by=row.created_by,
            row=row,
        )
        self._overlay(item, row)
        return item

    @staticmethod
    def _overlay(item, row):
        item.note = row.note
        item.include_in_report = row.include_in_report
        item.removed = row.removed_at is not None
        item.removed_reason = row.removed_reason

    def get(self, key):
        return self._by_key.get(key)

    def for_report(self) -> list:
        """What the report prints: findings left in and included, with the engineer's edits."""
        return [i for i in self.items if i.include_in_report]


class Screen:
    """What the findings screen shows, from the query string."""

    def __init__(self, findings, params):
        self.findings = findings
        selected = params.get("finding", "")
        # Arriving at a finding (global search, a link) clears the check filter.
        check = "" if selected else params.get("check", "")
        self.check = check if check in ORDER else ""
        self.visible = [i for i in findings.items if not self.check or i.check_id == self.check]
        # The detail only ever shows a finding in the visible list (DESIGN.md, Principles).
        self.selected = next((i for i in self.visible if i.key == selected), None)
        if self.selected is None and self.visible:
            self.selected = self.visible[0]
        mode = params.get("mode", "")
        # Adding needs no selection; editing edits the selected finding.
        self.mode = mode if mode == "add" or (mode == "edit" and self.selected) else ""
        self.confirm_remove = params.get("remove") == "1" and self.selected is not None

    @property
    def check_title(self) -> str:
        return CHECKS[self.check].title if self.check else ""

    def link(self, **values) -> str:
        values = {k: v for k, v in values.items() if v}
        return "?" + urlencode(values) if values else "?"


# Engineer edits. Each one checks the engagement is open, then writes the change and its
# audit event together.


class EditError(ValueError):
    """The change isn't valid; the message says why, for the engineer."""


def editable(customer, engagement) -> bool:
    """Completed engagements and archived customers are read-only, and On site data isn't
    kept on the controller."""
    return (
        engagement.status != engagement.Status.COMPLETED
        and not customer.is_archived
        and not customer.is_on_site
    )


def _text(data, name, limit=MAX_TEXT) -> str:
    return (data.get(name) or "").replace("\r\n", "\n").strip()[:limit]


def _form(findings, data, item=None) -> dict:
    """The fields of an add or edit form, checked."""
    manual = item is None or item.manual
    values = {
        "title": _text(data, "title", MAX_TITLE),
        "severity": data.get("severity", ""),
        "severity_reason": _text(data, "severity_reason"),
        "evidence": _text(data, "evidence"),
        "recommendation": _text(data, "recommendation"),
        "suggested_config": _text(data, "suggested_config"),
        "include_in_report": data.get("include_in_report") == "on",
    }
    if not values["title"]:
        raise EditError("Add a title.")
    if values["severity"] not in SEVERITY_LABELS:
        raise EditError("Pick a severity.")
    if not values["recommendation"]:
        raise EditError("Add a recommendation.")
    if manual:
        check_id = data.get("check_id", "")
        values["check_id"] = check_id if check_id in ORDER else ""
        ids = []
        for value in data.getlist("devices"):
            # Only this engagement's devices, which `findings.devices` is scoped to.
            if value in findings.devices and value not in ids:
                ids.append(value)
        if not ids:
            raise EditError("Pick at least one affected device.")
        values["device_ids"] = ids
        values["severity_reason"] = ""
    elif values["severity"] != item.check_severity and not values["severity_reason"]:
        raise EditError("Give a reason for the severity change.")
    return values


def add(findings, data, actor) -> Finding:
    values = _form(findings, data)
    with transaction.atomic():
        row = Finding.objects.for_customer(findings.customer).create(
            engagement=findings.engagement,
            kind=Finding.Kind.MANUAL,
            created_by=actor,
            updated_by=actor,
            **values,
        )
        _record(findings, AuditEvent.Action.FINDING_ADDED, row, actor, severity=row.severity)
    return row


def _row_for(findings, item) -> Finding:
    """The row that keeps the engineer's changes, made on the first change to a check finding."""
    if item.row is not None:
        return item.row
    row, _ = Finding.objects.for_customer(findings.customer).get_or_create(
        engagement=findings.engagement,
        kind=Finding.Kind.CHECK,
        check_id=item.check_id,
        defaults={"created_by": "", "updated_by": ""},
    )
    item.row = row
    return row


def edit(findings, item, data, actor) -> list:
    """Save the edit form. Returns the names of the fields that changed."""
    values = _form(findings, data, item)
    if not item.manual:
        check = CHECKS[item.check_id]
        defaults = {
            "title": check.title,
            "severity": item.check_severity,
            "recommendation": check.recommendation,
            "suggested_config": check.suggested_config,
        }
        # Store only what differs from the check, so a later check change still shows.
        for name, default in defaults.items():
            if values[name] == default:
                values[name] = ""
        if not values["severity"]:
            values["severity_reason"] = ""
        values.pop("evidence")
    # Against a blank row while there's none yet, so a no-change save writes nothing.
    current = item.row or Finding()
    changed = [name for name, value in values.items() if getattr(current, name) != value]
    if not changed:
        return []
    with transaction.atomic():
        row = _row_for(findings, item)
        for name, value in values.items():
            setattr(row, name, value)
        row.updated_by = actor
        row.save()
        detail = {"fields": changed}
        if "severity" in changed:
            detail["severity"] = row.severity or item.check_severity
            detail["severity_reason"] = row.severity_reason
        _record(findings, AuditEvent.Action.FINDING_UPDATED, row, actor, item=item, **detail)
    return changed


def set_included(findings, item, included: bool, actor) -> None:
    if item.include_in_report == included:
        return
    with transaction.atomic():
        row = _row_for(findings, item)
        row.include_in_report = included
        row.updated_by = actor
        row.save()
        _record(
            findings,
            AuditEvent.Action.FINDING_UPDATED,
            row,
            actor,
            item=item,
            fields=["include_in_report"],
            include_in_report=included,
        )


def set_note(findings, item, note: str, actor) -> bool:
    note = note.replace("\r\n", "\n").strip()[:MAX_TEXT]
    if item.note == note:
        return False
    with transaction.atomic():
        row = _row_for(findings, item)
        row.note = note
        row.updated_by = actor
        row.save()
        _record(findings, AuditEvent.Action.FINDING_UPDATED, row, actor, item=item, fields=["note"])
    return True


def remove(findings, item, reason: str, actor) -> None:
    """Dismiss a check finding or delete a manual one. Both keep the row, so they restore."""
    reason = reason.strip()[:MAX_TEXT]
    if not reason:
        raise EditError("Give a reason. It is kept in the audit log.")
    with transaction.atomic():
        row = _row_for(findings, item)
        row.removed_at = timezone.now()
        row.removed_reason = reason
        row.updated_by = actor
        row.save()
        _record(
            findings,
            AuditEvent.Action.FINDING_REMOVED,
            row,
            actor,
            item=item,
            how="deleted" if item.manual else "dismissed",
            reason=reason,
        )


def restore(findings, item, actor) -> None:
    with transaction.atomic():
        row = _row_for(findings, item)
        row.removed_at = None
        row.removed_reason = ""
        row.updated_by = actor
        row.save()
        _record(findings, AuditEvent.Action.FINDING_RESTORED, row, actor, item=item)


def _record(findings, action, row, actor, item=None, **detail):
    audit.record(
        findings.customer,
        action,
        actor=actor,
        object_type="finding",
        object_id=row.pk,
        object_name=(item.title if item is not None else row.title),
        engagement=findings.engagement.pk,
        kind=row.kind,
        check=row.check_id,
        **detail,
    )
