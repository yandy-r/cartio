"""Onboard a customer (YAN-1734, DESIGN.md section 2)."""

import re

import pytest
from cartio_controller.collectors.models import (
    AuditEvent,
    Collector,
    Customer,
    Engagement,
    Site,
)
from django.contrib.auth.models import Permission
from django.utils import timezone

from tests.test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db

URL = "/customers/new"


def _grant(user, *codenames):
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))


@pytest.fixture
def engineer(client):
    user = _engineer()
    _grant(user, "add_customer", "add_site", "add_engagement", "add_collector")
    _signed_in(client, user)
    return user


def _post(client, action="next", **fields):
    return client.post(URL, {"action": action, **fields})


def _page(client):
    response = client.get(URL)
    assert response.status_code == 200
    return response.content.decode()


def _to_review(client, name="Cedar Valley Dental", mode="hosted", live="", **extra):
    assert _post(client, name=name, compliance=["hipaa"], notes="Ask for Dana").status_code == 302
    assert _post(client, data_mode=mode, live_view=live).status_code == 302
    sites = {
        "site-0-name": "Main office",
        "site-0-location": "Springfield",
        "site-0-seeds": "192.0.2.1, 192.0.2.0/24\nCORE-SW1.example.net",
    }
    if extra.get("two_sites", True):
        assert _post(client, "add_site", **sites).status_code == 302
        sites |= {"site-1-name": "Westside clinic", "site-1-seeds": ""}
    assert _post(client, **sites).status_code == 302
    engagement = {
        "engagement_name": "Initial network assessment",
        "engagement_status": "planned",
        "start_date": "2026-11-03",
        "end_date": "2026-11-21",
        "assessment": "Baseline",
    }
    assert _post(client, **engagement).status_code == 302
    collectors = {"collector-0": "on", "collector-0-name": "cvd-main-vm"}
    if extra.get("two_sites", True):
        collectors |= {"collector-1": "on", "collector-1-name": "cvd-westside-vm"}
    assert _post(client, **collectors).status_code == 302
    assert "Review and create" in _page(client)


def test_new_customer_opens_the_wizard_for_engineers_who_may_create_everything(client):
    user = _engineer()
    _grant(user, "add_customer")
    _signed_in(client, user)
    # Adding a customer alone isn't enough: the wizard also creates sites and collectors.
    assert URL not in client.get("/").content.decode()
    assert client.get(URL).status_code == 403
    assert _post(client, name="Acme").status_code == 403

    _grant(user, "add_site", "add_engagement", "add_collector")
    assert f'href="{URL}"' in client.get("/").content.decode()
    assert "Who is the customer?" in _page(client)


def test_signed_out_visitors_go_to_sign_in(client):
    assert client.get(URL)["Location"].startswith("/sign-in")


def test_creating_the_customer_writes_every_record_and_audits_it(client, engineer):
    _to_review(client)
    response = _post(client, "create")
    assert response.status_code == 200
    page = response.content.decode()

    customer = Customer.objects.get(name="Cedar Valley Dental")
    assert customer.data_mode == "hosted"
    assert customer.live_view_allowed is False
    assert customer.compliance == ["hipaa"]
    assert customer.notes == "Ask for Dana"
    sites = {s.name: s for s in Site.objects.for_customer(customer)}
    # Seed hints are stored as the API stores them: canonical and lower-case.
    assert sites["Main office"].seed_hints == ["192.0.2.1", "192.0.2.0/24", "core-sw1.example.net"]
    assert sites["Main office"].location == "Springfield"
    assert sites["Westside clinic"].seed_hints == []
    engagement = Engagement.objects.for_customer(customer).get()
    assert (engagement.name, engagement.status, str(engagement.start_date)) == (
        "Initial network assessment",
        "planned",
        "2026-11-03",
    )

    # Each collector has a one-time token and an enroll command, shown on this page only.
    collectors = Collector.objects.for_customer(customer)
    assert sorted(c.name for c in collectors) == ["cvd-main-vm", "cvd-westside-vm"]
    command = "uv run cartio-collector enroll --controller http://testserver --token "
    tokens = re.findall(re.escape(command) + r"([0-9a-f]{64})<", page)
    assert {Collector.for_enrollment_token(t) for t in tokens} == set(collectors)
    assert "Shown once" in page
    assert "Cedar Valley Dental is set up" in page

    events = AuditEvent.objects.for_customer(customer)
    assert {(e.action, e.object_type) for e in events} == {
        ("record.created", "customer"),
        ("record.created", "site"),
        ("record.created", "engagement"),
        ("collector.created", "collector"),
    }
    assert events.count() == 6
    assert {e.actor for e in events} == {"engineer"}
    assert all(e.detail["via"] == "onboarding" for e in events)
    created = events.get(object_type="customer")
    assert created.detail["data_mode"] == "hosted"
    # No token in the audit log.
    assert all("token" not in str(e.detail) for e in events)


