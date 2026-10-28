"""Global search (DESIGN.md section 11): customers, engagements, collectors, devices and screens.

Each customer's records are searched in that customer's own scope (`for_customer`), and every
result links to a URL with its customer and engagement. Words match in any order and in part:
each word has to appear somewhere in the result, or in its customer's name.

Findings and integrations join the search when those features exist (YAN-1738, YAN-1742).
"""

from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse

from cartio_controller.collectors.models import Customer, Device

from . import onboarding, shell
from .health import collectors as _collectors

MAX_QUERY = 200
MAX_WORDS = 8

# Results shown per group; the group header says when there are more.
LIMITS = {"customers": 5, "devices": 8, "collectors": 5, "engagements": 5, "screens": 5}

HEALTH_TAGS = {
    "online": "Online",
    "offline": "Offline",
    "never checked in": "Never checked in",
    "not enrolled": "Not enrolled",
    "revoked": "Revoked",
}


def words(query: str) -> list[str]:
    return query.casefold().split()[:MAX_WORDS]


def _hit(terms, *values) -> bool:
    haystack = " ".join(str(value) for value in values if value).casefold()
    return all(term in haystack for term in terms)


def _mode(customer) -> str:
    if not customer.is_on_site:
        return "Hosted"
    return "On site · live view" if customer.live_view_allowed else "On site · inside access only"


