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
    Source,
)
from cartio_core.parsers import ParseError

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
            "device__name", "name", "description", "enabled", "mac_address", "mtu", "speed"
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
    return devices, interfaces, links


EXPECTED_LINKS = [
    ("access-sw1", "GigabitEthernet1/0/49", "core-sw1", "TenGigabitEthernet1/1/1", "", "", "cdp"),
    ("access-sw1", "GigabitEthernet1/0/49", "core-sw1", "TenGigabitEthernet1/1/1", "", "", "lldp"),
    ("core-sw1", "GigabitEthernet1/0/48", "edge-fw1", "port1", "", "", "lldp"),
    ("core-sw1", "TenGigabitEthernet1/1/1", "access-sw1", "GigabitEthernet1/0/49", "", "", "cdp"),
    ("core-sw1", "TenGigabitEthernet1/1/1", "access-sw1", "GigabitEthernet1/0/49", "", "", "lldp"),
]


def test_the_lab_becomes_devices_interfaces_and_links(acme):
    source = _source(acme)
    _store(source, "access-sw1", "core-sw1", "edge-fw1")
    devices, interfaces, links = _inventory(acme)
    assert devices == [
        (
            "access-sw1",
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
    assert len(interfaces) == 4 + 4 + 5
    assert links == EXPECTED_LINKS
    assert RawOutput.objects.for_customer(acme).count() == 5 + 5 + 3
    assert set(
        RawOutput.objects.for_customer(acme)
        .filter(device__name="edge-fw1")
        .values_list("command", flat=True)
    ) == set(outputs("edge-fw1"))


@pytest.mark.parametrize(
    "order",
    [
        ("access-sw1", "core-sw1", "edge-fw1"),
        ("edge-fw1", "core-sw1", "access-sw1"),
        ("core-sw1", "edge-fw1", "access-sw1"),
    ],
)
def test_links_resolve_whatever_order_devices_arrive_in(acme, order):
    _store(_source(acme), *order)
    assert _inventory(acme)[2] == EXPECTED_LINKS


def test_a_neighbor_not_collected_keeps_its_announced_names(acme):
    _store(_source(acme), "core-sw1")
    links = _inventory(acme)[2]
    assert ("core-sw1", "GigabitEthernet1/0/48", "", "", "edge-fw1", "port1", "lldp") in links


def test_reparsing_a_source_reproduces_the_same_records(acme):
    source = _source(acme)
    _store(source, "access-sw1", "core-sw1", "edge-fw1")
    before = _inventory(acme)
    outputs_before = RawOutput.objects.for_customer(acme).count()

    devices = reparse(source)

    assert sorted(d.name for d in devices) == ["access-sw1", "core-sw1", "edge-fw1"]
    assert _inventory(acme) == before
    # Re-parsing reads the stored outputs; it doesn't add any.
    assert RawOutput.objects.for_customer(acme).count() == outputs_before


def test_reparsing_rebuilds_records_changed_since(acme):
    source = _source(acme)
    _store(source, "access-sw1", "core-sw1")
    before = _inventory(acme)
    Interface.objects.for_customer(acme).filter(device__name="core-sw1").update(description="x")
    Link.objects.for_customer(acme).filter(protocol="cdp").delete()
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
    lab = outputs("access-sw1")
    lab["show version"] += SP.join(["enable", "secret", "5", "$1$abcd$0123456789abcdefghij"])
    with pytest.raises(UnmaskedOutputError):
        store_outputs(source, "ios", lab)
    assert not RawOutput.objects.for_customer(acme).exists()
    assert not Device.objects.for_customer(acme).exists()


def test_output_without_a_host_name_stores_nothing(acme):
    source = _source(acme)
    with pytest.raises(ParseError):
        store_outputs(source, "ios", {"show interfaces": outputs("access-sw1")["show interfaces"]})
    assert not RawOutput.objects.for_customer(acme).exists()


def test_a_neighbor_is_never_matched_in_another_customer(acme):
    globex = Customer.objects.create(name="Globex")
    _store(_source(globex), "access-sw1")
    _store(_source(acme), "core-sw1")
    for link in Link.objects.for_customer(acme):
        assert link.b_interface is None
    assert Link.objects.for_customer(globex).filter(b_interface=None).count() == 2


def test_a_neighbor_is_never_matched_in_another_engagement(acme):
    first = _source(acme)
    _store(first, "access-sw1")
    _store(_source(acme, "Next audit"), "core-sw1")
    assert not Link.objects.for_customer(acme).exclude(b_interface=None).exists()


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
    Customer.objects.filter(pk=acme.pk).update(data_mode=Customer.DataMode.ON_SITE)
    acme.refresh_from_db()
    source = Source.objects.for_customer(acme).get(pk=source.pk)
    with pytest.raises(OnSiteDataError):
        store_outputs(source, "ios", outputs("access-sw1"))
    with pytest.raises(OnSiteDataError):
        reparse(source)
