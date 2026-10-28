"""The assessment report (DESIGN.md section 9, YAN-1666): what it says, and its export.

The report reads the engineer-edited findings (`Findings.for_report()`), so a finding left out
or dismissed on the Findings screen is left out here too, and the engineer's note prints under
its finding. The screen previews the same pages the PDF and DOCX hold: both are built from
`Report.blocks()`.

A report shows one customer's data, and nothing internal: no database ids, no collector ids,
no secrets. Configs and evidence are already masked by the collector (ADR-0003).
"""

import csv
import io
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlencode

from django.utils import timezone

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import AuditEvent, Collector, Site, Source

from . import report_files
from .findings import SEVERITIES, SEVERITY_LABELS, Findings
from .inventory import SOURCE_LABELS
from .report_files import BRAND

READ_ONLY = (
    "Cartio read the network with read-only access and changed nothing on any device. "
    "Recommendations in this report are text and suggested configuration only; your team "
    "decides what to apply. Secrets in configurations were masked before they left the "
    "collector, and none appear in this report."
)

# Sections the engineer can leave out. The cover, summary and findings are always in.
OPTIONAL = {
    "scope": ("Scope and method", "Sites, collectors, what was read and how"),
    "inventory": ("Inventory summary", "Devices by vendor, model, OS and site"),
    "appendix": ("Device appendix", "Every device with its serial and OS"),
}

# Evidence beyond this many lines per finding is summarised, so one noisy check can't take
# over the report. The full lines stay on the Findings screen.
MAX_EVIDENCE_LINES = 30

FORMATS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "csv": "text/csv; charset=utf-8",
}

CSV_COLUMNS = [
    "severity",
    "title",
    "check",
    "affected_devices",
    "recommendation",
    "suggested_config",
    "note",
]


@dataclass
class Block:
    """One piece of the report, in the order it prints.

    `kind` is one of: cover, h1, h2, p, bullets, table, code, finding, break.
    """

    kind: str
    text: str = ""
    items: list | None = None
    header: list | None = None
    severity: str = ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _date(value) -> str:
    return f"{value:%B} {value.day}, {value.year}" if value else ""


