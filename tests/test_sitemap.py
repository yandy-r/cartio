"""The Site map: per-site L2/L3 maps from collected and imported data, and their exports."""

import csv
import io
import re
from xml.etree import ElementTree

import pytest
from cartio_controller.collectors.inventory import store_outputs
from cartio_controller.collectors.models import (
    AuditEvent,
    CollectionRun,
    Collector,
    Customer,
    Device,
    Engagement,
    Finding,
    Interface,
    Link,
    Site,
    Source,
)
from cartio_controller.engineers.sitemap import NO_SITE, SiteMap, role_of

from tests.test_engineer_ui import _engineer, _signed_in
from tests.test_parsers import LAB, outputs

pytestmark = pytest.mark.django_db


@pytest.fixture
def engineer(client):
    user = _engineer()
    _signed_in(client, user)
    return user


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _lab(customer, *, collected=("core-sw1", "edge-fw1"), imported=("floor-sw1",)):
    """The lab: some devices from a discovery run, some from imported files, on two sites."""
    engagement = Engagement.objects.for_customer(customer).create(name="2026 audit")
    collector = Collector.objects.for_customer(customer).create(name="vm01")
    run = CollectionRun.objects.for_customer(customer).create(
        job_type="discovery", collector=collector, engagement=engagement
    )
    live = Source.objects.for_customer(customer).create(
        engagement=engagement, kind=Source.Kind.LIVE_RUN, run=run
    )
    upload = Source.objects.for_customer(customer).create(
        engagement=engagement, kind=Source.Kind.UPLOAD, name="floor outputs"
    )
    for name in collected:
        store_outputs(live, LAB[name].value, outputs(name))
    for name in imported:
        store_outputs(upload, LAB[name].value, outputs(name))
    hq = Site.objects.for_customer(customer).create(name="HQ")
    floor = Site.objects.for_customer(customer).create(name="Floor 2")
    for device in Device.objects.for_customer(customer):
        _move(device, floor if device.name == "floor-sw1" else hq)
    return engagement, hq, floor


def _move(device, site):
    device.site = site
    device.save()


def _map(customer, engagement, **params):
    return SiteMap(customer, engagement, params)


def _edges(view, layer):
    names = {k: n.name for k, n in view.nodes.items()}
    return sorted(
        (
            *sorted([names[e.a], names[e.b]]),
            e.both_ends,
            e.protocol_label if layer == "l2" else e.label,
        )
        for e in view.edges[layer]
    )


def _url(customer, engagement, tail=""):
    return f"/customers/{customer.pk}/engagements/{engagement.pk}/site-map{tail}"


@pytest.mark.parametrize(
    ("name", "model", "platform", "role"),
    [
        ("edge-fw1", "FortiGate-60F", "fortios", "fw"),
        ("core-sw1", "C9300-48P", "ios-xe", "sw"),
        ("floor-sw1", "WS-C2960X-48FPD-L", "ios", "sw"),
        ("branch-rtr1", "", "", "rtr"),
        ("wan-edge", "ISR4331/K9", "ios-xe", "rtr"),
        ("lobby-ap3", "", "", "ap"),
        ("mystery", "", "", "unk"),
    ],
)
def test_roles_come_from_platform_model_and_name(name, model, platform, role):
    assert role_of(name, model, platform) == role


def test_the_lab_draws_both_sites_from_collected_and_imported_data(acme):
    engagement, hq, floor = _lab(acme)

    view = _map(acme, engagement, site=str(hq.pk))
    kinds = {n.name: n.kind for n in view.shown.values()}
    assert kinds == {
        "core-sw1": "site",
        "edge-fw1": "site",
        # On the other site, drawn so the uplink doesn't end nowhere.
        "floor-sw1": "offsite",
    }
    assert _edges(view, "l2") == [
        # The firewall's outputs have no LLDP, so only the switch saw this one.
        ("core-sw1", "edge-fw1", False, "LLDP"),
        # CDP and LLDP from both switches: one link, seen from both ends.
        ("core-sw1", "floor-sw1", True, "CDP, LLDP"),
    ]

    view = _map(acme, engagement, site=str(hq.pk), layer="l3")
    assert _edges(view, "l3") == [
        ("203.0.113.1", "edge-fw1", False, "BGP 203.0.113.0/24"),
        ("core-sw1", "edge-fw1", True, "OSPF 192.0.2.0/24"),
    ]
    isp = view.nodes["ip:203.0.113.1"]
    assert (isp.kind, isp.role, isp.sub) == ("unconfirmed", "ext", "AS 64500")

    view = _map(acme, engagement, site=str(floor.pk))
    assert {n.name: n.kind for n in view.shown.values()} == {
        "floor-sw1": "site",
        "core-sw1": "offsite",
    }
    assert _edges(view, "l2") == [("core-sw1", "floor-sw1", True, "CDP, LLDP")]
    # The floor switch has no routing neighbours, so its L3 map has no links.
    assert view.edges["l3"] == []


