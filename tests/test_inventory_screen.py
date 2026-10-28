"""The device inventory screen (YAN-1737, DESIGN.md section 6)."""

import csv
import io
import re
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from cartio_controller.collectors.models import (
    AuditEvent,
    ConfigSnapshot,
    Customer,
    Device,
    Engagement,
    Interface,
    Link,
    Site,
    Source,
)
from cartio_controller.engineers import xlsx

from tests.test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def engineer(client):
    user = _engineer()
    _signed_in(client, user)
    return user


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _engagement(customer, name="Q4 review"):
    return Engagement.objects.for_customer(customer).create(name=name, status="active")


def _devices(engagement):
    """Three devices at two sites, one of them found by a live run."""
    customer = engagement.customer
    hq = Site.objects.for_customer(customer).create(name="HQ")
    east = Site.objects.for_customer(customer).create(name="Clinic East")
    upload = Source.objects.for_customer(customer).create(
        engagement=engagement, kind="upload", name="core-sw-01_show-tech.txt"
    )
    make = Device.objects.for_customer(customer).create
    rows = [
        make(
            engagement=engagement,
            site=hq,
            source=upload,
            name="core-sw-01",
            platform="ios",
            manufacturer="Cisco",
            model="C9300-48P",
            serial="FOC2419X0AB",
            os_version="IOS-XE 17.9.4a",
            management_ip="192.0.2.20",
            last_seen=datetime(2026, 10, 27, 10, 40, tzinfo=UTC),
        ),
        make(
            engagement=engagement,
            site=hq,
            source=upload,
            name="fw-hq-01",
            platform="fortios",
            manufacturer="Fortinet",
            model="FortiGate 200F",
            serial="FG200F0000004821",
            os_version="FortiOS 7.2.8",
            management_ip="192.0.2.10",
        ),
        make(
            engagement=engagement,
            site=east,
            name="east-sw-01",
            manufacturer="Cisco",
            model="C2960X-24PS",
            management_ip="198.51.100.5",
        ),
    ]
    core = rows[0]
    gi1 = Interface.objects.for_customer(customer).create(device=core, name="Gi1/0/1")
    Interface.objects.for_customer(customer).create(device=core, name="Gi1/0/2")
    Link.objects.for_customer(customer).create(
        a_interface=gi1, b_device_name="fw-hq-01", b_interface_name="port1", protocol="lldp"
    )
    ConfigSnapshot.objects.for_customer(customer).create(
        device=core, source=upload, content="hostname core-sw-01\n"
    )
    return {device.name: device for device in rows}


def _url(engagement, suffix="inventory"):
    return f"/customers/{engagement.customer_id}/engagements/{engagement.pk}/{suffix}"


def _hosts(page: str) -> list:
    return re.findall(r'<button type="button" class="host mono"[^>]*>([^<]+)</button>', page)


