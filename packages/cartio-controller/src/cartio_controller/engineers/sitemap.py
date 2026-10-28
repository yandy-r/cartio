"""The Site map (DESIGN.md section 5): one site's devices and the links between them.

L2 links come from LLDP/CDP (`Link`), L3 links from OSPF neighbours and BGP peers
(`RoutingNeighbor`). A link reported by both of its devices is "seen from both ends"; one that
only one device reported, or whose far end isn't in the inventory, is drawn dashed. A far end
that isn't in the inventory is an unconfirmed device: seen as a neighbour, never polled.

Devices on another site that this site links to are drawn too, so a link never ends nowhere.
Discovery doesn't place devices on a site yet, so devices without one are a site of their own.

The layout is worked out here, from the L2 and L3 links together, so switching layer doesn't
move anything. `SiteMap.svg()` is the picture both the screen and the exports use, and what the
report's topology page (YAN-1745) needs.
"""

import csv
import io
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from urllib.parse import urlencode
from xml.sax.saxutils import escape as _escape
from xml.sax.saxutils import quoteattr as _quoteattr

from django.utils import timezone
from django.utils.safestring import mark_safe

from cartio_controller.collectors import audit
from cartio_controller.collectors.models import (
    AuditEvent,
    Device,
    Link,
    RoutingNeighbor,
    Site,
)

from .findings import SEVERITIES, SEVERITY_LABELS, Findings

# Characters XML 1.0 can't hold, which a neighbour's announced name could carry.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def escape(text: str) -> str:
    return _escape(_NOT_XML.sub("", text))


def quoteattr(text: str) -> str:
    return _quoteattr(_NOT_XML.sub("", text))


NO_SITE = "none"
NO_SITE_LABEL = "No site yet"

# Role: label, icon path (24x24, as the prototype draws them) and how high it sits on the map.
ROLES = {
    "ext": (
        "External peer",
        "M7 18h10a4 4 0 000-8 6 6 0 00-11.6 1.5A3.5 3.5 0 007 18z",
        0,
    ),
    "fw": (
        "Firewall",
        "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z M8 11h8 M8 15h8 M12 11v4",
        1,
    ),
    "rtr": (
        "Router",
        "M12 3a9 9 0 110 18 9 9 0 010-18z M8 9l4-3 4 3 M16 15l-4 3-4-3 M12 6v12",
        1,
    ),
    "sw": ("Switch", "M3 8h18v8H3z M7 12h.01 M11 12h.01 M15 12h.01 M18 12h.01", 2),
    "ap": ("Access point", "M4.5 10a11 11 0 0115 0 M7.5 13.2a6.5 6.5 0 019 0 M12 17.5h.01", 3),
    "unk": (
        "Unknown role",
        "M9.5 9a2.5 2.5 0 114 2c-1 .7-1.5 1.2-1.5 2.5 M12 17h.01 M4 4h16v16H4z",
        3,
    ),
}
ROLE_LABELS = {key: label for key, (label, _, _) in ROLES.items()}

# Model and host name hints, checked in this order. Words in host names count only whole.
_HINTS = [
    ("fw", re.compile(r"forti|^asa|pa-\d|srx|firewall|firepower")),
    ("ap", re.compile(r"aironet|^air-|^c9\d{2}ax|^mr\d|access.?point")),
    ("rtr", re.compile(r"^isr|^asr|^csr|^c8\d{2}|^mx\d|router")),
    ("sw", re.compile(r"ws-c|^c9[23458]\d{2}|catalyst|nexus|^n\dk|switch|^ex\d")),
]
_NAME_WORDS = {
    "fw": "fw",
    "firewall": "fw",
    "rtr": "rtr",
    "router": "rtr",
    "gw": "rtr",
    "sw": "sw",
    "switch": "sw",
    "core": "sw",
    "dist": "sw",
    "access": "sw",
    "ap": "ap",
    "wap": "ap",
}
_FIREWALL_PLATFORMS = {"fortios"}