def test_the_layout_keeps_its_place_across_layers_and_puts_the_top_of_the_network_first(acme):
    engagement, hq, _ = _lab(acme)
    l2 = _map(acme, engagement, site=str(hq.pk))
    l3 = _map(acme, engagement, site=str(hq.pk), layer="l3")
    assert {k: (n.x, n.y) for k, n in l2.nodes.items()} == {
        k: (n.x, n.y) for k, n in l3.nodes.items()
    }
    y = {n.name: n.y for n in l2.nodes.values()}
    # The ISP above the firewall, the firewall above the core, the core above the floor switch.
    assert y["203.0.113.1"] < y["edge-fw1"] < y["core-sw1"] < y["floor-sw1"]


def test_a_neighbour_never_polled_is_drawn_dashed(acme):
    engagement, hq, _ = _lab(acme, collected=("core-sw1",), imported=())
    view = _map(acme, engagement, site=str(hq.pk))
    unconfirmed = {n.name for n in view.shown.values() if n.unconfirmed}
    assert unconfirmed == {"floor-sw1", "edge-fw1"}
    assert all(not e.both_ends for e in view.edges["l2"])
    drawn = view.svg()
    assert 'stroke-dasharray="5 4"' in drawn
    assert 'stroke-dasharray="7 5"' in drawn


def test_devices_without_a_site_get_a_map_of_their_own(acme):
    engagement, _, _ = _lab(acme)
    _move(Device.objects.for_customer(acme).get(name="floor-sw1"), None)
    view = _map(acme, engagement, site=NO_SITE)
    assert view.site_name == "No site yet"
    assert [n.name for n in view.site_devices] == ["floor-sw1"]


def test_findings_mark_devices_and_filters_dim_the_rest(acme):
    engagement, hq, _ = _lab(acme)
    core = Device.objects.for_customer(acme).get(name="core-sw1")
    Finding.objects.for_customer(acme).create(
        engagement=engagement,
        kind=Finding.Kind.MANUAL,
        title="Telnet open",
        severity=Finding.Severity.CRITICAL,
        device_ids=[str(core.pk)],
    )
    view = _map(acme, engagement, site=str(hq.pk), findings="1")
    marked = {n.name: (n.worst, n.dimmed) for n in view.nodes.values()}
    assert marked["core-sw1"] == ("critical", False)
    assert marked["edge-fw1"] == ("", True)
    summary = view.summary()
    assert [n.name for n, _ in summary["attention"]] == ["core-sw1"]
    assert summary["findings"] == 1


def test_the_screen_shows_the_map_hover_cards_and_a_site_summary(client, engineer, acme):
    engagement, hq, _ = _lab(acme)
    # Without a site in the URL, the first site with devices shows.
    page = client.get(_url(acme, engagement)).content.decode()
    assert '<a class="pill" href="?site=' in page
    page = client.get(_url(acme, engagement, f"?site={hq.pk}")).content.decode()
    assert 'class="sitemap-svg"' in page
    assert 'aria-label="core-sw1, Switch, 0 findings"' in page
    assert "Seen from one end only" in page
    assert 'aria-label="Site summary"' in page
    assert "2 devices" in page
    # One hover card per node, including the device on the other site.
    assert page.count('class="hover-card"') == 3


def test_pinning_a_device_shows_its_details_links_and_connect(client, engineer, acme):
    engagement, hq, _ = _lab(acme)
    core = Device.objects.for_customer(acme).get(name="core-sw1")
    page = client.get(_url(acme, engagement, f"?site={hq.pk}&device={core.pk}")).content.decode()
    assert 'aria-label="core-sw1"' in page
    assert "TenGigabitEthernet1/1/1 → floor-sw1 GigabitEthernet1/0/49" in page
    assert "one end only" in page
    assert "Connect to…" in page
    assert "Clear selection and show the site summary" in page

    page = client.get(
        _url(acme, engagement, f"?site={hq.pk}&layer=l3&device={core.pk}")
    ).content.decode()
    assert "OSPF 192.0.2.0/24" in page


def test_another_sites_device_opens_its_own_map(acme):
    engagement, hq, floor = _lab(acme)
    view = _map(acme, engagement, site=str(hq.pk))
    floor_sw = next(n for n in view.nodes.values() if n.name == "floor-sw1")
    assert view.node_link(floor_sw) == f"?site={floor.pk}&device={floor_sw.id}"


def test_all_customers_rows_open_the_site_map(client, engineer, acme):
    engagement, _, _ = _lab(acme)
    acme.refresh_from_db()
    page = client.get("/").content.decode()
    assert f'href="{_url(acme, engagement)}"' in page


