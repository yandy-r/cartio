"""The device inventory screen (DESIGN.md section 6): filtering, sorting, summary and export.

Filters, sort and selection live in the query string, so a link or the back button shows the
same view. Only rows the filters leave visible count as selected, for the footer and for export.
"""

import csv
import io
import ipaddress
import uuid
from collections import Counter
from urllib.parse import urlencode

from django.db.models import Count, Max
from django.utils import timezone

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import AuditEvent, Device, Source

from . import xlsx

# Where a device came from, as the Source column says it.
SOURCE_LABELS = {
    Source.Kind.LIVE_RUN: "Discovery",
    Source.Kind.UPLOAD: "Import",
    Source.Kind.TOOL_EXPORT: "Tool export",
}

# OS lifecycle states, in the order the filter pills show them.
LIFECYCLE = {
    "supported": "Supported",
    "eos-soon": "EoS soon",
    "eos": "EoS or EoL",
    "unknown": "Unknown",
    "pending": "Pending",
}

# Table columns: (sort key, header).
COLUMNS = [
    ("hostname", "Hostname"),
    ("ip", "IP"),
    ("vendor", "Vendor and model"),
    ("serial", "Serial"),
    ("os", "OS"),
    ("site", "Site"),
    ("source", "Source"),
    ("findings", "Findings"),
    ("seen", "Last seen"),
]

# Sortable columns, by their URL value.
SORTS = ("hostname", "ip", "vendor", "serial", "os", "site", "source", "seen")

EXPORT_COLUMNS = [
    "hostname",
    "ip",
    "vendor",
    "model",
    "serial",
    "os",
    "os_state",
    "site",
    "source",
    "last_seen",
]


def lifecycle_state(device) -> str:
    """The device's OS lifecycle state.

    Cartio has no end-of-life data yet, so every device is unknown until a lifecycle source lands.
    Integrations will add pending devices (DESIGN.md section 14).
    """
    return "unknown"


class View:
    """What the inventory shows for one engagement, read from the query string."""

    def __init__(self, customer, engagement, params):
        self.customer = customer
        self.engagement = engagement
        self.q = params.get("q", "").strip()[:200]
        self.vendor = params.get("vendor", "")[:100]
        self.os = params.get("os", "")
        if self.os not in LIFECYCLE:
            self.os = ""
        self.site = params.get("site", "")[:200]
        sort = params.get("sort", "hostname")
        self.descending = sort.startswith("-")
        self.sort = sort.lstrip("-")
        if self.sort not in SORTS:
            self.sort, self.descending = "hostname", False
        self.selected = _uuids(params.getlist("selected"))

        self.all_devices = self._devices()
        self.rows = [d for d in self.all_devices if self._matches(d)]
        visible = {d.pk for d in self.rows}
        # Selecting or clearing every visible row, from the header checkbox.
        if params.get("select") == "visible":
            self.selected |= visible
        elif params.get("select") == "none":
            self.selected -= visible
        for device in self.rows:
            device.selected = device.pk in self.selected
        self.selected_visible = [d for d in self.rows if d.selected]
        self.selected_hidden = sorted(str(pk) for pk in self.selected - visible)

    def _devices(self) -> list:
        if self.engagement is None:
            return []
        devices = (
            Device.objects.for_customer(self.customer)
            .filter(engagement=self.engagement)
            .select_related("site", "source")
            .annotate(
                interface_count=Count("interfaces", distinct=True),
                neighbor_count=Count("interfaces__links_a", distinct=True),
                config_count=Count("config_snapshots", distinct=True),
                config_at=Max("config_snapshots__collected_at"),
            )
        )
        rows = list(devices)
        for device in rows:
            device.os_state = lifecycle_state(device)
            device.os_state_label = LIFECYCLE[device.os_state]
            device.source_label = (
                SOURCE_LABELS.get(device.source.kind, "") if device.source else "Unknown"
            )
        # Blank values last either way, then host name so the order is stable.
        filled = [d for d in rows if _sort_value(d, self.sort) is not None]
        blank = [d for d in rows if _sort_value(d, self.sort) is None]
        filled.sort(key=lambda d: d.name)
        filled.sort(key=lambda d: _sort_value(d, self.sort), reverse=self.descending)
        return filled + blank

    def _matches(self, device) -> bool:
        if self.vendor and device.manufacturer != self.vendor:
            return False
        if self.os and device.os_state != self.os:
            return False
        if self.site and (device.site.name if device.site else "") != self.site:
            return False
        if self.q:
            needle = self.q.casefold()
            haystack = (device.name, str(device.management_ip or ""), device.serial, device.model)
            return any(needle in value.casefold() for value in haystack)
        return True

    # Links: the current view with one thing changed.

    def params(self, **changes) -> dict:
        current = {
            "q": self.q,
            "vendor": self.vendor,
            "os": self.os,
            "site": self.site,
            "sort": ("-" if self.descending else "") + self.sort,
        }
        current.update(changes)
        if current["sort"] == "hostname":
            current["sort"] = ""
        return {key: value for key, value in current.items() if value}

    def link(self, **changes) -> str:
        return "?" + urlencode(self.params(**changes))

    @property
    def here(self) -> str:
        return self.link()

    @property
    def select_visible_link(self) -> str:
        return self.link(select="visible")

    @property
    def select_none_link(self) -> str:
        return self.link(select="none")

    @property
    def clear_site_link(self) -> str:
        return self.link(site="")

    @property
    def filtered(self) -> bool:
        return bool(self.q or self.vendor or self.os or self.site)

    def columns(self) -> list:
        """Table headers: (label, link, aria-sort, arrow)."""
        out = []
        for key, label in COLUMNS:
            if key not in SORTS:
                out.append((label, "", "", ""))
                continue
            current = key == self.sort
            descending = current and not self.descending
            link = self.link(sort=("-" if descending else "") + key)
            aria = ("descending" if self.descending else "ascending") if current else "none"
            arrow = ("↓" if self.descending else "↑") if current else ""
            out.append((label, link, aria, arrow))
        return out

    def vendor_pills(self) -> list:
        """(label, link, pressed) for All and each vendor in the engagement."""
        counts = Counter(d.manufacturer for d in self.all_devices if d.manufacturer)
        pills = [("All", self.link(vendor=""), not self.vendor)]
        for name, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            pills.append((name, self.link(vendor=name), self.vendor == name))
        return pills

    def os_pills(self) -> list:
        present = {d.os_state for d in self.all_devices}
        pills = [("All", self.link(os=""), not self.os)]
        for key, label in LIFECYCLE.items():
            if key in present or key == self.os:
                pills.append((label, self.link(os=key), self.os == key))
        return pills

    def summary(self) -> list:
        """Breakdowns for the summary column, each row a filter: (title, [(label, count, pct,
        link, pressed)]). Picking the applied one again clears it."""
        total = len(self.all_devices) or 1
        groups = [
            ("Vendor", "vendor", Counter(d.manufacturer or "" for d in self.all_devices)),
            ("OS state", "os", Counter(d.os_state for d in self.all_devices)),
            (
                "Site",
                "site",
                Counter((d.site.name if d.site else "") for d in self.all_devices),
            ),
        ]
        out = []
        for title, key, counts in groups:
            rows = []
            for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
                if key == "os":
                    label = LIFECYCLE[value]
                else:
                    label = value or ("Unknown vendor" if key == "vendor" else "No site")
                # A blank vendor or site can't be filtered on, so its row is only a count.
                pressed = bool(value) and getattr(self, key) == value
                link = self.link(**{key: "" if pressed else value}) if value else ""
                rows.append((label, count, round(100 * count / total), link, pressed))
            out.append((title, rows))
        return out