def role_of(name: str, model: str = "", platform: str = "") -> str:
    """A device's role from its platform, model and host name. Cartio doesn't store roles."""
    if platform in _FIREWALL_PLATFORMS:
        return "fw"
    model = model.casefold()
    for role, pattern in _HINTS:
        if model and pattern.search(model):
            return role
    for word in re.split(r"[^a-z]+", name.casefold()):
        if word in _NAME_WORDS:
            return _NAME_WORDS[word]
    return "unk"


@dataclass
class Node:
    """A device on the map: one of this site's, one on another site, or one never polled."""

    id: str
    name: str
    role: str
    # "site" for this site's devices, "offsite" for another site's, "unconfirmed" otherwise.
    kind: str
    device: Device | None = None
    sub: str = ""
    # What's known of a device that was never polled: announced interfaces, AS, how it was seen.
    seen_as: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    x: int = 0
    y: int = 0
    dimmed: bool = False
    index: int = 0

    @property
    def role_label(self) -> str:
        return ROLE_LABELS[self.role]

    @property
    def icon(self) -> str:
        return ROLES[self.role][1]

    @property
    def dom_id(self) -> str:
        # By position: ids hold announced names, which could clash once made safe for HTML.
        return str(self.index)

    @property
    def unconfirmed(self) -> bool:
        return self.kind == "unconfirmed"

    @property
    def worst(self) -> str:
        """The most severe finding's severity, or ""."""
        found = [f.severity for f in self.findings]
        return min(found, key=SEVERITIES.index) if found else ""

    @property
    def aria(self) -> str:
        parts = [self.name, self.role_label]
        if self.kind == "offsite":
            parts.append(f"on {self.sub}")
        elif self.unconfirmed:
            parts.append("not collected")
        count = len(self.findings)
        parts.append(f"{count} finding{'' if count == 1 else 's'}")
        return ", ".join(parts)


@dataclass
class Edge:
    """A link between two nodes in one layer, with every report of it."""

    a: str
    b: str
    layer: str
    # L2: (interface on a, interface on b); L3: the protocol.
    a_port: str = ""
    b_port: str = ""
    protocols: set = field(default_factory=set)
    subnet: str = ""
    # The devices that reported this link.
    reporters: set = field(default_factory=set)
    dimmed: bool = False

    @property
    def both_ends(self) -> bool:
        return {self.a, self.b} <= self.reporters

    @property
    def protocol_label(self) -> str:
        return ", ".join(sorted(p.upper() for p in self.protocols))

    @property
    def label(self) -> str:
        """The L3 label: protocol and subnet."""
        return " ".join(p for p in (self.protocol_label, self.subnet) if p)

    def other(self, node_id: str) -> str:
        return self.b if node_id == self.a else self.a

    def port_of(self, node_id: str) -> str:
        return self.a_port if node_id == self.a else self.b_port

    def describe(self, nodes) -> str:
        a, b = nodes[self.a].name, nodes[self.b].name
        if self.layer == "l2":
            ends = f"{a} {self.a_port} to {b} {self.b_port}".replace("  ", " ")
            how = self.protocol_label
        else:
            ends = f"{a} to {b}"
            how = self.label
        seen = "seen from both ends" if self.both_ends else "seen from one end only"
        return f"{ends} · {how} · {seen}"


@dataclass
class SiteChoice:
    key: str
    name: str
    count: int


# Map geometry, in SVG units.
COLUMN = 168
ROW = 150
PAD = 72
PER_ROW = 8
HEADER = 56