def test_onboard_another_customer_starts_fresh_and_never_reopens_the_last_one(client, engineer):
    _to_review(client)
    _post(client, "create")
    page = _page(client)
    assert "Who is the customer?" in page
    assert "Cedar Valley Dental" not in page
    assert "--token" not in page
    # Reloading the done page posts "create" again: it starts a fresh wizard, creates nothing.
    response = _post(client, "create")
    assert response.status_code == 302
    assert Customer.objects.count() == 1
    assert Collector.all_customers.count() == 2


def test_going_back_keeps_what_was_entered(client, engineer):
    _post(client, name="Northwind", compliance=["pci", "soc2"], notes="Night window")
    _post(client, data_mode="on_site", live_view="no")
    _post(client, "back", **{"site-0-name": "HQ", "site-0-seeds": "10.1.0.0/16"})
    page = _page(client)
    assert 'value="on_site" id="mode-on-site" checked' in page
    assert 'value="no" checked' in page
    _post(client, "back", data_mode="on_site", live_view="no")
    page = _page(client)
    assert 'value="Northwind"' in page
    assert "Night window" in page
    assert 'value="pci" checked' in page
    # Jumping forward from the step list keeps the sites entered before going back.
    _post(client, "goto:3", name="Northwind", compliance=["pci"])
    page = _page(client)
    assert 'value="HQ"' in page
    assert "10.1.0.0/16" in page


def test_steps_ahead_open_only_once_the_steps_before_are_complete(client, engineer):
    response = _post(client, "goto:5", name="")
    assert response.status_code == 400
    assert "Enter the customer&#x27;s name." in response.content.decode()
    _post(client, name="Acme")
    # Step 2 isn't chosen yet, so step 4 stays closed.
    _post(client, "goto:4", data_mode="")
    assert "Where does their network data live?" in _page(client)


def test_on_site_asks_for_live_view_with_no_default(client, engineer):
    _post(client, name="Bluepeak")
    page = _page(client)
    assert 'name="live_view" value="yes" checked' not in page
    assert 'name="live_view" value="no" checked' not in page
    response = _post(client, data_mode="on_site")
    assert response.status_code == 400
    assert "Decide on live view. There is no default." in response.content.decode()
    assert _post(client, data_mode="on_site", live_view="yes").status_code == 302


@pytest.mark.parametrize(("live", "allowed"), [("yes", True), ("no", False)])
def test_on_site_live_view_is_stored_as_chosen(client, engineer, live, allowed):
    _to_review(client, mode="on_site", live=live, two_sites=False)
    assert "Your network data is stored only in your network" in _page(client)
    _post(client, "create")
    customer = Customer.objects.get()
    assert customer.data_mode == "on_site"
    assert customer.live_view_allowed is allowed
    created = AuditEvent.objects.for_customer(customer).get(object_type="customer")
    assert created.detail["live_view_allowed"] is allowed


def test_hosted_customers_never_get_live_view(client, engineer):
    _to_review(client, mode="hosted", live="yes", two_sites=False)
    _post(client, "create")
    assert Customer.objects.get().live_view_allowed is False