def _sort_value(device, column):
    if column == "ip":
        if not device.management_ip:
            return None
        address = ipaddress.ip_address(device.management_ip)
        return (address.version, int(address))
    if column == "seen":
        return device.last_seen
    value = {
        "hostname": device.name,
        "vendor": device.manufacturer,
        "serial": device.serial,
        "os": device.os_version,
        "site": device.site.name if device.site else "",
        "source": device.source_label,
    }[column]
    return value.casefold() or None


def _uuids(values) -> set:
    out = set()
    for value in values[:5000]:
        try:
            out.add(uuid.UUID(value))
        except ValueError:
            continue
    return out


def export_row(device) -> list:
    return [
        device.name,
        str(device.management_ip or ""),
        device.manufacturer,
        device.model,
        device.serial,
        device.os_version,
        device.os_state_label,
        device.site.name if device.site else "",
        device.source_label,
        device.last_seen.isoformat() if device.last_seen else "",
    ]


def filename(customer, engagement, fmt: str, at=None) -> str:
    """The export's file name names the customer, engagement and time (DESIGN.md, Export).

    ASCII only: browsers don't decode an encoded `filename="..."` in Content-Disposition.
    """
    at = at or timezone.now()
    parts = ["cartio-inventory", customer.name, engagement.name, at.strftime("%Y%m%dT%H%MZ")]
    safe = "-".join(
        "".join(c if c.isascii() and c.isalnum() else "-" for c in p).strip("-") for p in parts
    )
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{safe}.{fmt}"


def export(view, actor: str, fmt: str) -> bytes:
    """The selected visible rows as CSV or XLSX. The export is audit-logged first."""
    if fmt not in ("csv", "xlsx"):
        raise ValueError("Export as csv or xlsx.")
    rows = view.selected_visible
    audit.record(
        view.customer,
        AuditEvent.Action.INVENTORY_EXPORTED,
        actor=actor,
        object_type="engagement",
        object_id=view.engagement.pk,
        object_name=view.engagement.name,
        format=fmt,
        devices=len(rows),
        filters=view.params(sort=""),
    )
    if fmt == "xlsx":
        return xlsx.workbook("Inventory", [EXPORT_COLUMNS, *(export_row(d) for d in rows)])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(EXPORT_COLUMNS)
    for device in rows:
        writer.writerow([audit.spreadsheet_safe(value) for value in export_row(device)])
    return out.getvalue().encode()