class SiteMap:
    """One site of an engagement, in one layer, with filters and a pinned device."""

    def __init__(self, customer, engagement, params):
        self.customer = customer
        self.engagement = engagement
        self.layer = "l3" if params.get("layer") == "l3" else "l2"
        self.role = params.get("role", "")
        if self.role not in ROLES:
            self.role = ""
        self.vendor = params.get("vendor", "")[:100]
        self.with_findings = params.get("findings") == "1"

        devices = list(
            Device.objects.for_customer(customer)
            .filter(engagement=engagement)
            .select_related("site", "source")
        )
        self.sites = self._sites(devices)
        keys = [s.key for s in self.sites]
        site = params.get("site", "")
        if site not in keys:
            site = next((s.key for s in self.sites if s.count), keys[0] if keys else "")
        self.site = site
        self.site_name = next((s.name for s in self.sites if s.key == site), "")

        found = Findings(customer, engagement)
        by_device = defaultdict(list)
        for item in found.items:
            for affected in item.failing:
                by_device[str(affected.device.pk)].append(item)
        self.vendors = sorted({d.manufacturer for d in devices if d.manufacturer})

        self.nodes: dict[str, Node] = {}
        by_name = {d.name.casefold(): d for d in devices}
        for device in devices:
            if _site_key(device) == self.site:
                self._device_node(device, by_device)
        self.edges = {"l2": self._l2(by_name, by_device), "l3": self._l3(by_device)}
        self._layout()
        # Both layers' devices are laid out, so nothing moves; each layer draws its own.
        linked = {k for e in self.links for k in (e.a, e.b)}
        self.shown = {k: n for k, n in self.nodes.items() if n.kind == "site" or k in linked}
        for index, node in enumerate(self.nodes.values()):
            node.index = index
        self._filter()

        self.pinned = self.shown.get(params.get("device", ""))

    # Building the graph.

    def _sites(self, devices) -> list:
        counts = defaultdict(int)
        for device in devices:
            counts[_site_key(device)] += 1
        sites = Site.objects.for_customer(self.customer).filter(archived_at__isnull=True)
        rows = [SiteChoice(str(s.pk), s.name, counts.get(str(s.pk), 0)) for s in sites]
        # An archived site that still has devices in this engagement keeps its map.
        shown = {r.key for r in rows}
        for device in devices:
            key = _site_key(device)
            if key != NO_SITE and key not in shown:
                rows.append(SiteChoice(key, device.site.name, counts[key]))
                shown.add(key)
        rows.sort(key=lambda r: r.name.casefold())
        if counts.get(NO_SITE):
            rows.append(SiteChoice(NO_SITE, NO_SITE_LABEL, counts[NO_SITE]))
        return rows

    def _device_node(self, device, by_device) -> Node:
        key = str(device.pk)
        if key not in self.nodes:
            here = _site_key(device) == self.site
            self.nodes[key] = Node(
                id=key,
                name=device.name,
                role=role_of(device.name, device.model, device.platform),
                kind="site" if here else "offsite",
                device=device,
                sub=(device.model or device.manufacturer)
                if here
                else (device.site.name if device.site else NO_SITE_LABEL),
                findings=sorted(by_device.get(key, []), key=lambda i: SEVERITIES.index(i.severity)),
            )
        return self.nodes[key]

    def _unconfirmed(self, key, name, role, sub, seen) -> Node:
        node = self.nodes.get(key)
        if node is None:
            node = self.nodes[key] = Node(key, name, role, "unconfirmed", sub=sub)
        if seen not in node.seen_as:
            node.seen_as.append(seen)
        return node

    def _in_site(self, device) -> bool:
        return device is not None and _site_key(device) == self.site

    def _l2(self, by_name, by_device) -> list:
        links = (
            Link.objects.for_customer(self.customer)
            .filter(a_interface__device__engagement=self.engagement)
            .select_related("a_interface__device__site", "b_interface__device__site")
            .order_by("pk")
        )
        edges = {}
        for link in links:
            a_dev = link.a_interface.device
            if link.b_interface_id is not None:
                b_dev, b_port = link.b_interface.device, link.b_interface.name
            else:
                b_dev, b_port = by_name.get(link.b_device_name.casefold()), link.b_interface_name
            if not (self._in_site(a_dev) or self._in_site(b_dev)):
                continue
            a = self._device_node(a_dev, by_device)
            if b_dev is not None:
                b = self._device_node(b_dev, by_device)
            else:
                name = link.b_device_name or "Unnamed neighbour"
                seen = f"{link.get_protocol_display()} neighbour of {a_dev.name}"
                if b_port:
                    seen += f", port {b_port}"
                b = self._unconfirmed(
                    "n:" + name.casefold(), name, role_of(name), "Not collected", seen
                )
            if a is b:
                continue
            ends = sorted([(a.id, link.a_interface.name), (b.id, b_port)])
            key = tuple(ends)
            edge = edges.get(key)
            if edge is None:
                edge = edges[key] = Edge(ends[0][0], ends[1][0], "l2", ends[0][1], ends[1][1])
            edge.protocols.add(link.protocol)
            edge.reporters.add(a.id)
        return list(edges.values())

    def _l3(self, by_device) -> list:
        neighbors = (
            RoutingNeighbor.objects.for_customer(self.customer)
            .filter(device__engagement=self.engagement)
            .select_related("device__site", "peer__site", "interface")
            .order_by("pk")
        )
        edges = {}
        for neighbor in neighbors:
            if not (self._in_site(neighbor.device) or self._in_site(neighbor.peer)):
                continue
            a = self._device_node(neighbor.device, by_device)
            if neighbor.peer is not None:
                b = self._device_node(neighbor.peer, by_device)
            else:
                protocol = neighbor.get_protocol_display()
                external = neighbor.protocol == RoutingNeighbor.Protocol.BGP and neighbor.remote_as
                sub = f"AS {neighbor.remote_as}" if neighbor.remote_as else "Not collected"
                b = self._unconfirmed(
                    "ip:" + neighbor.address,
                    neighbor.address,
                    "ext" if external else "unk",
                    sub,
                    f"{protocol} neighbour of {a.name}",
                )
            if a is b:
                continue
            key = (*sorted([a.id, b.id]), neighbor.protocol)
            edge = edges.get(key)
            if edge is None:
                ends = sorted([a.id, b.id])
                edge = edges[key] = Edge(ends[0], ends[1], "l3")
            edge.protocols.add(neighbor.protocol)
            edge.subnet = edge.subnet or neighbor.subnet
            edge.reporters.add(a.id)
        return list(edges.values())

    # Layout: rows from the top of the network down, in the same place in both layers.

    def _layout(self) -> None:
        adjacent = defaultdict(set)
        for edge in self.edges["l2"] + self.edges["l3"]:
            adjacent[edge.a].add(edge.b)
            adjacent[edge.b].add(edge.a)
        rank = {key: ROLES[n.role][2] for key, n in self.nodes.items()}

        def order(key):
            return (rank[key], self.nodes[key].name.casefold(), key)

        tiers: dict[str, int] = {}
        alone = [k for k in sorted(self.nodes, key=order) if not adjacent[k]]
        for start in sorted(self.nodes, key=order):
            if start in tiers or not adjacent[start]:
                continue
            # One connected group: its highest-ranked devices on top, the rest by hops from them.
            group, queue = {start}, deque([start])
            while queue:
                for nxt in adjacent[queue.popleft()]:
                    if nxt not in group:
                        group.add(nxt)
                        queue.append(nxt)
            top = min(rank[k] for k in group)
            queue = deque(sorted((k for k in group if rank[k] == top), key=order))
            for key in queue:
                tiers[key] = 0
            while queue:
                key = queue.popleft()
                for nxt in sorted(adjacent[key], key=order):
                    if nxt not in tiers:
                        tiers[nxt] = tiers[key] + 1
                        queue.append(nxt)

        rows = defaultdict(list)
        for key in sorted(tiers, key=order):
            rows[tiers[key]].append(key)
        ordered = [rows[t] for t in sorted(rows)]
        # Each device under the devices it links to above, to keep lines from crossing.
        for _ in range(2):
            for i in range(1, len(ordered)):
                above = {k: n for n, k in enumerate(ordered[i - 1])}
                ordered[i] = _by_neighbours(ordered[i], adjacent, above)
            for i in range(len(ordered) - 2, -1, -1):
                below = {k: n for n, k in enumerate(ordered[i + 1])}
                ordered[i] = _by_neighbours(ordered[i], adjacent, below)
        if alone:
            ordered.append(alone)

        lines = []
        for row in ordered:
            lines += [row[i : i + PER_ROW] for i in range(0, len(row), PER_ROW)]
        widest = max((len(line) for line in lines), default=1)
        self.width = max(2 * PAD + widest * COLUMN, 480)
        self.height = 2 * PAD + max(len(lines), 1) * ROW
        for n, line in enumerate(lines):
            left = (self.width - len(line) * COLUMN) / 2
            for i, key in enumerate(line):
                self.nodes[key].x = round(left + i * COLUMN + COLUMN / 2)
                self.nodes[key].y = round(PAD + n * ROW + ROW / 2 - 20)

    # Filters dim what doesn't match; nothing moves.

    def _filter(self) -> None:
        for node in self.nodes.values():
            node.dimmed = not self._matches(node)
        for edge in self.edges["l2"] + self.edges["l3"]:
            edge.dimmed = self.nodes[edge.a].dimmed and self.nodes[edge.b].dimmed

    def _matches(self, node) -> bool:
        if self.role and node.role != self.role:
            return False
        if self.vendor and (node.device is None or node.device.manufacturer != self.vendor):
            return False
        return not (self.with_findings and not node.findings)

    @property
    def filtered(self) -> bool:
        return bool(self.role or self.vendor or self.with_findings)

    # What the screen shows.

    @property
    def links(self) -> list:
        return self.edges[self.layer]

    @property
    def site_devices(self) -> list:
        return sorted(
            (n for n in self.nodes.values() if n.kind == "site"), key=lambda n: n.name.casefold()
        )

    @property
    def empty(self) -> bool:
        return not self.shown

    def links_of(self, node) -> list:
        """The pinned device's links in this layer: (far end, its port, my port, edge)."""
        rows = [
            (self.nodes[e.other(node.id)], e.port_of(e.other(node.id)), e.port_of(node.id), e)
            for e in self.links
            if node.id in (e.a, e.b)
        ]
        return sorted(rows, key=lambda r: (r[2], r[0].name.casefold()))

    def summary(self) -> dict:
        """The side panel with nothing pinned: counts by role, findings, what needs attention."""
        devices = self.site_devices
        roles = defaultdict(int)
        for node in devices:
            roles[node.role] += 1
        findings = {}
        for node in devices:
            for item in node.findings:
                findings[item.key] = item
        severity = {s: 0 for s in SEVERITIES}
        for item in findings.values():
            severity[item.severity] += 1
        attention = [(n, self.node_link(n)) for n in devices if n.worst in ("critical", "high")]
        return {
            "devices": len(devices),
            "roles": [(ROLE_LABELS[k], roles[k]) for k in ROLES if roles[k]],
            "severity": [(s, SEVERITY_LABELS[s], severity[s]) for s in SEVERITIES],
            "findings": len(findings),
            "attention": attention,
            "one_sided": sum(1 for e in self.links if not e.both_ends),
            "unconfirmed": sum(1 for n in self.shown.values() if n.unconfirmed),
        }

    def link(self, **values) -> str:
        """This view with some values changed. Changing site or layer keeps the filters."""
        state = {
            "site": self.site,
            "layer": self.layer if self.layer != "l2" else "",
            "role": self.role,
            "vendor": self.vendor,
            "findings": "1" if self.with_findings else "",
            "device": self.pinned.id if self.pinned else "",
        }
        state.update(values)
        state = {k: v for k, v in state.items() if v}
        return "?" + urlencode(state) if state else "?"

    def node_link(self, node) -> str:
        """Pinning a node: another site's device opens its own site's map, pinned there."""
        if node.kind == "offsite":
            return self.link(site=_site_key(node.device), device=node.id)
        return self.link(device=node.id)

    @property
    def clear_link(self) -> str:
        return self.link(device="")

    @property
    def clear_filters_link(self) -> str:
        return self.link(role="", vendor="", findings="")

    @property
    def pinned_links(self) -> list:
        return self.links_of(self.pinned) if self.pinned else []

    @property
    def layer_links(self) -> list:
        return [
            ("l2", "L2", self.link(layer="")),
            ("l3", "L3", self.link(layer="l3")),
        ]

    def site_links(self) -> list:
        return [(s, self.link(site=s.key, device="")) for s in self.sites]

    def roles(self) -> list:
        return [(k, ROLE_LABELS[k]) for k in ROLES]

    @property
    def screen_svg(self) -> str:
        """The interactive map for the page. Every value in it is escaped by `_svg`."""
        return mark_safe(self.svg(interactive=True))  # noqa: S308

    def params(self) -> dict:
        return {
            "site": self.site,
            "layer": self.layer,
            "role": self.role,
            "vendor": self.vendor,
            "findings": "1" if self.with_findings else "",
        }

    def svg(self, *, interactive=False, title="") -> str:
        return _svg(self, interactive=interactive, title=title)