@pytest.mark.parametrize(
    ("seeds", "message"),
    [
        ("10.0.0.0/8", "too wide; use /16 or narrower"),
        ("2001:db8::/32", "too wide; use /48 or narrower"),
        ("127.0.0.1", "is not an address on a customer network"),
        ("bad_host!", "is not an IP address, a CIDR network or a host name"),
    ],
)
def test_seed_hints_are_validated_as_the_api_validates_them(client, engineer, seeds, message):
    _post(client, name="Acme")
    _post(client, data_mode="hosted")
    response = _post(client, **{"site-0-name": "HQ", "site-0-seeds": f"192.0.2.1 {seeds}"})
    assert response.status_code == 400
    assert message in response.content.decode()
    assert "Sites and where discovery starts" in _page(client)


def test_every_site_needs_a_unique_name(client, engineer):
    _post(client, name="Acme")
    _post(client, data_mode="hosted")
    _post(client, "add_site", **{"site-0-name": "HQ"})
    response = _post(client, **{"site-0-name": "HQ", "site-1-name": ""})
    assert "Every site needs a name." in response.content.decode()
    response = _post(client, **{"site-0-name": "HQ", "site-1-name": "HQ"})
    assert "Two sites can&#x27;t share a name." in response.content.decode()
    _post(client, "remove_site:1", **{"site-0-name": "HQ", "site-1-name": "HQ"})
    assert _post(client, **{"site-0-name": "HQ"}).status_code == 302


def test_customer_names_are_unique_ignoring_case_archived_included(client, engineer):
    Customer.objects.create(name="Acme", archived_at=timezone.now())
    response = _post(client, name="ACME")
    assert response.status_code == 400
    assert "Archived customers count too." in response.content.decode()


def test_a_name_taken_meanwhile_sends_the_engineer_back_to_step_one(client, engineer):
    _to_review(client, name="Larkfield", two_sites=False)
    Customer.objects.create(name="larkfield")
    response = _post(client, "create")
    assert response.status_code == 400
    assert "Who is the customer?" in response.content.decode()
    assert Customer.objects.count() == 1


def test_engagement_dates_and_skipping_it(client, engineer):
    _post(client, name="Acme")
    _post(client, data_mode="hosted")
    _post(client, **{"site-0-name": "HQ"})
    response = _post(
        client,
        engagement_name="Q4",
        start_date="2026-11-21",
        end_date="2026-11-03",
    )
    assert "Must be on or after the start date." in response.content.decode()
    _post(client, skip_engagement="on", engagement_name="")
    # Unticking the site's collector: the customer is created without any.
    _post(client, **{"collector-0-name": "x"})
    assert "None yet" in _page(client)
    _post(client, "create")
    customer = Customer.objects.get()
    assert not Engagement.objects.for_customer(customer).exists()
    assert not Collector.objects.for_customer(customer).exists()


def test_records_land_on_the_new_customer_only(client, engineer):
    other = Customer.objects.create(name="Other")
    Site.objects.for_customer(other).create(name="Main office")
    Collector.objects.for_customer(other).create(name="cvd-main-vm")
    _to_review(client)
    _post(client, "create")
    customer = Customer.objects.get(name="Cedar Valley Dental")
    # The other customer's site and collector of the same names don't block or move.
    assert Site.objects.for_customer(other).count() == 1
    assert Collector.objects.for_customer(other).count() == 1
    assert Site.objects.for_customer(customer).count() == 2
    assert not AuditEvent.objects.for_customer(other).exists()


def test_start_a_new_customer_clears_the_wizard(client, engineer):
    _post(client, name="Acme")
    assert client.post(URL, {"action": "restart"}).status_code == 302
    page = _page(client)
    assert "Who is the customer?" in page
    assert 'value="Acme"' not in page


def test_compliance_tags_show_on_the_customer_list(client, engineer):
    Customer.objects.create(name="Acme", compliance=["hipaa", "pci"])
    page = client.get("/").content.decode()
    assert "HIPAA" in page
    assert "PCI" in page
