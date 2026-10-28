"""The All customers screen (DESIGN.md section 1): every customer, what needs attention, activity.

This is the one screen that lists more than one customer. Each customer's rows still come from
its own scoped queries (`for_customer`), so nothing on a row can come from another customer.
"""

from urllib.parse import urlencode

from django.utils import timezone

from cartio_controller.collectors.models import AuditEvent, Customer, Device, Site

from . import health, shell

# Tabs over the list, by their URL value. All, Hosted and On site leave archived customers out.
TABS = {
    "all": "All",
    "hosted": "Hosted",
    "on_site": "On site",
    "archived": "Archived",
}

ACTIVITY_SHOWN = 8
MAX_QUERY = 200


def _tags(customer) -> list[str]:
    tags = customer.get_compliance_display()
    if customer.is_on_site:
        tags.append("Live view on" if customer.live_view_allowed else "Inside access only")
    return tags


def _row(customer, now):
    """One customer's row, from that customer's own records only."""
    engagements = shell.engagements_of(customer)
    customer.engagement = engagements[0] if engagements else None
    customer.tags = _tags(customer)
    customer.site_list = list(
        Site.objects.for_customer(customer).filter(archived_at__isnull=True).order_by("name")
    )
    customer.collector_list = health.collectors(customer, now)
    # On site customers' devices stay in their own store (ADR-0002), so there's nothing to count.
    customer.device_count = None
    if not customer.is_on_site and customer.engagement is not None:
        customer.device_count = (
            Device.objects.for_customer(customer).filter(engagement=customer.engagement).count()
        )
    customer.open_url = shell.screen_url("customer", customer, customer.engagement)
    return customer


def _matches(customer, query: str) -> bool:
    names = [customer.name, *customer.tags]
    names += [site.name for site in customer.site_list]
    names += [collector.name for collector in customer.collector_list]
    return any(query in name.casefold() for name in names)


def _attention(customers, now) -> list[dict]:
    """Collectors that need an engineer: offline first, then not checked in, then not enrolled."""
    order = {"offline": 0, "never checked in": 1, "not enrolled": 2}
    items = []
    for customer in customers:
        for collector in customer.collector_list:
            if collector.health not in order:
                continue
            if collector.health == "offline":
                title = f"{collector.name} is offline"
                detail = (
                    f"Last check-in {timezone.localtime(collector.last_check_in):%Y-%m-%d %H:%M}"
                )
            elif collector.health == "never checked in":
                title = f"{collector.name} has never checked in"
                detail = "Enrolled, but no check-in yet"
            else:
                title = f"{collector.name} is waiting to enroll"
                detail = "Waiting for its one-time enrollment token"
            items.append(
                {
                    "rank": order[collector.health],
                    "health": collector.health,
                    "title": title,
                    "customer": customer,
                    "detail": detail,
                    "url": shell.screen_url("customer", customer, customer.engagement)
                    + "#collectors",
                }
            )
    items.sort(key=lambda item: (item["rank"], item["customer"].name, item["title"]))
    return items


def _activity(customers) -> list:
    """The latest audit entries, read customer by customer and merged."""
    events = []
    for customer in customers:
        rows = AuditEvent.objects.for_customer(customer).order_by("-at")[:ACTIVITY_SHOWN]
        for event in rows:
            event.customer_name = customer.name
            events.append(event)
    events.sort(key=lambda event: event.at, reverse=True)
    return events[:ACTIVITY_SHOWN]


class Overview:
    """What the All customers screen shows, read from the query string."""

    def __init__(self, params, now=None):
        now = now or timezone.now()
        self.tab = params.get("mode", "all")
        if self.tab not in TABS:
            self.tab = "all"
        self.query = params.get("q", "").strip()[:MAX_QUERY]

        current = [_row(c, now) for c in Customer.objects.filter(archived_at__isnull=True)]
        archived = Customer.objects.filter(archived_at__isnull=False)
        self.counts = {
            "all": len(current),
            "hosted": sum(1 for c in current if not c.is_on_site),
            "on_site": sum(1 for c in current if c.is_on_site),
            "archived": archived.count(),
        }
        self.any_customers = bool(current) or self.counts["archived"] > 0

        if self.tab == "archived":
            shown = [_row(c, now) for c in archived]
        elif self.tab == "hosted":
            shown = [c for c in current if not c.is_on_site]
        elif self.tab == "on_site":
            shown = [c for c in current if c.is_on_site]
        else:
            shown = current
        if self.query:
            folded = self.query.casefold()
            shown = [c for c in shown if _matches(c, folded)]
        self.customers = shown

        collectors = [k for c in current for k in c.collector_list]
        engagements = [c.engagement for c in current if c.engagement is not None]
        self.collector_count = len(collectors)
        self.online_count = sum(1 for k in collectors if k.health == "online")
        self.offline_count = sum(1 for k in collectors if k.health == "offline")
        self.active_count = sum(1 for e in engagements if e.status == "active")
        self.planned_count = sum(1 for e in engagements if e.status == "planned")
        self.device_count = sum(c.device_count or 0 for c in current)
        self.attention = _attention(current, now)
        self.activity = _activity(current)

    def tabs(self) -> list[dict]:
        def url(tab):
            params = {} if tab == "all" else {"mode": tab}
            if self.query:
                params["q"] = self.query
            return "?" + urlencode(params)

        return [
            {"key": key, "label": label, "count": self.counts[key], "url": url(key)}
            for key, label in TABS.items()
        ]
