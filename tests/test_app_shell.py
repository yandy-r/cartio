"""The engineer UI's app shell and customer and engagement switcher (YAN-1741)."""

import re
from pathlib import Path

import pytest
from cartio_controller.collectors.models import Collector, Customer, Engagement
from cartio_controller.engineers import shell, views
from django.contrib.auth.models import Permission
from django.test import RequestFactory

from tests.test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db

TEMPLATES = Path(views.__file__).parent / "templates"
STATIC = Path(views.__file__).parent / "static" / "engineers"


@pytest.fixture
def engineer(client):
    user = _engineer()
    user.user_permissions.add(Permission.objects.get(codename="view_collectionrun"))
    _signed_in(client, user)
    return user


def _engagements(customer, *rows):
    return [
        Engagement.objects.for_customer(customer).create(name=name, status=status)
        for name, status in rows
    ]


def test_sidebar_has_the_design_sections(client, engineer):
    page = client.get("/").content.decode()
    for text in (
        "Search…",
        "Workspace",
        "Site map",
        "Inventory",
        "Findings",
        "Compare",
        "Report",
        "Operations",
        "Collectors &amp; jobs",
        "Audit log",
        "Cartio never changes customer devices.",
        "Engineer · MFA on",
        engineer.get_username(),
    ):
        assert text in page
    assert 'aria-current="page"' in page


def test_pages_make_no_third_party_requests(client, engineer):
    acme = Customer.objects.create(name="Acme")
    for url in ("/", f"/customers/{acme.pk}", f"/customers/{acme.pk}/jobs"):
        page = client.get(url).content.decode()
        assert not re.search(r"""(?:src|href)=["'](?:https?:)?//""", page), url
        assert "/static/engineers/js/htmx-2.0.4.min.js" in page
        assert "/static/engineers/css/cartio.css" in page


def test_fonts_and_htmx_are_served_from_static_files():
    css = (STATIC / "css" / "cartio.css").read_text()
    assert "http" not in css
    for font in re.findall(r'url\("\.\./fonts/([^"]+)"\)', css):
        assert (STATIC / "fonts" / font).is_file()
    assert "IBM Plex Sans" in css and "IBM Plex Mono" in css
    assert (STATIC / "js" / "htmx-2.0.4.min.js").is_file()


def test_design_tokens_are_css_custom_properties():
    css = (STATIC / "css" / "cartio.css").read_text()
    for token in ("#0E1A24", "#F3F5F7", "#0E7490", "#B42318", "#15803D"):
        assert re.search(rf"--[a-z-]+: {token};", css), token
    # Controls are at least 44px tall and focus is always visible.
    assert ":focus-visible" in css and "min-height: 44px" in css


def test_customer_url_carries_the_engagement(client, engineer):
    acme = Customer.objects.create(name="Acme")
    done, active = _engagements(acme, ("Q2 review", "completed"), ("Q4 review", "active"))

    # Without an engagement in the URL, the current (active) one shows.
    page = client.get(f"/customers/{acme.pk}").content.decode()
    assert "Q4 review · Active" in page
    assert f"/customers/{acme.pk}/engagements/{active.pk}/collectors" in page

    page = client.get(f"/customers/{acme.pk}/engagements/{done.pk}").content.decode()
    assert "Q2 review · Completed" in page
    assert "Q2 review is completed and read-only." in page
    page = client.get(f"/customers/{acme.pk}/engagements/{done.pk}/jobs").content.decode()
    assert "Acme · Q2 review" in page


def test_switcher_keeps_the_engineer_on_the_same_screen(client, engineer):
    acme = Customer.objects.create(name="Acme")
    globex = Customer.objects.create(name="Globex", data_mode=Customer.DataMode.ON_SITE)
    first, second = _engagements(acme, ("Q4 review", "active"), ("Baseline", "planned"))

    page = client.get(f"/customers/{acme.pk}/engagements/{first.pk}/jobs").content.decode()
    # The current customer's engagements, then every customer with its data mode.
    assert f'href="/customers/{acme.pk}/engagements/{second.pk}/jobs"' in page
    assert f'href="/customers/{globex.pk}/jobs"' in page
    assert "Customer · Hosted" in page
    assert "Inside access only" in page

    page = client.get(f"/customers/{acme.pk}/engagements/{first.pk}").content.decode()
    assert f'href="/customers/{globex.pk}"' in page
    assert f'href="/customers/{acme.pk}/engagements/{second.pk}"' in page