class Report:
    """The report for one engagement, with the sections and severities picked on the screen."""

    def __init__(self, customer, engagement, params, prepared_by=""):
        self.customer = customer
        self.engagement = engagement
        self.prepared_by = prepared_by
        self.prepared_on = timezone.localdate()
        # The screen's form always sends `set`; without it (a plain link) every section is in.
        if params.get("set"):
            picked = set(params.getlist("sections"))
            self.sections = [key for key in OPTIONAL if key in picked]
        else:
            self.sections = list(OPTIONAL)
        minimum = params.get("min", "")
        self.min_severity = minimum if minimum in SEVERITY_LABELS else SEVERITIES[-1]

        found = Findings(customer, engagement)
        self.run = found.run
        self.checks = found.checks
        cut = SEVERITIES.index(self.min_severity)
        included = found.for_report()
        self.findings = [i for i in included if SEVERITIES.index(i.severity) <= cut]
        self.below_cut = len(included) - len(self.findings)
        self.left_out = len(found.items) - len(included)
        self.devices = list(found.devices.values())
        self.sites = list(
            Site.objects.for_customer(customer).filter(archived_at__isnull=True).order_by("name")
        )
        self.collectors = list(
            Collector.objects.for_customer(customer)
            .filter(revoked_at__isnull=True, enrolled_at__isnull=False)
            .order_by("name")
        )
        self.sources = Counter(
            Source.objects.for_customer(customer)
            .filter(engagement=engagement)
            .values_list("kind", flat=True)
        )

    # What the screen shows around the preview.

    @property
    def manual_count(self) -> int:
        return sum(1 for i in self.findings if i.manual)

    @property
    def edited_count(self) -> int:
        return sum(1 for i in self.findings if i.edited or i.note)

    def query(self, **values) -> str:
        """The screen's query string for these choices, so export prints what was previewed."""
        params = [("set", "1"), *(("sections", s) for s in self.sections)]
        params.append(("min", values.get("min", self.min_severity)))
        return urlencode(params)

    @property
    def severity_counts(self) -> list:
        counts = Counter(i.severity for i in self.findings)
        return [(value, SEVERITY_LABELS[value], counts[value]) for value in SEVERITIES]

    @property
    def top_risks(self) -> list:
        """Critical and high findings, worst first, in a sentence each."""
        risks = []
        for item in self.findings:
            if item.severity not in ("critical", "high"):
                continue
            devices = _plural(len(self.affected(item)), "device")
            risks.append(f"{item.severity_label}: {item.title} ({devices}).")
        return risks[:5]

    def summary_text(self) -> str:
        counts = [f"{n} {label.lower()}" for _, label, n in self.severity_counts if n]
        sites = {d.site_id for d in self.devices if d.site_id}
        reviewed = f"We reviewed {_plural(len(self.devices), 'device')}"
        if sites:
            reviewed += f" across {_plural(len(sites), 'site')}"
        period = self.period()
        if period:
            reviewed += f" ({period})"
        if not self.findings:
            return reviewed + ". The checks found nothing that needs action."
        return (
            f"{reviewed} and found {_plural(len(self.findings), 'issue')} to address: "
            f"{', '.join(counts)}. Each is described below with the devices it affects and "
            "what we recommend."
        )

    def period(self) -> str:
        start, end = self.engagement.start_date, self.engagement.end_date
        if start and end:
            return f"{_date(start)} to {_date(end)}"
        return _date(start)

    def method_lines(self) -> list:
        lines = []
        if self.sources:
            read = ", ".join(
                f"{SOURCE_LABELS.get(kind, kind).lower()} ({n})"
                for kind, n in sorted(self.sources.items())
            )
            lines.append(f"Data came from: {read}.")
        if self.collectors:
            names = ", ".join(c.name for c in self.collectors)
            lines.append(f"Collectors on the customer's network: {names}.")
        lines.append(
            "Collectors log in to each device with a read-only account and run show commands "
            "only, then parse the output into an inventory, neighbours and configurations."
        )
        if self.run is not None:
            ran = _date(self.run.started_at)
            lines.append(f"The checks below ran on {ran} against the collected data:")
        return lines

    def check_rows(self) -> list:
        return [
            [check.title, str(check.counts["pass"]), str(check.counts["fail"])]
            for check in self.checks
        ]

    @staticmethod
    def _counted(values) -> list:
        return [[*key, str(n)] for key, n in sorted(Counter(values).items(), key=_by_count)]

    def vendor_rows(self) -> list:
        return self._counted(
            (d.manufacturer or "Unknown", d.model or "Unknown") for d in self.devices
        )

    def os_rows(self) -> list:
        return self._counted(
            (d.manufacturer or "Unknown", d.os_version or "Unknown") for d in self.devices
        )

    def site_rows(self) -> list:
        return self._counted((d.site.name if d.site else "No site",) for d in self.devices)

    def device_rows(self) -> list:
        return [
            [
                d.name,
                str(d.management_ip or ""),
                " ".join(v for v in (d.manufacturer, d.model) if v),
                d.serial,
                d.os_version,
                d.site.name if d.site else "",
            ]
            for d in self.devices
        ]

    @staticmethod
    def affected(item) -> list:
        devices = item.devices if item.manual else item.failing
        return [a.device.name for a in devices]

    @staticmethod
    def evidence(item) -> str:
        lines = [line for line in item.evidence.splitlines() if line.strip()]
        if len(lines) > MAX_EVIDENCE_LINES:
            more = len(lines) - MAX_EVIDENCE_LINES
            lines = lines[:MAX_EVIDENCE_LINES] + [f"… and {more} more lines"]
        return "\n".join(lines)

    def cover(self) -> list:
        """The cover's lines, after the title."""
        lines = [self.customer.name, self.engagement.name]
        if self.period():
            lines.append(self.period())
        by = f"Prepared by {self.prepared_by} · {BRAND}" if self.prepared_by else BRAND
        lines.append(f"{by} · {_date(self.prepared_on)}")
        return lines

    def blocks(self) -> list:
        """The report, top to bottom, for the PDF and DOCX writers."""
        out = [Block("cover", "Network assessment", items=self.cover()), Block("break")]
        out += [Block("h1", "Summary"), Block("p", self.summary_text())]
        out.append(
            Block(
                "table",
                header=["Severity", "Findings"],
                items=[[label, str(n)] for _, label, n in self.severity_counts],
            )
        )
        if self.top_risks:
            out += [Block("h2", "Top risks"), Block("bullets", items=self.top_risks)]
        if "scope" in self.sections:
            out += [Block("h1", "Scope and method")]
            if self.engagement.assessment:
                out.append(Block("p", self.engagement.assessment))
            if self.sites:
                out.append(Block("h2", "Sites"))
                out.append(
                    Block(
                        "table",
                        header=["Site", "Location"],
                        items=[[s.name, s.location] for s in self.sites],
                    )
                )
            out.append(Block("h2", "Method"))
            out += [Block("p", line) for line in self.method_lines()]
            if self.run is not None:
                out.append(
                    Block("table", header=["Check", "Pass", "Fail"], items=self.check_rows())
                )
            out.append(Block("p", READ_ONLY))
        if "inventory" in self.sections:
            out += [
                Block("h1", "Inventory summary"),
                Block("p", f"{_plural(len(self.devices), 'device')} in this assessment."),
                Block("h2", "By vendor and model"),
                Block("table", header=["Vendor", "Model", "Devices"], items=self.vendor_rows()),
                Block("h2", "By OS version"),
                Block("table", header=["Vendor", "OS version", "Devices"], items=self.os_rows()),
                Block("h2", "By site"),
                Block("table", header=["Site", "Devices"], items=self.site_rows()),
            ]
        out += [Block("break"), Block("h1", "Findings")]
        if not self.findings:
            out.append(Block("p", "No findings to report."))
        for item in self.findings:
            out.append(Block("finding", item.title, severity=item.severity))
            out.append(Block("p", f"Affected: {', '.join(self.affected(item)) or 'none listed'}"))
            evidence = self.evidence(item)
            if evidence:
                out += [Block("h2", "What we found"), Block("code", evidence)]
            out += [Block("h2", "Recommendation"), Block("p", item.recommendation)]
            if item.suggested_config:
                out += [
                    Block("h2", "Suggested configuration"),
                    Block("code", item.suggested_config),
                ]
            if item.note:
                out += [Block("h2", "Engineer's note"), Block("p", item.note)]
        if "appendix" in self.sections:
            out += [
                Block("break"),
                Block("h1", "Device appendix"),
                Block(
                    "table",
                    header=["Hostname", "IP", "Vendor and model", "Serial", "OS", "Site"],
                    items=self.device_rows(),
                ),
            ]
        return out


