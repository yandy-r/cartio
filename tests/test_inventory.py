"""Raw outputs are stored and parsed into Devices, Interfaces and Links (YAN-1743, ADR-0008)."""

import pytest
from cartio_controller.collectors.inventory import UnmaskedOutputError, reparse, store_outputs
from cartio_controller.collectors.models import (
    AuditEvent,
    Customer,
    Device,
    Engagement,
    Interface,
    Link,
    OnSiteDataError,
    RawOutput,
    RoutingNeighbor,
    Source,
)
from cartio_core.parsers import ParseError
from django.db import models

from tests.test_parsers import LAB, outputs

pytestmark = pytest.mark.django_db

SP = " "


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _source(customer, engagement="Audit"):
    engagement = Engagement.objects.for_customer(customer).get_or_create(name=engagement)[0]
    return Source.objects.for_customer(customer).create(
        engagement=engagement, kind="upload", name="lab outputs"
    )


def _store(source, *names):
    for name in names:
        store_outputs(source, LAB[name].value, outputs(name))


def _inventory(customer):
    """Every record as plain values, without primary keys, so two runs can be compared."""
    devices = sorted(
        Device.objects.for_customer(customer).values_list(
            "name", "platform", "manufacturer", "model", "serial", "os_version", "source__name"
        )
    )
    interfaces = sorted(
        Interface.objects.for_customer(customer).values_list(
            "device__name",
            "name",
            "description",
            "enabled",
            "mac_address",
            "mtu",
            "speed",
            "ip_address",
            "prefix_length",
        )
    )
    links = sorted(
        (
            link.a_interface.device.name,
            link.a_interface.name,
            link.b_interface.device.name if link.b_interface else "",
            link.b_interface.name if link.b_interface else "",
            link.b_device_name,
            link.b_interface_name,
            link.protocol,
        )
        for link in Link.objects.for_customer(customer).select_related(
            "a_interface__device", "b_interface__device"
        )
    )
    return devices, interfaces, links, _routing(customer)


def _routing(customer):
    return sorted(
        (
            neighbor.device.name,
            neighbor.interface.name if neighbor.interface else "",
            neighbor.protocol,
            neighbor.address,
            neighbor.router_id,
            neighbor.remote_as,
            neighbor.state,
            neighbor.peer.name if neighbor.peer else "",
            neighbor.subnet,
        )
        for neighbor in RoutingNeighbor.objects.for_customer(customer).select_related(
            "device", "interface", "peer"
        )
    )


EXPECTED_LINKS = sorted(
    [
        (
            "floor-sw1",
            "GigabitEthernet1/0/49",
            "core-sw1",
            "TenGigabitEthernet1/1/1",
            "",
            "",
            "cdp",
        ),
        (
            "floor-sw1",
            "GigabitEthernet1/0/49",
            "core-sw1",
            "TenGigabitEthernet1/1/1",
            "",
            "",
            "lldp",
        ),
        ("core-sw1", "GigabitEthernet1/0/48", "edge-fw1", "port1", "", "", "lldp"),
        (
            "core-sw1",
            "TenGigabitEthernet1/1/1",
            "floor-sw1",
            "GigabitEthernet1/0/49",
            "",
            "",
            "cdp",
        ),
        (
            "core-sw1",
            "TenGigabitEthernet1/1/1",
            "floor-sw1",
            "GigabitEthernet1/0/49",
            "",
            "",
            "lldp",
        ),
    ]
)


EXPECTED_ROUTING = sorted(
    [
        (
            "core-sw1",
            "Vlan10",
            "ospf",
            "192.0.2.1",
            "192.0.2.1",
            None,
            "FULL/DR",
            "edge-fw1",
            "192.0.2.0/24",
        ),
        (
            "edge-fw1",
            "port1",
            "ospf",
            "192.0.2.10",
            "192.0.2.10",
            None,
            "Full/Backup",
            "core-sw1",
            "192.0.2.0/24",
        ),
        # The ISP's router isn't in the inventory: it keeps its address only.
        ("edge-fw1", "wan1", "bgp", "203.0.113.1", "", 64500, "Established", "", "203.0.113.0/24"),
    ]
)


