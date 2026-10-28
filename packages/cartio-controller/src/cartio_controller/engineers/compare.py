"""Compare (DESIGN.md section 8): two engagements of one customer, and per-device config diffs.

Devices match by host name across the two engagements. Findings match by check and device, or
for a finding an engineer added by its title and device. Configs are the latest masked snapshot
of each device; masked secrets compare by digest, so a changed secret shows as changed without
its value (ADR-0003). A placeholder whose masking key version moved can't be compared, so the
line counts as unchanged and is marked "key rotated".

`Comparison` is the part the report's "changes since the last assessment" page (YAN-1745) needs.
"""

import difflib
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

from django.http import Http404
from django.utils import timezone

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import (
    AuditEvent,
    ConfigSnapshot,
    CrossCustomerError,
    Device,
)

from .findings import Findings

# The summary tiles, in the order the design shows them.
GROUPS = {
    "added": "Devices added",
    "removed": "Devices no longer seen",
    "os": "OS version changes",
    "fixed": "Findings fixed",
    "new": "New findings",
    "open": "Findings still open",
}
TAGS = {
    "added": "Added",
    "removed": "Removed",
    "os": "OS",
    "fixed": "Fixed",
    "new": "New",
    "open": "Open",
}

_PLACEHOLDER = re.compile(r"<masked:([A-Za-z0-9_.-]{1,16}):([0-9a-f]{16})(?::weak)?>")


def ordered(engagements) -> list:
    """Earliest first: by start date, then by when the engagement was created."""
    return sorted(
        engagements,
        key=lambda e: (e.start_date or e.created_at.date(), e.created_at, e.pk),
    )


def default_baseline(engagements, current):
    """The engagement just before `current`, or None when `current` is the first."""
    rows = ordered(engagements)
    at = rows.index(current)
    return rows[at - 1] if at > 0 else None


@dataclass
class Change:
    group: str
    host: str
    text: str
    site: str = ""

    @property
    def tag(self) -> str:
        return TAGS[self.group]


@dataclass
class Line:
    # "" for a line in both, "-" for the baseline's, "+" for the current one's.
    sig: str
    a: int | None
    b: int | None
    text: str
    # "secret" when only a masked secret's digest changed, "rotated" when only its key version.
    flag: str = ""


@dataclass
class DeviceDiff:
    host: str
    base: ConfigSnapshot
    current: ConfigSnapshot
    lines: list = field(default_factory=list)
    added: int = 0
    removed: int = 0


def _normal(line: str) -> str:
    return _PLACEHOLDER.sub("<masked>", line)


def _secret_changed(a: str, b: str) -> bool:
    """For two lines that differ only in masked values: did a secret change?

    Digests made with the same key version compare. Across key versions they can't, so a line
    whose only difference is the key version counts as unchanged.
    """
    return any(
        va == vb and da != db
        for (va, da), (vb, db) in zip(_PLACEHOLDER.findall(a), _PLACEHOLDER.findall(b), strict=True)
    )


def diff_lines(base: str, current: str) -> tuple[list, int, int]:
    """The two configs as one list of lines, and how many lines were added and removed."""
    a, b = base.splitlines(), current.splitlines()
    matcher = difflib.SequenceMatcher(
        None, [_normal(x) for x in a], [_normal(x) for x in b], autojunk=False
    )
    lines, added, removed = [], 0, 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2), strict=True):
                if a[i] == b[j]:
                    lines.append(Line("", i + 1, j + 1, b[j]))
                elif _secret_changed(a[i], b[j]):
                    lines.append(Line("-", i + 1, None, a[i]))
                    lines.append(Line("+", None, j + 1, b[j], "secret"))
                    added += 1
                    removed += 1
                else:
                    lines.append(Line("", i + 1, j + 1, b[j], "rotated"))
            continue
        for i in range(i1, i2):
            lines.append(Line("-", i + 1, None, a[i]))
            removed += 1
        for j in range(j1, j2):
            lines.append(Line("+", None, j + 1, b[j]))
            added += 1
    return lines, added, removed


def _latest_configs(customer, devices) -> dict:
    """Each device's latest snapshot, by device id."""
    rows = ConfigSnapshot.objects.for_customer(customer)
    # Snapshots are append-only, so read the content of the latest ones only.
    latest = {}
    for pk, device_id in (
        rows.filter(device__in=devices).order_by("-collected_at", "-pk").values_list("pk", "device")
    ):
        latest.setdefault(device_id, pk)
    return {s.device_id: s for s in rows.filter(pk__in=latest.values())}