def pages(blocks) -> list:
    """The blocks split at page breaks, for the screen's preview."""
    out = [[]]
    for block in blocks:
        if block.kind == "break":
            out.append([])
        else:
            out[-1].append(block)
    return [page for page in out if page]


def _by_count(entry):
    key, n = entry
    return (-n, [k.casefold() for k in key])


def filename(customer, engagement, fmt: str, at=None) -> str:
    """Names the customer, engagement and time, in ASCII (see `inventory.filename`)."""
    at = at or timezone.now()
    parts = ["cartio-report", customer.name, engagement.name, at.strftime("%Y%m%dT%H%MZ")]
    if fmt == "csv":
        parts[0] = "cartio-findings"
    safe = "-".join(
        "".join(c if c.isascii() and c.isalnum() else "-" for c in p).strip("-") for p in parts
    )
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{safe}.{fmt}"


def findings_csv(report) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for item in report.findings:
        row = [
            item.severity_label,
            item.title,
            item.check_title,
            " ".join(report.affected(item)),
            item.recommendation,
            item.suggested_config,
            item.note,
        ]
        writer.writerow([audit.spreadsheet_safe(value) for value in row])
    return out.getvalue().encode()


def export(report, actor: str, fmt: str) -> tuple[bytes, str]:
    """The report as PDF or DOCX, or its findings as CSV. The export is audit-logged first."""
    if fmt not in FORMATS:
        raise ValueError("Export as pdf, docx or csv.")
    name = filename(report.customer, report.engagement, fmt)
    audit.record(
        report.customer,
        AuditEvent.Action.REPORT_EXPORTED,
        actor=actor,
        object_type="engagement",
        object_id=report.engagement.pk,
        object_name=report.engagement.name,
        format=fmt,
        file=name,
        findings=len(report.findings),
        min_severity=report.min_severity,
        sections=report.sections,
    )
    if fmt == "csv":
        return findings_csv(report), name
    if fmt == "docx":
        return report_files.docx(report), name
    return report_files.pdf(report), name


def history(customer, engagement, limit=10) -> list:
    """This engagement's earlier exports, newest first, from the audit log."""
    return list(
        AuditEvent.objects.for_customer(customer)
        .filter(
            action=AuditEvent.Action.REPORT_EXPORTED,
            object_type="engagement",
            object_id=str(engagement.pk),
        )
        .order_by("-at")[:limit]
    )