def _site_key(device) -> str:
    return str(device.site_id) if device.site_id else NO_SITE


def _by_neighbours(row, adjacent, positions) -> list:
    """Sort a row by the mean position of each node's neighbours in the next row."""

    def where(item):
        index, key = item
        near = [positions[k] for k in adjacent[key] if k in positions]
        return (sum(near) / len(near) if near else index * len(positions) / max(len(row), 1), index)

    return [key for _, key in sorted(enumerate(row), key=where)]


# Drawing. Presentation attributes only, so the exported file looks the same anywhere.

_SEVERITY_COLOURS = {
    "critical": "#B42318",
    "high": "#E8590C",
    "medium": "#B54708",
    "low": "#475467",
}
_FONT = "IBM Plex Sans, system-ui, sans-serif"
_MONO = "IBM Plex Mono, ui-monospace, monospace"


def _svg(view, *, interactive, title) -> str:
    top = HEADER if title else 0
    width, height = view.width, view.height + top
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        + (
            'class="sitemap-svg" preserveAspectRatio="xMidYMid meet" role="group" '
            f"aria-label={quoteattr(f'{view.site_name} {view.layer.upper()} map')}"
            if interactive
            else f'width="{width}" height="{height}"'
        )
        + f' font-family="{_FONT}">',
        f'<rect class="sitemap-bg" x="0" y="0" width="{width}" height="{height}" fill="#FAFBFC"/>',
    ]
    if title:
        out.append(
            f'<text x="{PAD // 2}" y="34" font-size="16" font-weight="600" fill="#101828">'
            f"{escape(title)}</text>"
        )
    out.append(f'<g transform="translate(0 {top})">')
    nodes = view.nodes
    # Links between the same two devices (LAG members, OSPF and BGP) side by side, not stacked.
    pairs = defaultdict(list)
    for edge in view.links:
        pairs[(edge.a, edge.b)].append(edge)
    for edge in view.links:
        a, b = nodes[edge.a], nodes[edge.b]
        siblings = pairs[(edge.a, edge.b)]
        nth = siblings.index(edge) - (len(siblings) - 1) / 2
        length = max(((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5, 1)
        dx, dy = round(-(b.y - a.y) / length * 8 * nth), round((b.x - a.x) / length * 8 * nth)
        both = edge.both_ends
        colour = ("#0E7490" if edge.layer == "l3" else "#64748B") if both else "#E8590C"
        weight = 3 if edge.layer == "l3" else 2
        dash = "" if both else ' stroke-dasharray="7 5"'
        opacity = "0.2" if edge.dimmed else "0.9"
        line = f'x1="{a.x + dx}" y1="{a.y + dy}" x2="{b.x + dx}" y2="{b.y + dy}"'
        out.append(f'<g class="sitemap-link" opacity="{opacity}">')
        out.append(f"<title>{escape(edge.describe(nodes))}</title>")
        if interactive:
            out.append(f'<line {line} stroke="transparent" stroke-width="14"/>')
        out.append(f'<line {line} stroke="{colour}" stroke-width="{weight}"{dash}/>')
        if edge.layer == "l3" and edge.label:
            mx, my = (a.x + b.x) // 2, round((a.y + b.y) / 2 + nth * 24)
            text_width = 7 * len(edge.label) + 12
            out.append(
                f'<rect x="{mx - text_width // 2}" y="{my - 10}" width="{text_width}" height="20" '
                'rx="4" fill="#FFFFFF" stroke="#DDE2E8"/>'
                f'<text x="{mx}" y="{my + 4}" font-size="11" font-family="{_MONO}" '
                f'text-anchor="middle" fill="#344054">{escape(edge.label)}</text>'
            )
        out.append("</g>")
    for node in sorted(view.shown.values(), key=lambda n: n.name.casefold()):
        out.append(_node_svg(view, node, interactive))
    out.append("</g></svg>")
    return "".join(out)


def _node_svg(view, node, interactive) -> str:
    pinned = view.pinned is node
    worst = node.worst
    border = _SEVERITY_COLOURS.get(worst) if worst in ("critical", "high") else None
    stroke = border or ("#0E7490" if pinned else "#98A2B3" if node.unconfirmed else "#CBD5E1")
    stroke_width = 3 if border or pinned else 2 if node.unconfirmed else 1
    dash = ' stroke-dasharray="5 4"' if node.unconfirmed else ""
    fill = "#ECFEFF" if pinned else "#F3F5F7" if node.kind == "offsite" else "#FFFFFF"
    icon = "#667085" if node.unconfirmed or node.kind == "offsite" else "#0E1A24"
    x, y = node.x, node.y
    parts = [
        f'<rect class="node-box" x="{x - 24}" y="{y - 24}" width="48" height="48" rx="12" '
        f'fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{stroke_width}"{dash}/>',
        f'<g transform="translate({x - 12} {y - 12})" fill="none" stroke="{icon}" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{ROLES[node.role][1]}"/></g>',
    ]
    if node.findings:
        badge = _SEVERITY_COLOURS.get(worst, "#475467")
        parts.append(
            f'<circle cx="{x + 24}" cy="{y - 24}" r="11" fill="{badge}" stroke="#FFFFFF" '
            f'stroke-width="2"/><text x="{x + 24}" y="{y - 20}" font-size="11" '
            f'font-weight="700" text-anchor="middle" fill="#FFFFFF">{len(node.findings)}</text>'
        )
    name = node.name if len(node.name) <= 22 else node.name[:21] + "…"
    sub = node.sub if len(node.sub) <= 26 else node.sub[:25] + "…"
    # A backing behind the labels, so a link passing under them doesn't strike them through.
    label_width = round(max(7.5 * len(name), 6 * len(sub))) + 12
    parts.append(
        f'<rect x="{x - label_width // 2}" y="{y + 30}" width="{label_width}" height="34" '
        'rx="4" fill="#FFFFFF" fill-opacity="0.9"/>'
    )
    parts.append(
        f'<text x="{x}" y="{y + 42}" font-size="12" font-weight="500" font-family="{_MONO}" '
        f'text-anchor="middle" fill="#101828">{escape(name)}</text>'
        f'<text x="{x}" y="{y + 58}" font-size="11" text-anchor="middle" '
        f'fill="#475467">{escape(sub)}</text>'
    )
    opacity = ' opacity="0.25"' if node.dimmed else ""
    body = "".join(parts)
    if not interactive:
        return f'<g class="sitemap-node"{opacity}><title>{escape(node.aria)}</title>{body}</g>'
    pressed = "true" if pinned else "false"
    return (
        f'<a class="sitemap-node" href={quoteattr(view.node_link(node))}{opacity} '
        f'data-card="card-{node.dom_id}" aria-label={quoteattr(node.aria)} '
        f'aria-pressed="{pressed}">'
        # A wider target than the icon, so the label clicks too.
        f'<rect x="{x - 60}" y="{y - 30}" width="120" height="96" fill="transparent"/>'
        f"{body}</a>"
    )


# Exports: PNG and SVG of the current view, and the site's device list as CSV.

EXPORT_FORMATS = ("png", "svg", "csv")
CSV_COLUMNS = [
    "Hostname",
    "Role",
    "IP",
    "Vendor",
    "Model",
    "Serial",
    "OS",
    "Site",
    "Findings",
    "L2 links",
    "L3 neighbours",
    "Last seen",
]


def filename(view, fmt: str, at=None) -> str:
    """Names the customer, engagement, site and time, in ASCII (see `inventory.filename`)."""
    at = at or timezone.now()
    parts = [
        "cartio-site-map",
        view.customer.name,
        view.engagement.name,
        view.site_name,
        view.layer.upper() if fmt != "csv" else "devices",
        at.strftime("%Y%m%dT%H%MZ"),
    ]
    safe = "-".join(
        "".join(c if c.isascii() and c.isalnum() else "-" for c in p).strip("-") for p in parts
    )
    while "--" in safe:
        safe = safe.replace("--", "-")
    # A PNG is drawn in the browser from the SVG this sends.
    return f"{safe}.{fmt}"


def title(view, at=None) -> str:
    at = at or timezone.now()
    return (
        f"{view.customer.name} · {view.engagement.name} · {view.site_name} · "
        f"{view.layer.upper()} · {at:%Y-%m-%d %H:%M} UTC"
    )


def csv_text(view) -> str:
    counts = {"l2": defaultdict(int), "l3": defaultdict(int)}
    for layer, edges in view.edges.items():
        for edge in edges:
            counts[layer][edge.a] += 1
            counts[layer][edge.b] += 1
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for node in view.site_devices:
        d = node.device
        row = [
            d.name,
            node.role_label,
            str(d.management_ip or ""),
            d.manufacturer,
            d.model,
            d.serial,
            d.os_version,
            view.site_name,
            str(len(node.findings)),
            str(counts["l2"][node.id]),
            str(counts["l3"][node.id]),
            d.last_seen.isoformat() if d.last_seen else "",
        ]
        writer.writerow([audit.spreadsheet_safe(value) for value in row])
    return out.getvalue()


def export(view, actor: str, fmt: str) -> tuple[bytes, str, str]:
    """The map or its device list as a file, audit-logged first: (content, name, type)."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError("Export as png, svg or csv.")
    at = timezone.now()
    name = filename(view, fmt, at)
    audit.record(
        view.customer,
        AuditEvent.Action.SITEMAP_EXPORTED,
        actor=actor,
        object_type="engagement",
        object_id=view.engagement.pk,
        object_name=view.engagement.name,
        format=fmt,
        site=view.site_name,
        layer=view.layer,
        devices=len(view.site_devices),
        # The device list is the whole site; filters only change the picture.
        filters={}
        if fmt == "csv"
        else {k: v for k, v in view.params().items() if v and k not in ("site", "layer")},
        file=name,
    )
    if fmt == "csv":
        return csv_text(view).encode(), name, "text/csv; charset=utf-8"
    content = '<?xml version="1.0" encoding="UTF-8"?>\n' + view.svg(title=title(view, at))
    return content.encode(), name, "image/svg+xml"
