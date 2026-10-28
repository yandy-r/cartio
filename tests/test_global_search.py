"""Global search (YAN-1740, DESIGN.md section 11)."""

import re
from pathlib import Path

import pytest
from cartio_controller.collectors.models import Collector, Customer, Device, Engagement, Site
from cartio_controller.engineers import views
from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db

STATIC = Path(views.__file__).parent / "static" / "engineers"


@pytest.fixture
def engineer(client):
    user = _engineer()
    _signed_in(client, user)
    return user


def _customer(name, **extra):
    customer = Customer.objects.create(name=name, **extra)
    engagement = Engagement.objects.for_customer(customer).create(name="Q4 review", status="active")
    return customer, engagement


def _device(customer, engagement, name, **extra):
    return Device.objects.for_customer(customer).create(engagement=engagement, name=name, **extra)


def _search(client, q="", **params):
    page = client.get("/search", {"q": q, **params}, HTTP_HX_REQUEST="true")
    assert page.status_code == 200
    return page.content.decode()


def _titles(page):
    return re.findall(r'<span class="search-title[^"]*">([^<]*)</span>', page)


@pytest.fixture
def northwind():
    customer, engagement = _customer("Northwind Clinics")
    hq = Site.objects.for_customer(customer).create(name="HQ data center")
    _device(
        customer,
        engagement,
        "fw-hq-01",
        site=hq,
        manufacturer="Fortinet",
        model="FortiGate 60F",
        serial="FG60F0000004821",
        management_ip="192.0.2.10",
    )
    _device(
        customer,
        engagement,
        "core-sw-01",
        site=hq,
        manufacturer="Cisco",
        model="C9300-48P",
        serial="FOC2419X0AB",
        management_ip="192.0.2.20",
    )
    return customer, engagement


def test_the_sidebar_button_opens_search(client, engineer):
    page = client.get("/").content.decode()
    assert "data-search-open" in page
    assert 'aria-keyshortcuts="Control+K Meta+K"' in page
    assert 'id="search-dialog"' in page
    assert "engineers/js/search.js" in page
    assert "disabled" not in re.search(r'<button[^>]*class="search-button"[^>]*>', page).group(0)


def test_search_script_handles_the_keys():
    script = (STATIC / "js" / "search.js").read_text()
    for key in ('"k"', "ArrowDown", "ArrowUp", "Enter", "showModal"):
        assert key in script


def test_words_match_in_any_order_and_in_part(client, engineer, northwind):
    for query in ("fortigate 60f", "60F fortigate", "192.0.2.1", "fg60f", "hq data fw"):
        assert "fw-hq-01" in _titles(_search(client, query)), query
    assert "fw-hq-01" not in _titles(_search(client, "fortigate 9300"))


def test_a_device_opens_inventory_filtered_to_it(client, engineer, northwind):
    customer, engagement = northwind
    page = _search(client, "fw-hq")
    assert (
        f'href="/customers/{customer.pk}/engagements/{engagement.pk}/inventory?q=fw-hq-01"' in page
    )
    assert "Northwind Clinics · HQ data center · Fortinet FortiGate 60F · 192.0.2.10" in page


def test_customer_words_match_its_devices(client, engineer, northwind):
    assert "core-sw-01" in _titles(_search(client, "northwind core"))


def test_results_are_grouped_and_name_their_customer(client, engineer, northwind):
    customer, engagement = northwind
    Collector.objects.for_customer(customer).create(name="nw-hq-vm")
    page = _search(client, "northwind")
    groups = re.findall(r'id="search-group-\w+">([^<]*)<', page)
    assert groups == ["Customers", "Devices", "Collectors", "Engagements"]
    assert f'href="/customers/{customer.pk}/engagements/{engagement.pk}"' in page
    assert f'href="/customers/{customer.pk}/engagements/{engagement.pk}/collectors"' in page
    assert "Northwind Clinics · Active" in page
    assert '<span id="search-count" hx-swap-oob="innerHTML">' in page


def test_large_groups_say_how_many_more(client, engineer):
    customer, engagement = _customer("Acme")
    for n in range(10):
        _device(customer, engagement, f"acc-sw-{n:02}")
    page = _search(client, "acc-sw")
    assert len(_titles(page)) == 8
    assert "8 of 10" in page


def test_no_query_shows_screens_and_customers(client, engineer, northwind):
    page = _search(client)
    assert "Jump to" in page and "All customers" in page and "Northwind Clinics" in page
    assert "Type to search" in page