def _finding_rows(customer, engagement) -> dict | None:
    """Open findings, one per affected device, keyed so they match across engagements.

    None when the checks haven't run on the engagement: its findings aren't known yet.
    """
    found = Findings(customer, engagement)
    if found.run is None:
        return None
    rows = {}
    for item in found.items:
        devices = item.failing if not item.manual else item.devices
        name = item.check_id if not item.manual else item.title.casefold()
        for affected in devices or [None]:
            device = affected.device if affected else None
            host = device.name if device else ""
            site = device.site.name if device and device.site_id else ""
            rows[(item.kind, name, host.casefold())] = (host, item.title, site)
    return rows


class Comparison:
    """What changed from `baseline` to `current`, two engagements of the same customer."""

    def __init__(self, customer, baseline, current):
        if baseline.customer_id != customer.pk or current.customer_id != customer.pk:
            raise CrossCustomerError("Compare only engagements of one customer.")
        self.customer = customer
        self.baseline = baseline
        self.current = current
        before = self._devices(baseline)
        after = self._devices(current)
        self.device_count = len(after)
        groups = {key: [] for key in GROUPS}
        for key, device in after.items():
            old = before.get(key)
            if old is None:
                groups["added"].append(
                    Change("added", device.name, _describe(device), _site(device))
                )
            elif old.os_version and device.os_version and old.os_version != device.os_version:
                text = f"{old.os_version} → {device.os_version}"
                groups["os"].append(Change("os", device.name, text, _site(device)))
        for key, device in before.items():
            if key not in after:
                text = "In the baseline, not found in this engagement."
                groups["removed"].append(Change("removed", device.name, text, _site(device)))
        old_findings = _finding_rows(customer, baseline)
        new_findings = _finding_rows(customer, current)
        # Without a run on both sides, fixed and new would only say which side wasn't assessed.
        self.not_assessed = [
            e for e, rows in ((baseline, old_findings), (current, new_findings)) if rows is None
        ]
        if self.not_assessed:
            old_findings = new_findings = {}
        for key, (host, title, site) in new_findings.items():
            group = "open" if key in old_findings else "new"
            groups[group].append(Change(group, host, title, site))
        for key, (host, title, site) in old_findings.items():
            # A finding on a device that's gone isn't fixed; the device shows as removed.
            if key not in new_findings and (not host or host.casefold() in after):
                groups["fixed"].append(Change("fixed", host, title, site))
        for changes in groups.values():
            changes.sort(key=lambda c: (c.host.casefold(), c.text.casefold()))
        self.groups = groups
        self.diffs = self._diffs(before, after)

    def _devices(self, engagement) -> dict:
        rows = (
            Device.objects.for_customer(self.customer)
            .filter(engagement=engagement)
            .select_related("site")
        )
        return {d.name.casefold(): d for d in rows}

    def _diffs(self, before, after) -> list:
        both = [(before[k], after[k]) for k in sorted(after) if k in before]
        old_configs = _latest_configs(self.customer, [old for old, _ in both])
        new_configs = _latest_configs(self.customer, [new for _, new in both])
        diffs = []
        for old, new in both:
            base, current = old_configs.get(old.pk), new_configs.get(new.pk)
            if base is None or current is None or base.sha256 == current.sha256:
                continue
            lines, added, removed = diff_lines(base.content, current.content)
            # A key rotation alone isn't a change.
            if added or removed:
                diffs.append(DeviceDiff(new.name, base, current, lines, added, removed))
        return diffs

    def counts(self) -> dict:
        return {key: len(changes) for key, changes in self.groups.items()}

    def diff_for(self, host: str):
        return next((d for d in self.diffs if d.host == host), None)


def _describe(device) -> str:
    parts = [p for p in (device.manufacturer, device.model) if p]
    text = " ".join(parts) or "New device"
    return f"{text} on {device.os_version}" if device.os_version else text


def _site(device) -> str:
    return device.site.name if device.site_id else ""