@pytest.mark.parametrize("fmt", ["svg", "png", "csv"])
def test_exports_are_logged_and_name_the_customer_engagement_and_site(client, engineer, acme, fmt):
    engagement, hq, _ = _lab(acme)
    response = client.post(
        _url(acme, engagement, "/export"), {"format": fmt, "site": str(hq.pk), "layer": "l3"}
    )
    assert response.status_code == 200
    name = response["Content-Disposition"]
    assert name.startswith('attachment; filename="cartio-site-map-Acme-2026-audit-HQ-')
    assert name.endswith(f'.{fmt}"')
    event = AuditEvent.objects.for_customer(acme).get(action=AuditEvent.Action.SITEMAP_EXPORTED)
    assert event.actor == "engineer"
    assert event.detail["format"] == fmt
    assert event.detail["site"] == "HQ"
    body = response.content.decode()
    if fmt == "csv":
        rows = list(csv.reader(io.StringIO(body)))
        assert rows[0][:2] == ["Hostname", "Role"]
        assert sorted(r[0] for r in rows[1:]) == ["core-sw1", "edge-fw1"]
    else:
        # PNG is drawn in the browser from this SVG.
        assert response["Content-Type"] == "image/svg+xml"
        assert body.startswith("<?xml")
        assert "Acme · 2026 audit · HQ · L3" in body
        assert "OSPF 192.0.2.0/24" in body


def test_exports_refuse_unknown_formats_and_empty_sites(client, engineer, acme):
    engagement, _, _ = _lab(acme)
    empty = Site.objects.for_customer(acme).create(name="Warehouse")
    url = _url(acme, engagement, "/export")
    assert client.post(url, {"format": "pdf"}).status_code == 400
    assert client.post(url, {"format": "svg", "site": str(empty.pk)}).status_code == 400
    assert not AuditEvent.objects.for_customer(acme).filter(
        action=AuditEvent.Action.SITEMAP_EXPORTED
    )


def test_one_customer_per_map(client, engineer, acme):
    engagement, hq, _ = _lab(acme)
    other = Customer.objects.create(name="Other")
    theirs, their_site, _ = _lab(other)
    their_core = Device.objects.for_customer(other).get(name="core-sw1")

    # Another customer's engagement under this customer's URL doesn't exist.
    assert client.get(f"/customers/{acme.pk}/engagements/{theirs.pk}/site-map").status_code == 404
    response = client.post(
        f"/customers/{acme.pk}/engagements/{theirs.pk}/site-map/export", {"format": "svg"}
    )
    assert response.status_code == 404
    # Their site and device ids find nothing here.
    view = _map(acme, engagement, site=str(their_site.pk), device=str(their_core.pk))
    assert view.site in {str(s.pk) for s in Site.objects.for_customer(acme)}
    assert view.pinned is None
    ours = set(Device.objects.for_customer(acme).values_list("pk", flat=True))
    assert {n.device.pk for n in view.nodes.values() if n.device} <= ours


def test_inside_access_only_shows_the_notice_and_exports_nothing(client, engineer):
    locked = Customer.objects.create(name="Ostrander", data_mode=Customer.DataMode.ON_SITE)
    engagement = Engagement.objects.for_customer(locked).create(name="2026 audit")
    page = client.get(_url(locked, engagement)).content.decode()
    assert "inside access only" in page
    assert "sitemap-svg" not in page
    response = client.post(_url(locked, engagement, "/export"), {"format": "svg"})
    assert response.status_code == 404


def test_announced_names_cant_break_the_svg_or_draw_over_each_other(acme):
    engagement, hq, _ = _lab(acme)
    core = Device.objects.for_customer(acme).get(name="core-sw1")
    for port in ("GigabitEthernet1/0/1", "GigabitEthernet1/0/2"):
        interface = Interface.objects.for_customer(acme).create(device=core, name=port)
        Link.objects.for_customer(acme).create(
            a_interface=interface, b_device_name="odd\x01<peer>", b_interface_name=port
        )
    view = _map(acme, engagement, site=str(hq.pk))
    drawn = view.svg(title="t")
    ElementTree.fromstring(drawn)  # noqa: S314 (our own output)
    assert "odd&lt;peer&gt;" in drawn
    # Two links between the same two devices are drawn side by side, not on top of each other.
    peer = view.nodes["n:odd\x01<peer>"]
    assert len([e for e in view.edges["l2"] if peer.id in (e.a, e.b)]) == 2
    drawn_lines = re.findall(
        r'<line (x1="[^"]+" y1="[^"]+" x2="[^"]+" y2="[^"]+") stroke="#', drawn
    )
    assert len(drawn_lines) == len(set(drawn_lines))