def test_no_match_says_so(client, engineer, northwind):
    page = _search(client, "zzz-nothing")
    assert "No matches for “zzz-nothing”" in page
    assert "0 results" in page


def test_screens_keep_the_current_customer_and_engagement(client, engineer, northwind):
    customer, engagement = northwind
    page = _search(client, "inventory", customer=customer.pk, engagement=engagement.pk)
    assert f'href="/customers/{customer.pk}/engagements/{engagement.pk}/inventory"' in page
    # Screens the engineer has no permission for aren't offered.
    assert "Audit log" not in _titles(
        _search(client, "audit", customer=customer.pk, engagement=engagement.pk)
    )
    engineer.user_permissions.add(Permission.objects.get(codename="view_auditevent"))
    assert "Audit log" in _titles(
        _search(client, "audit", customer=customer.pk, engagement=engagement.pk)
    )


def test_only_current_customer_scope(client, engineer, northwind):
    customer, _ = northwind
    other, other_engagement = _customer("Larkfield Schools")
    _device(other, other_engagement, "fw-lk-01", model="FortiGate 100F")
    both = _titles(_search(client, "fortigate"))
    assert "fw-hq-01" in both and "fw-lk-01" in both
    only = _titles(_search(client, "fortigate", customer=customer.pk, scope="current"))
    assert only == ["fw-hq-01"]


def test_results_never_cross_customers(client, engineer, northwind):
    """Each customer's devices link to that customer, even with a lookalike elsewhere."""
    customer, engagement = northwind
    other, other_engagement = _customer("Larkfield Schools")
    twin = _device(other, other_engagement, "fw-hq-01", management_ip="10.60.0.1")
    page = _search(client, "fw-hq-01")
    links = re.findall(r'href="(/customers/\d+/engagements/\d+/inventory)\?q=fw-hq-01"', page)
    assert sorted(links) == sorted(
        [
            f"/customers/{customer.pk}/engagements/{engagement.pk}/inventory",
            f"/customers/{other.pk}/engagements/{other_engagement.pk}/inventory",
        ]
    )
    # The scope toggle limits to the named customer, and another customer's engagement can't
    # be borrowed to reach its records.
    only = _search(
        client, "fw-hq-01", customer=customer.pk, engagement=other_engagement.pk, scope="current"
    )
    assert "Larkfield" not in only and "10.60.0.1" not in only
    assert twin.customer_id == other.pk


def test_every_device_query_names_its_customer(client, engineer, northwind):
    _customer("Larkfield Schools")
    with CaptureQueriesContext(connection) as queries:
        _search(client, "fw")
    device_queries = [q["sql"] for q in queries if 'FROM "collectors_device"' in q["sql"]]
    assert device_queries
    assert all('"collectors_device"."customer_id" =' in sql for sql in device_queries)


def test_archived_customers_and_engagements_are_not_searched(client, engineer, northwind):
    customer, engagement = northwind
    old, old_engagement = _customer("Old Customer")
    _device(old, old_engagement, "fw-old-01")
    Customer.objects.filter(pk=old.pk).update(archived_at="2026-01-01T00:00Z")
    archived = Engagement.objects.for_customer(customer).create(name="2024 review")
    _device(customer, archived, "fw-2024-01")
    Engagement.objects.for_customer(customer).filter(pk=archived.pk).update(
        archived_at="2026-01-01T00:00Z"
    )
    titles = _titles(_search(client, "fw"))
    assert "fw-hq-01" in titles
    assert "fw-old-01" not in titles and "fw-2024-01" not in titles
    assert "Old Customer" not in _search(client, "old")


def test_on_site_customers_have_no_device_results_and_search_says_why(client, engineer):
    _customer("Ostrander Manufacturing", data_mode="on_site")
    _customer("Bluepeak Credit Union", data_mode="on_site", live_view_allowed=True)
    page = _search(client, "fw")
    assert "Ostrander Manufacturing keeps its data on site with inside access only" in page
    assert "Bluepeak Credit Union keeps its data on site with live view" in page
    # Still found by name: customer and collector health stay on the controller.
    assert "Ostrander Manufacturing" in _titles(_search(client, "ostrander"))


def test_on_site_devices_are_never_queried(client, engineer):
    customer, _ = _customer("Ostrander Manufacturing", data_mode="on_site")
    with CaptureQueriesContext(connection) as queries:
        _search(client, "fw", customer=customer.pk, scope="current")
    assert not [q for q in queries if 'FROM "collectors_device"' in q["sql"]]


def test_search_needs_a_signed_in_engineer(client):
    assert client.get("/search", {"q": "fw"}).status_code == 302