def test_switcher_search_is_an_htmx_partial(client, engineer):
    Customer.objects.create(name="Acme")
    globex = Customer.objects.create(name="Globex")
    Customer.objects.create(name="Gone", archived_at="2026-01-01T00:00Z")

    page = client.get("/switcher", {"screen": "jobs", "q": "glo"}).content.decode()
    assert "<html" not in page
    assert f'href="/customers/{globex.pk}/jobs"' in page
    assert "Acme" not in page

    page = client.get("/switcher", {"screen": "jobs", "q": "go"}).content.decode()
    assert "Gone" not in page
    assert (
        "No customer matches “nothing”."
        in client.get("/switcher", {"screen": "customer", "q": "nothing"}).content.decode()
    )
    assert client.get("/switcher", {"screen": "admin:index"}).status_code == 400


def test_switcher_needs_sign_in(client):
    response = client.get("/switcher", {"screen": "jobs"})
    assert response.status_code == 302


def test_another_customers_engagement_is_not_found(client, engineer):
    acme = Customer.objects.create(name="Acme")
    globex = Customer.objects.create(name="Globex")
    (theirs,) = _engagements(globex, ("Globex secret project", "active"))
    (archived,) = _engagements(acme, ("Old", "active"))
    Engagement.objects.for_customer(acme).filter(pk=archived.pk).update(
        archived_at="2026-01-01T00:00Z"
    )

    for url in (
        f"/customers/{acme.pk}/engagements/{theirs.pk}",
        f"/customers/{acme.pk}/engagements/{theirs.pk}/jobs",
        f"/customers/{acme.pk}/engagements/{archived.pk}",
    ):
        assert client.get(url).status_code == 404, url
    # Acme's pages never list Globex's engagements.
    assert "Globex secret project" not in client.get(f"/customers/{acme.pk}").content.decode()


def test_on_site_customers_say_how_the_engineer_sees_them(client, engineer):
    live = Customer.objects.create(
        name="Bluepeak", data_mode=Customer.DataMode.ON_SITE, live_view_allowed=True
    )
    inside = Customer.objects.create(name="Ostrander", data_mode=Customer.DataMode.ON_SITE)
    hosted = Customer.objects.create(name="Northwind")

    page = client.get(f"/customers/{live.pk}").content.decode()
    assert "Live view · relayed from the on-site collector, not stored on the controller" in page
    page = client.get(f"/customers/{inside.pk}").content.decode()
    assert 'class="banner inside-access"' in page and "banner live-view" not in page
    page = client.get(f"/customers/{hosted.pk}").content.decode()
    assert "banner live-view" not in page and "banner inside-access" not in page


def test_network_data_screens_show_the_inside_access_notice(engineer):
    inside = Customer.objects.create(name="Ostrander", data_mode=Customer.DataMode.ON_SITE)
    Collector.objects.for_customer(inside).create(name="om-plant-vm")
    live = Customer.objects.create(
        name="Bluepeak", data_mode=Customer.DataMode.ON_SITE, live_view_allowed=True
    )
    request = RequestFactory().get("/")
    request.user = engineer

    def render(customer, network_data):
        customer, engagements, engagement = shell.resolve(customer.pk)
        return views.render_screen(
            request,
            "customer",
            "engineers/customer_detail.html",
            {"network_rows": "SHOULD NOT SHOW"},
            customer,
            engagements,
            engagement,
            network_data=network_data,
        ).content.decode()

    page = render(inside, network_data=True)
    assert "Overview is inside access only for Ostrander" in page
    assert "om-plant-vm" in page and "not enrolled" in page
    assert "Collectors online" not in page
    # Live view, or a screen without network data, renders the screen itself.
    assert "inside access only for" not in render(live, network_data=True)
    assert "inside access only for" not in render(inside, network_data=False)


def test_templates_extend_the_shell():
    for template in (TEMPLATES / "engineers").glob("[!_]*.html"):
        if template.name != "base.html":
            assert template.read_text().startswith('{% extends "engineers/base.html" %}')


def test_branding_comes_from_settings(client, engineer, settings):
    page = client.get("/").content.decode()
    assert '<span class="brand-name">Cartio</span>' in page
    assert "brand-org" not in page

    settings.BRAND_NAME = "Netscope"
    settings.BRAND_ORG = "Example Networks"
    page = client.get("/").content.decode()
    assert '<span class="brand-name">Netscope</span>' in page
    assert '<span class="brand-org">Example Networks</span>' in page
    assert "· Netscope</title>" in page