def test_the_lab_becomes_devices_interfaces_and_links(acme):
    source = _source(acme)
    _store(source, "floor-sw1", "core-sw1", "edge-fw1")
    devices, interfaces, links, routing = _inventory(acme)
    assert devices == sorted(
        [
            (
                "floor-sw1",
                "ios",
                "Cisco",
                "WS-C2960X-48FPD-L",
                "FOC0000X0AA",
                "15.2(7)E8",
                "lab outputs",
            ),
            ("core-sw1", "ios-xe", "Cisco", "C9300-48P", "FOC0000Y0BB", "17.9.4a", "lab outputs"),
            (
                "edge-fw1",
                "fortios",
                "Fortinet",
                "FortiGate-60F",
                "FGT60F0000000001",
                "7.2.8",
                "lab outputs",
            ),
        ]
    )

    assert len(interfaces) == 4 + 4 + 5
    assert links == EXPECTED_LINKS
    assert routing == EXPECTED_ROUTING
    assert ("core-sw1", "Vlan10", "192.0.2.10", 24) in [
        (i[0], i[1], i[7], i[8]) for i in interfaces
    ]
    assert RawOutput.objects.for_customer(acme).count() == 7 + 7 + 5
    assert set(
        RawOutput.objects.for_customer(acme)
        .filter(device__name="edge-fw1")
        .values_list("command", flat=True)
    ) == set(outputs("edge-fw1"))


@pytest.mark.parametrize(
    "order",
    [
        ("floor-sw1", "core-sw1", "edge-fw1"),
        ("edge-fw1", "core-sw1", "floor-sw1"),
        ("core-sw1", "edge-fw1", "floor-sw1"),
    ],
)
def test_links_resolve_whatever_order_devices_arrive_in(acme, order):
    _store(_source(acme), *order)
    assert _inventory(acme)[2] == EXPECTED_LINKS
    assert _inventory(acme)[3] == EXPECTED_ROUTING


def test_a_neighbor_not_collected_keeps_its_announced_names(acme):
    _store(_source(acme), "core-sw1")
    links = _inventory(acme)[2]
    assert ("core-sw1", "GigabitEthernet1/0/48", "", "", "edge-fw1", "port1", "lldp") in links


def test_reparsing_a_source_reproduces_the_same_records(acme):
    source = _source(acme)
    _store(source, "floor-sw1", "core-sw1", "edge-fw1")
    before = _inventory(acme)
    outputs_before = RawOutput.objects.for_customer(acme).count()

    devices = reparse(source)

    assert sorted(d.name for d in devices) == ["core-sw1", "edge-fw1", "floor-sw1"]
    assert _inventory(acme) == before
    # Re-parsing reads the stored outputs; it doesn't add any.
    assert RawOutput.objects.for_customer(acme).count() == outputs_before


def test_reparsing_rebuilds_records_changed_since(acme):
    source = _source(acme)
    _store(source, "floor-sw1", "core-sw1")
    before = _inventory(acme)
    Interface.objects.for_customer(acme).filter(device__name="core-sw1").update(description="x")
    Link.objects.for_customer(acme).filter(protocol="cdp").delete()
    RoutingNeighbor.objects.for_customer(acme).filter(protocol="ospf").delete()
    reparse(source)
    assert _inventory(acme) == before


def test_storing_and_reparsing_are_audit_logged(acme):
    source = _source(acme)
    _store(source, "core-sw1")
    reparse(source, actor="dana")
    events = AuditEvent.objects.for_customer(acme)
    stored = events.get(action=AuditEvent.Action.OUTPUTS_STORED)
    assert (stored.object_type, stored.object_name) == ("device", "core-sw1")
    assert stored.detail["commands"] == sorted(outputs("core-sw1"))
    reparsed = events.get(action=AuditEvent.Action.SOURCE_REPARSED)
    assert (reparsed.actor, reparsed.detail["devices"]) == ("dana", 1)


def test_output_with_an_unmasked_secret_is_refused(acme):
    source = _source(acme)
    lab = outputs("floor-sw1")
    lab["show version"] += SP.join(["enable", "secret", "5", "$1$" + "a" * 26])
    with pytest.raises(UnmaskedOutputError):
        store_outputs(source, "ios", lab)
    assert not RawOutput.objects.for_customer(acme).exists()
    assert not Device.objects.for_customer(acme).exists()


def test_output_without_a_host_name_stores_nothing(acme):
    source = _source(acme)
    with pytest.raises(ParseError):
        store_outputs(source, "ios", {"show interfaces": outputs("floor-sw1")["show interfaces"]})
    assert not RawOutput.objects.for_customer(acme).exists()