class Search:
    """The results for one query, grouped by type.

    `current` is the customer on the page the engineer searched from, or None (All customers).
    `only_current` limits the results to that customer.
    """

    def __init__(self, user, query, current=None, current_engagement=None, only_current=False):
        self.user = user
        self.query = query.strip()[:MAX_QUERY]
        self.terms = words(self.query)
        self.current = current
        self.current_engagement = current_engagement
        self.only_current = only_current and current is not None
        self.customers = self._customers()
        self.groups = []
        self.notes = []
        if self.terms:
            self._search()
        else:
            self._jump_to()
        for index, result in enumerate(r for g in self.groups for r in g["results"]):
            result["index"] = index
        self.count = sum(len(g["results"]) for g in self.groups)

    def _customers(self) -> list:
        # Every engineer may see every open customer (the switcher lists the same ones).
        if self.only_current:
            return [self.current]
        return list(Customer.objects.filter(archived_at__isnull=True).order_by("name"))

    def _group(self, key, name, results):
        if results:
            limit = LIMITS[key]
            more = f"{limit} of {len(results)}" if len(results) > limit else ""
            self.groups.append({"key": key, "name": name, "results": results[:limit], "more": more})

    # Without a query: screens to jump to, and customers.

    def _jump_to(self):
        self._group("screens", "Jump to", self._screens())
        self._group("customers", "Customers", [self._customer(c) for c in self.customers])

    def _search(self):
        terms = self.terms
        self._group(
            "customers",
            "Customers",
            [self._customer(c) for c in self.customers if _hit(terms, c.name, _mode(c))],
        )
        devices = []
        for customer in self.customers:
            devices += self._devices(customer)
        # Host names that start with the first word first, then by name.
        devices.sort(key=lambda r: (not r["title"].casefold().startswith(terms[0]), r["sort"]))
        self._group("devices", "Devices", devices)
        collectors = []
        engagements = []
        for customer in self.customers:
            collectors += self._collectors(customer)
            engagements += self._engagements(customer)
        self._group("collectors", "Collectors", collectors)
        self._group("engagements", "Engagements", engagements)
        self._group(
            "screens", "Screens", [s for s in self._screens() if _hit(terms, s["title"], s["sub"])]
        )
        self._data_mode_notes()

    def _customer(self, customer):
        engagements = shell.engagements_of(customer)
        return {
            "title": customer.name,
            "sub": _mode(customer),
            "url": shell.screen_url("customer", customer, engagements[0] if engagements else None),
            "icon": "customer",
            "target": "Overview",
        }

    def _devices(self, customer) -> list:
        # On site customers keep their network data off the controller (ADR-0002, ADR-0005).
        if customer.is_on_site:
            return []
        # Words in the customer's name match every one of its devices.
        terms = [t for t in self.terms if t not in customer.name.casefold()]
        rows = (
            Device.objects.for_customer(customer)
            .filter(engagement__archived_at__isnull=True)
            .select_related("engagement", "site")
        )
        for term in terms:
            rows = rows.filter(
                Q(name__icontains=term)
                | Q(management_ip__icontains=term)
                | Q(serial__icontains=term)
                | Q(manufacturer__icontains=term)
                | Q(model__icontains=term)
                | Q(site__name__icontains=term)
            )
        results = []
        for device in rows.order_by("name")[: LIMITS["devices"] * 4]:
            model = " ".join(v for v in (device.manufacturer, device.model) if v)
            sub = [customer.name, device.site.name if device.site else "", model]
            sub += [str(device.management_ip or ""), f"SN {device.serial}" if device.serial else ""]
            url = shell.screen_url("inventory", customer, device.engagement)
            results.append(
                {
                    "title": device.name,
                    "sort": device.name.casefold(),
                    "sub": " · ".join(v for v in sub if v),
                    "detail": device.engagement.name,
                    "url": f"{url}?{urlencode({'q': device.name})}",
                    "icon": "device",
                    "mono": True,
                    "target": "Inventory",
                }
            )
        return results

    def _collectors(self, customer) -> list:
        results = []
        engagement = self._engagement_for(customer)
        for collector in _collectors(customer, revoked=True):
            site = collector.site.name if collector.site else ""
            tag = HEALTH_TAGS.get(collector.health, "")
            if _hit(self.terms, collector.name, collector.hostname, site, tag, customer.name):
                results.append(
                    {
                        "title": collector.name,
                        "sub": " · ".join(v for v in (customer.name, site) if v),
                        "tag": tag,
                        "tag_class": collector.health.replace(" ", "-"),
                        "url": shell.screen_url("collectors", customer, engagement),
                        "icon": "collector",
                        "mono": True,
                        "target": "Collectors",
                    }
                )
        return results

    def _engagements(self, customer) -> list:
        results = []
        for engagement in shell.engagements_of(customer):
            status = engagement.get_status_display()
            if _hit(self.terms, engagement.name, status, customer.name):
                results.append(
                    {
                        "title": engagement.name,
                        "sub": f"{customer.name} · {status}",
                        "url": shell.screen_url("customer", customer, engagement),
                        "icon": "engagement",
                        "target": "Overview",
                    }
                )
        return results

    def _engagement_for(self, customer):
        """The current engagement for the page's customer, else the customer's current one."""
        if self.current is not None and customer.pk == self.current.pk:
            if self.current_engagement is not None:
                return self.current_engagement
        engagements = shell.engagements_of(customer)
        return engagements[0] if engagements else None

    def _screens(self) -> list:
        """Screens the engineer can open, for the page's customer and engagement."""
        rows = [("All customers", "Every customer and collector health", "customers")]
        if onboarding.can_onboard(self.user):
            rows.append(("Onboard a customer", "Add a customer, sites and collectors", "onboard"))
        if self.current is not None:
            rows += [
                ("Overview", "Sites and collectors", "customer"),
                ("Inventory", "Every device, filter and export", "inventory"),
                ("Collectors & jobs", "Collector health, add and revoke", "collectors"),
            ]
            if self.user.has_perm("collectors.view_collectionrun"):
                rows.append(("Jobs", "Collection runs and their results", "jobs"))
            if self.user.has_perm("collectors.view_auditevent"):
                rows.append(("Audit log", "Who did what, and export", "audit"))
        results = []
        for title, sub, screen in rows:
            if screen in shell.SCREENS:
                url = shell.screen_url(screen, self.current, self.current_engagement)
                sub = f"{sub} · {self.current.name}"
            else:
                url = reverse(f"engineers:{screen}")
            results.append(
                {"title": title, "sub": sub, "url": url, "icon": "screen", "target": "Screen"}
            )
        return results

    def _data_mode_notes(self):
        """Say which customers' devices aren't searched here, and why."""
        locked = [c.name for c in self.customers if c.inside_access_only]
        live = [c.name for c in self.customers if c.is_on_site and c.live_view_allowed]
        if locked:
            self.notes.append(
                f"{_names(locked)} {_keep(locked)} data on site with inside access only, so "
                f"{_their(locked)} devices aren't searchable here."
            )
        if live:
            self.notes.append(
                f"{_names(live)} {_keep(live)} data on site with live view, so none of it is "
                f"stored here. Searching {_their(live)} devices through the collector isn't "
                f"available yet."
            )


def _names(names) -> str:
    if len(names) <= 3:
        return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
    return f"{', '.join(names[:2])} and {len(names) - 2} more customers"


def _keep(names) -> str:
    return "keeps its" if len(names) == 1 else "keep their"


def _their(names) -> str:
    return "its" if len(names) == 1 else "their"