def test_inventory_lists_the_engagements_devices(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    # Another engagement's devices don't show.
    other = _engagement(acme, "Q2 review")
    Device.objects.for_customer(acme).create(engagement=other, name="old-sw-01")

    page = client.get(_url(engagement)).content.decode()
    for header in ("Hostname", "IP", "Vendor and model", "Serial", "OS", "Site", "Source"):
        assert f">{header}<" in page
    for header in ("Findings", "Last seen"):
        assert header in page
    assert _hosts(page) == ["core-sw-01", "east-sw-01", "fw-hq-01"]
    assert "3 devices" in page
    assert "192.0.2.20" in page and "FOC2419X0AB" in page and "IOS-XE 17.9.4a" in page
    assert 'aria-label="Connect to core-sw-01"' in page
    # The nav links here, and marks it as the current page.
    assert re.search(rf'href="{_url(engagement)}" aria-current="page"', page)


def test_inventory_without_an_engagement_in_the_url_shows_the_current_one(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    page = client.get(f"/customers/{acme.pk}/inventory").content.decode()
    assert "core-sw-01" in page
    assert "Inventory" in page


def test_search_covers_hostname_ip_serial_and_model(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    for query, hosts in (
        ("fw-hq", ["fw-hq-01"]),
        ("198.51.100", ["east-sw-01"]),
        ("foc2419", ["core-sw-01"]),
        ("C2960X", ["east-sw-01"]),
        ("nothing-like-it", []),
    ):
        page = client.get(_url(engagement), {"q": query}).content.decode()
        assert _hosts(page) == hosts, query
    page = client.get(_url(engagement), {"q": "nothing-like-it"}).content.decode()
    assert "No devices match these filters." in page


def test_q_in_the_url_fills_the_search_box(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    page = client.get(_url(engagement), {"q": "core-sw-01"}).content.decode()
    assert 'name="q" value="core-sw-01"' in page
    assert _hosts(page) == ["core-sw-01"]
    assert "1 of 3 devices" in page


def test_vendor_and_os_pills_filter(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    page = client.get(_url(engagement)).content.decode()
    assert 'href="?vendor=Cisco"' in page and 'href="?vendor=Fortinet"' in page
    assert 'href="?os=unknown"' in page

    page = client.get(_url(engagement), {"vendor": "Fortinet"}).content.decode()
    assert _hosts(page) == ["fw-hq-01"]
    assert re.search(r'href="\?vendor=Fortinet"[^>]*aria-pressed="true"', page)
    page = client.get(_url(engagement), {"os": "supported"}).content.decode()
    assert _hosts(page) == []
    # An unknown state is ignored rather than hiding everything.
    page = client.get(_url(engagement), {"os": "nonsense"}).content.decode()
    assert len(_hosts(page)) == 3


def test_summary_breakdowns_apply_filters(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    page = client.get(_url(engagement)).content.decode()
    summary = page[page.index('id="inventory-summary"') :]
    for title in ("Vendor", "OS state", "Site"):
        assert f"<h2>{title}</h2>" in summary
    assert 'href="?site=Clinic+East"' in summary

    page = client.get(_url(engagement), {"site": "Clinic East"}).content.decode()
    assert _hosts(page) == ["east-sw-01"]
    # Picking the applied breakdown again clears it.
    assert re.search(r'href="\?"[^>]*aria-pressed="true"', page)


def test_columns_sort(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    page = client.get(_url(engagement), {"sort": "ip"}).content.decode()
    assert _hosts(page) == ["fw-hq-01", "core-sw-01", "east-sw-01"]
    assert 'aria-sort="ascending"' in page
    assert 'href="?sort=-ip"' in page
    page = client.get(_url(engagement), {"sort": "-ip"}).content.decode()
    assert _hosts(page) == ["east-sw-01", "core-sw-01", "fw-hq-01"]
    # Blank values go last either way.
    for sort in ("serial", "-serial"):
        page = client.get(_url(engagement), {"sort": sort}).content.decode()
        assert _hosts(page)[-1] == "east-sw-01", sort


def test_htmx_requests_get_the_results_without_the_shell(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    page = client.get(_url(engagement), {"vendor": "Cisco"}, **HTMX).content.decode()
    assert "<nav" not in page and "<!doctype" not in page
    assert _hosts(page) == ["core-sw-01", "east-sw-01"]
    for piece in (
        "inventory-pills",
        "inventory-summary",
        "inventory-count",
        "inventory-export-menu",
    ):
        assert re.search(rf'id="{piece}"[^>]*hx-swap-oob="true"', page), piece


def test_hover_card_holds_the_device_record_without_a_request(client, engineer, acme):
    engagement = _engagement(acme)
    devices = _devices(engagement)
    page = client.get(_url(engagement)).content.decode()
    core = devices["core-sw-01"]
    card = page[page.index(f'id="card-{core.pk}"') :]
    card = card[: card.index("</dl>")]
    assert 'role="tooltip"' in page
    assert f'aria-describedby="card-{core.pk}"' in page
    assert "<dt>Interfaces</dt><dd>2</dd>" in card
    assert "<dt>Neighbors</dt><dd>1</dd>" in card
    assert "1 snapshot, latest" in card
    assert "core-sw-01_show-tech.txt" in card


def test_only_visible_rows_count_as_selected(client, engineer, acme):
    engagement = _engagement(acme)
    devices = _devices(engagement)
    picked = [str(devices["core-sw-01"].pk), str(devices["fw-hq-01"].pk)]

    page = client.get(_url(engagement), {"selected": picked}).content.decode()
    assert "2 of 3 selected" in page
    assert "Export 2" in page

    page = client.get(_url(engagement), {"selected": picked, "vendor": "Cisco"}).content.decode()
    assert "1 of 2 selected" in page
    assert "1 selected device is hidden by the filters and won't be exported" in page
    # The hidden one stays selected for when the filter is cleared.
    assert f'type="hidden" form="inventory-export" name="selected" value="{picked[1]}"' in page


def test_selecting_every_visible_row(client, engineer, acme):
    engagement = _engagement(acme)
    _devices(engagement)
    page = client.get(_url(engagement), {"vendor": "Cisco", "select": "visible"}).content.decode()
    assert "2 of 2 selected" in page
    page = client.get(_url(engagement), {"select": "none"}).content.decode()
    assert "0 of 3 selected" in page
    assert "Select the devices to export first." in page


def test_export_csv_has_only_selected_visible_rows_and_is_audit_logged(client, engineer, acme):
    engagement = _engagement(acme)
    devices = _devices(engagement)
    picked = [str(devices["core-sw-01"].pk), str(devices["fw-hq-01"].pk)]
    response = client.post(
        _url(engagement, "inventory/export"),
        {"format": "csv", "selected": picked, "vendor": "Cisco"},
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    disposition = response["Content-Disposition"]
    assert re.search(r'filename="cartio-inventory-Acme-Q4-review-\d{8}T\d{4}Z\.csv"', disposition)
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0][:3] == ["hostname", "ip", "vendor"]
    assert [row[0] for row in rows[1:]] == ["core-sw-01"]

    event = AuditEvent.objects.for_customer(acme).get(action="inventory.exported")
    assert event.actor == engineer.get_username()
    assert event.object_id == str(engagement.pk)
    assert event.detail == {"format": "csv", "devices": 1, "filters": {"vendor": "Cisco"}}


def test_export_xlsx(client, engineer, acme):
    engagement = _engagement(acme)
    devices = _devices(engagement)
    response = client.post(
        _url(engagement, "inventory/export"),
        {"format": "xlsx", "selected": [str(devices["fw-hq-01"].pk)]},
    )
    assert response.status_code == 200
    assert response["Content-Disposition"].endswith('.xlsx"')
    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        sheet = package.read("xl/worksheets/sheet1.xml").decode()
        assert "[Content_Types].xml" in package.namelist()
    assert "fw-hq-01" in sheet and "FG200F0000004821" in sheet
    assert "core-sw-01" not in sheet
    assert AuditEvent.objects.for_customer(acme).filter(action="inventory.exported").count() == 1


def test_export_needs_a_selection_a_format_and_a_post(client, engineer, acme):
    engagement = _engagement(acme)
    devices = _devices(engagement)
    url = _url(engagement, "inventory/export")
    assert client.post(url, {"format": "csv"}).status_code == 400
    # Selected, but hidden by the filters.
    hidden = {"format": "csv", "selected": [str(devices["fw-hq-01"].pk)], "vendor": "Cisco"}
    assert client.post(url, hidden).status_code == 400
    picked = [str(devices["fw-hq-01"].pk)]
    assert client.post(url, {"format": "pdf", "selected": picked}).status_code == 400
    assert client.get(url, {"format": "csv", "selected": picked}).status_code == 405
    assert not AuditEvent.objects.for_customer(acme).filter(action="inventory.exported").exists()


def test_inventory_never_crosses_customers(client, engineer, acme):
    globex = Customer.objects.create(name="Globex")
    mine = _engagement(acme)
    theirs = _engagement(globex)
    _devices(mine)
    their_devices = _devices(theirs)

    # Another customer's engagement under this customer's URL.
    assert client.get(f"/customers/{acme.pk}/engagements/{theirs.pk}/inventory").status_code == 404
    url = f"/customers/{acme.pk}/engagements/{theirs.pk}/inventory/export"
    picked = [str(device.pk) for device in their_devices.values()]
    assert client.post(url, {"format": "csv", "selected": picked}).status_code == 404

    # Another customer's devices selected under this customer's engagement count for nothing.
    page = client.get(_url(mine), {"selected": picked}).content.decode()
    assert "0 of 3 selected" in page
    response = client.post(_url(mine, "inventory/export"), {"format": "csv", "selected": picked})
    assert response.status_code == 400
    assert not AuditEvent.objects.for_customer(globex).filter(action="inventory.exported").exists()


def test_inside_access_only_shows_the_notice_and_exports_nothing(client, engineer):
    ostrander = Customer.objects.create(name="Ostrander", data_mode=Customer.DataMode.ON_SITE)
    engagement = _engagement(ostrander)
    page = client.get(_url(engagement)).content.decode()
    assert "Inventory is inside access only for Ostrander" in page
    assert 'id="inventory-results"' not in page
    response = client.get(_url(engagement), **HTMX).content.decode()
    assert "Inventory is inside access only" in response
    assert client.post(_url(engagement, "inventory/export"), {"format": "csv"}).status_code == 404


def test_completed_engagement_has_no_import(client, engineer, acme):
    engagement = Engagement.objects.for_customer(acme).create(name="Q2", status="completed")
    page = client.get(_url(engagement)).content.decode()
    assert "Import files" not in page
    assert "Q2 is completed and read-only." in page
    page = client.get(_url(_engagement(acme))).content.decode()
    assert "Import files" in page


def test_inventory_needs_a_signed_in_engineer(client, acme):
    engagement = _engagement(acme)
    assert client.get(_url(engagement)).status_code == 302
    assert client.post(_url(engagement, "inventory/export"), {"format": "csv"}).status_code in (
        302,
        403,
    )


def test_csv_export_defuses_formulas(client, engineer, acme):
    engagement = _engagement(acme)
    device = Device.objects.for_customer(acme).create(
        engagement=engagement, name="sw-01", model="=HYPERLINK(1)"
    )
    response = client.post(
        _url(engagement, "inventory/export"), {"format": "csv", "selected": [str(device.pk)]}
    )
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[1][3] == "'=HYPERLINK(1)"


def test_xlsx_cells_are_text_never_formulas():
    content = xlsx.workbook("Inventory", [["a", "=cmd()", "<b>&\x01"]])
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        sheet = package.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet
    assert 't="inlineStr"' in sheet and "=cmd()" in sheet
    assert "&lt;b&gt;&amp;" in sheet and "\x01" not in sheet
    assert 'r="C1"' in sheet


def test_htmx_posts_the_selection_so_it_needs_no_long_url(client, engineer, acme):
    engagement = _engagement(acme)
    devices = _devices(engagement)
    # Filters in the URL, the selection (and here many unknown ids) in the body.
    picked = [str(devices["core-sw-01"].pk)] + [str(uuid.uuid4()) for _ in range(800)]
    response = client.post(f"{_url(engagement)}?vendor=Cisco", {"selected": picked}, **HTMX)
    page = response.content.decode()
    assert response.status_code == 200
    assert "1 of 2 selected" in page
    assert "HX-Push-Url" not in response

    # The search box posts its filters in the body; they win, and the search goes in history.
    response = client.post(
        f"{_url(engagement)}?vendor=Fortinet",
        {"q": "east", "vendor": "Cisco"},
        HTTP_HX_TRIGGER="inventory-filters",
        **HTMX,
    )
    assert _hosts(response.content.decode()) == ["east-sw-01"]
    assert response["HX-Push-Url"] == f"{_url(engagement)}?q=east&vendor=Cisco"

    # A plain post isn't how the screen is used.
    assert client.post(_url(engagement), {"selected": picked}).status_code == 400


def test_export_file_name_is_ascii(client, engineer):
    customer = Customer.objects.create(name="Müller Klinik 東京")
    engagement = _engagement(customer, "Prüfung")
    device = Device.objects.for_customer(customer).create(engagement=engagement, name="sw-01")
    response = client.post(
        _url(engagement, "inventory/export"), {"format": "csv", "selected": [str(device.pk)]}
    )
    name = re.search(r'filename="([^"]+)"', response["Content-Disposition"]).group(1)
    assert name.isascii()
    assert name.startswith("cartio-inventory-M-ller-Klinik-Pr-fung-")