class Screen:
    """What the compare screen shows, from the URL: the current engagement and the query."""

    def __init__(self, customer, engagements, current, params):
        self.customer = customer
        self.current = current
        self.engagements = ordered(engagements)
        self.single = len(self.engagements) < 2
        baseline_pk = params.get("baseline", "")
        if baseline_pk:
            # Only this customer's engagements are offered, so any other id is not found.
            self.baseline = next((e for e in self.engagements if str(e.pk) == baseline_pk), None)
            if self.baseline is None:
                raise Http404
        else:
            self.baseline = None if self.single else default_baseline(self.engagements, current)
        self.same = self.baseline is not None and self.baseline.pk == current.pk
        self.reversed = self.baseline is not None and self.engagements.index(
            self.baseline
        ) > self.engagements.index(current)
        self.comparison = None
        if self.baseline is not None and not self.same:
            self.comparison = Comparison(customer, self.baseline, current)
        self.group = ""
        self.changes = []
        self.diff = None
        self.changes_only = params.get("changes") == "1"
        if self.comparison is not None:
            counts = self.comparison.counts()
            group = params.get("group", "")
            if group not in GROUPS:
                group = next((k for k in GROUPS if counts[k]), "added")
            self.group = group
            self.changes = self.comparison.groups[group]
            diffs = self.comparison.diffs
            self.diff = self.comparison.diff_for(params.get("device", "")) or (
                diffs[0] if diffs else None
            )

    @property
    def group_title(self) -> str:
        return GROUPS.get(self.group, "")

    def tiles(self) -> list:
        counts = self.comparison.counts()
        return [
            {"key": k, "label": label, "count": counts[k], "url": self.link(group=k)}
            for k, label in GROUPS.items()
        ]

    def diff_links(self) -> list:
        return [(d, self.link(device=d.host)) for d in self.comparison.diffs]

    def lines(self) -> list:
        lines = self.diff.lines if self.diff else []
        if self.changes_only:
            return [line for line in lines if line.sig or line.flag]
        return lines

    @property
    def toggle_url(self) -> str:
        return self.link(changes="" if self.changes_only else "1")

    def link(self, **values) -> str:
        """This view with some values changed. Keeps the baseline, group, device and toggle."""
        state = {
            "baseline": self.baseline.pk if self.baseline else "",
            "group": self.group,
            "device": self.diff.host if self.diff else "",
            "changes": "1" if self.changes_only else "",
        }
        state.update(values)
        state = {k: v for k, v in state.items() if v}
        return "?" + urlencode(state) if state else "?"


def filename(customer, current, host: str, at=None) -> str:
    """Names the customer, engagement, device and time, in ASCII (see `inventory.filename`)."""
    at = at or timezone.now()
    parts = ["cartio-diff", customer.name, current.name, host, at.strftime("%Y%m%dT%H%MZ")]
    safe = "-".join(
        "".join(c if c.isascii() and c.isalnum() else "-" for c in p).strip("-") for p in parts
    )
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{safe}.diff"


def _one_line(text: str) -> str:
    return " ".join(text.splitlines())


def diff_text(comparison, diff, at=None) -> str:
    """One device's diff as a unified diff of the whole file. Masked values only."""
    at = at or timezone.now()
    base, current = comparison.baseline, comparison.current
    customer, host = _one_line(comparison.customer.name), _one_line(diff.host)
    base_name, current_name = _one_line(base.name), _one_line(current.name)
    out = [
        f"# Cartio config diff · {customer} · {host}",
        f"# Baseline: {base_name} · collected {diff.base.collected_at:%Y-%m-%d %H:%M} UTC",
        f"# Current: {current_name} · collected {diff.current.collected_at:%Y-%m-%d %H:%M} UTC",
        f"# Exported {at:%Y-%m-%d %H:%M} UTC · secrets are masked, values never shown",
        f"--- {base_name}/{host}",
        f"+++ {current_name}/{host}",
        f"@@ -{_range(diff.lines, 'a')} +{_range(diff.lines, 'b')} @@",
    ]
    out += [f"{line.sig or ' '}{line.text}" for line in diff.lines]
    return "\n".join(out) + "\n"


def _range(lines, side: str) -> str:
    count = sum(1 for line in lines if getattr(line, side))
    return f"{1 if count else 0},{count}"


def export(comparison, diff, actor: str) -> tuple[bytes, str]:
    """One device's diff as a file. The export is audit-logged first."""
    name = filename(comparison.customer, comparison.current, diff.host)
    audit.record(
        comparison.customer,
        AuditEvent.Action.COMPARE_EXPORTED,
        actor=actor,
        object_type="device",
        object_id=diff.current.device_id,
        object_name=diff.host,
        baseline=comparison.baseline.pk,
        current=comparison.current.pk,
        file=name,
    )
    return diff_text(comparison, diff).encode(), name