def test_a_neighbor_is_never_matched_in_another_customer(acme):
    globex = Customer.objects.create(name="Globex")
    _store(_source(globex), "floor-sw1")
    _store(_source(acme), "core-sw1")
    for link in Link.objects.for_customer(acme):
        assert link.b_interface is None
    assert Link.objects.for_customer(globex).filter(b_interface=None).count() == 2


def test_a_neighbor_is_never_matched_in_another_engagement(acme):
    first = _source(acme)
    _store(first, "floor-sw1")
    _store(_source(acme, "Next audit"), "core-sw1")
    assert not Link.objects.for_customer(acme).exclude(b_interface=None).exists()


def test_a_routing_neighbor_is_never_matched_in_another_customer_or_engagement(acme):
    globex = Customer.objects.create(name="Globex")
    _store(_source(globex), "edge-fw1")
    _store(_source(acme, "Next audit"), "edge-fw1")
    _store(_source(acme), "core-sw1")
    # Both edge-fw1s own 192.0.2.1, but neither is in core-sw1's customer and engagement.
    assert not RoutingNeighbor.objects.for_customer(acme).exclude(peer=None).exists()
    assert not RoutingNeighbor.objects.for_customer(globex).exclude(peer=None).exists()


def test_a_routing_peer_follows_its_address(acme):
    source = _source(acme)
    _store(source, "core-sw1", "edge-fw1")
    # edge-fw1 is re-addressed: core-sw1's OSPF neighbor at 192.0.2.1 is no longer edge-fw1.
    lab = outputs("edge-fw1")
    lab["get system interface"] = lab["get system interface"].replace(
        "ip: 192.0.2.1 255.255.255.0", "ip: 192.0.2.2 255.255.255.0"
    )
    store_outputs(source, "fortios", lab)
    ospf = RoutingNeighbor.objects.for_customer(acme).get(device__name="core-sw1")
    assert ospf.peer is None


def test_an_address_on_two_devices_resolves_to_neither(acme):
    source = _source(acme)
    _store(source, "edge-fw1")
    # floor-sw1 claims edge-fw1's 192.0.2.1 too (a shared VIP, or overlapping VRFs).
    lab = outputs("floor-sw1")
    lab["show interfaces"] = lab["show interfaces"].replace(
        "Internet address is 192.0.2.21/24", "Internet address is 192.0.2.1/24"
    )
    store_outputs(source, "ios", lab)
    _store(source, "core-sw1")
    ospf = RoutingNeighbor.objects.for_customer(acme).get(device__name="core-sw1")
    assert ospf.peer is None


def test_a_routing_neighbor_not_collected_keeps_its_address(acme):
    _store(_source(acme), "core-sw1")
    assert _inventory(acme)[3] == [
        (
            "core-sw1",
            "Vlan10",
            "ospf",
            "192.0.2.1",
            "192.0.2.1",
            None,
            "FULL/DR",
            "",
            "192.0.2.0/24",
        )
    ]


def test_on_site_customers_keep_device_data_on_site():
    initech = Customer.objects.create(name="Initech", data_mode=Customer.DataMode.ON_SITE)
    engagement = Engagement.objects.for_customer(initech).create(name="Audit")
    # The models refuse it, so no write path can store it by accident.
    with pytest.raises(OnSiteDataError):
        Source.objects.for_customer(initech).create(engagement=engagement, kind="upload")
    with pytest.raises(OnSiteDataError):
        Device.objects.for_customer(initech).create(engagement=engagement, name="sw1")
    with pytest.raises(OnSiteDataError):
        Device.objects.for_customer(initech).bulk_create(
            [Device(engagement=engagement, name="sw1")]
        )
    assert not Device.objects.for_customer(initech).exists()


def test_on_site_customers_cant_store_or_reparse_outputs(acme):
    source = _source(acme)
    _store(source, "core-sw1")
    # Past the data mode action: these rows predate the switch, which would delete them.
    models.QuerySet.update(Customer.objects.filter(pk=acme.pk), data_mode="on_site")
    acme.refresh_from_db()
    source = Source.objects.for_customer(acme).get(pk=source.pk)
    with pytest.raises(OnSiteDataError):
        store_outputs(source, "ios", outputs("floor-sw1"))
    with pytest.raises(OnSiteDataError):
        reparse(source)
