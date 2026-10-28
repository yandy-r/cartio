"""The app shell's context: which customer and engagement a screen shows, and the switcher.

Per-customer screens take the customer, and optionally the engagement, from the URL
(`customers/<id>/engagements/<id>/...`). Without an engagement in the URL, a screen shows the
customer's current one. The switcher links to the same screen for another context.
"""

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from cartio_controller.collectors.models import Customer, Engagement

# Per-customer screens, by URL name. The switcher keeps the engineer on one of these.
SCREENS = {
    "customer": "Overview",
    "sitemap": "Site map",
    "inventory": "Inventory",
    "findings": "Findings",
    "compare": "Compare",
    "report": "Report",
    "collectors": "Collectors & jobs",
    "jobs": "Collectors & jobs",
    "audit": "Audit log",
}

# Which engagement a screen shows when the URL doesn't name one.
_CURRENT_FIRST = {
    Engagement.Status.ACTIVE: 0,
    Engagement.Status.PLANNED: 1,
    Engagement.Status.COMPLETED: 2,
}


def engagements_of(customer) -> list:
    """The customer's open engagements, current first."""
    rows = list(
        Engagement.objects.for_customer(customer)
        .filter(archived_at__isnull=True)
        .order_by("-start_date", "-pk")
    )
    rows.sort(key=lambda e: _CURRENT_FIRST.get(e.status, 3))
    return rows


def resolve(customer_pk, engagement_pk=None):
    """The customer, its engagements and the engagement to show. 404 for another customer's."""
    customer = get_object_or_404(Customer, pk=customer_pk)
    engagements = engagements_of(customer)
    if engagement_pk is None:
        return customer, engagements, (engagements[0] if engagements else None)
    for engagement in engagements:
        if engagement.pk == engagement_pk:
            return customer, engagements, engagement
    # An archived engagement, or one that belongs to another customer, isn't shown.
    raise Http404


def screen_url(screen, customer, engagement=None) -> str:
    kwargs = {"customer": customer.pk}
    if engagement is not None:
        kwargs["engagement"] = engagement.pk
    return reverse(f"engineers:{screen}", kwargs=kwargs)


def switcher_customers(screen, query=""):
    """Customers for the switcher menu, each with a link to the same screen."""
    rows = Customer.objects.filter(archived_at__isnull=True)
    if query:
        rows = rows.filter(name__icontains=query)
    rows = list(rows.order_by("name"))
    for customer in rows:
        customer.switch_url = screen_url(screen, customer)
    return rows


def context(screen, customer, engagements, engagement) -> dict:
    """Template context for the shell around a per-customer screen."""
    for each in engagements:
        each.switch_url = screen_url(screen, customer, each)
    return {
        "screen": screen,
        "customer": customer,
        "engagement": engagement,
        "engagements": engagements,
        "switcher_customers": switcher_customers(screen),
    }
