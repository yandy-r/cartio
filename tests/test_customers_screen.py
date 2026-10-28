"""The All customers screen (YAN-1733, DESIGN.md section 1)."""

from datetime import timedelta

import pytest
from cartio_controller.collectors import audit
from cartio_controller.collectors.models import (
    AuditEvent,
    Collector,
    Customer,
    Device,
    Engagement,
    Site,
)
from django.contrib.auth.models import Permission
from django.utils import timezone

from tests.test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db


@pytest.fixture
def engineer(client):
    user = _engineer()
    _signed_in(client, user)
    return user


def _page(client, **params):
    response = client.get("/", params)
    assert response.status_code == 200
    return response.content.decode()


def _acme():
    acme = Customer.objects.create(name="Acme")
    engagement = Engagement.objects.for_customer(acme).create(name="Q4 review", status="active")
    Site.objects.for_customer(acme).create(name="Harbor HQ")
    now = timezone.now()
    Collector.objects.for_customer(acme).create(
        name="acme-vm", enrolled_at=now, last_check_in=now, hostname="vm1", version="0.4.0"
    )
    make = Device.objects.for_customer(acme).create
    make(engagement=engagement, name="core-01")
    make(engagement=engagement, name="edge-01")
    return acme, engagement


def test_one_row_per_customer_with_its_engagement_sites_collectors_and_devices(client, engineer):
    acme, engagement = _acme()
    Customer.objects.create(name="Ostrander", data_mode="on_site")
    Customer.objects.create(name="Bluepeak", data_mode="on_site", live_view_allowed=True)

    page = _page(client)
    for text in (
        "Connected once · 3 customers · 1 collector",
        "Q4 review",
        "Active · 1 site",
        "acme-vm",
        "0.4.0",
        "Last check-in",
        "Certificate",
        "Inside access only",
        "Live view on",
        "Live view is off for this customer.",
    ):
        assert text in page
    # Two devices in the current engagement, and the row opens that customer and engagement.
    assert '<td class="mono">2</td>' in page
    assert f'href="/customers/{acme.pk}/engagements/{engagement.pk}"' in page


def test_a_row_shows_only_its_own_customers_data(client, engineer):
    acme, _ = _acme()
    globex = Customer.objects.create(name="Globex")
    Collector.objects.for_customer(globex).create(name="globex-vm")

    page = _page(client)
    acme_row = page[page.index(">Acme<") : page.index(">Globex<")]
    assert "acme-vm" in acme_row and "globex-vm" not in acme_row
    globex_row = page[page.index(">Globex<") :]
    assert "acme-vm" not in globex_row.split("</tr>")[0]
    assert '<td class="mono">2</td>' not in globex_row.split("</tr>")[0]


def test_search_covers_customers_sites_and_collectors(client, engineer):
    _acme()
    Customer.objects.create(name="Globex")

    for query in ("acme", "harbor", "ACME-VM"):
        page = _page(client, q=query)
        assert ">Acme<" in page and ">Globex<" not in page
    assert "No customers match “nothing”." in _page(client, q="nothing")


def test_search_and_tabs_swap_the_list_in_place(client, engineer):
    _acme()
    Customer.objects.create(name="Ostrander", data_mode="on_site")

    response = client.get("/", {"q": "ostr"}, headers={"HX-Request": "true"})
    page = response.content.decode()
    assert "<html" not in page
    assert ">Ostrander<" in page and ">Acme<" not in page
    assert response["HX-Push-Url"] == "/?q=ostr"

    # Back to a pushed URL with no cached snapshot: htmx asks for the whole page.
    page = client.get(
        "/",
        {"q": "ostr"},
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    ).content.decode()
    assert "<html" in page and ">Ostrander<" in page

    page = _page(client, mode="on_site")
    assert ">Ostrander<" in page and ">Acme<" not in page
    page = _page(client, mode="hosted")
    assert ">Acme<" in page and ">Ostrander<" not in page


def test_archived_customers_are_left_out_unless_asked_for(client, engineer):
    _acme()
    gone = Customer.objects.create(name="Gone")
    Collector.objects.for_customer(gone).create(name="gone-vm")
    Customer.objects.filter(pk=gone.pk).update(archived_at=timezone.now())

    page = _page(client)
    assert ">Gone<" not in page and "gone-vm" not in page
    assert "Connected once · 1 customer · 1 collector" in page
    page = _page(client, mode="archived")
    assert ">Gone<" in page and ">Acme<" not in page


def test_only_engineers_see_the_list(client):
    _acme()
    assert client.get("/").status_code == 302
    # Only staff accounts can sign in; a session for any other account is refused anyway.
    client.force_login(_engineer("someone", is_staff=False))
    response = client.get("/")
    assert response.status_code == 403
    assert "Acme" not in response.content.decode()


def test_needs_attention_comes_from_collector_state(client, engineer):
    acme, _ = _acme()
    collectors = Collector.objects.for_customer(acme)
    collectors.create(
        name="acme-old",
        enrolled_at=timezone.now(),
        last_check_in=timezone.now() - timedelta(days=2),
    )
    collectors.create(name="acme-new")

    page = _page(client)
    attention = page[page.index("Needs attention") : page.index("Recent activity")]
    assert "acme-old is offline" in attention
    assert "acme-new is waiting to enroll" in attention
    assert "acme-vm" not in attention
    assert attention.index("acme-old") < attention.index("acme-new")


def test_recent_activity_comes_from_each_customers_audit_log(client, engineer):
    acme, _ = _acme()
    globex = Customer.objects.create(name="Globex")
    audit.record(
        acme,
        AuditEvent.Action.INVENTORY_EXPORTED,
        object_type="engagement",
        object_id=1,
        object_name="Q4 review",
        actor="dana",
    )
    audit.record(
        globex, AuditEvent.Action.RUN_STARTED, object_type="run", object_id=2, object_name="health"
    )

    page = _page(client)
    activity = page[page.index("Recent activity") :]
    assert "Inventory exported · Acme" in activity
    assert "Q4 review · by dana" in activity
    assert "Collection run started · Globex" in activity


def test_empty_state_offers_new_customer(client, engineer):
    page = _page(client)
    assert "No customers yet" in page
    assert "Ask an admin to add customers." in page

    engineer.user_permissions.add(
        *Permission.objects.filter(
            codename__in=["add_customer", "add_site", "add_engagement", "add_collector"]
        )
    )
    page = _page(client)
    assert 'href="/customers/new"' in page


def test_new_customer_and_new_engagement_go_to_their_flows(client, engineer):
    acme, _ = _acme()
    page = _page(client)
    assert "New customer" not in page and "+ New engagement" not in page

    engineer.user_permissions.add(
        *Permission.objects.filter(
            codename__in=["add_customer", "add_site", "add_engagement", "add_collector"]
        )
    )
    page = _page(client)
    assert 'href="/customers/new"' in page
    assert 'href="/admin/collectors/engagement/add/"' in page
    assert f'href="/admin/collectors/engagement/add/?customer={acme.pk}"' in page
