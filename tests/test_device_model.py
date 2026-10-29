"""The normalized device model keeps customers and engagements apart (YAN-1656, ADR-0008).

The generic isolation and database-guard tests cover every table already. These cover what's
particular to the device model: engagements don't mix, and stored output carries its digest.
"""

import hashlib

import pytest
from cartio_controller.collectors.models import (
    AppendOnlyError,
    CollectionRun,
    Collector,
    ConfigSnapshot,
    CrossCustomerError,
    CrossEngagementError,
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
from django.db import IntegrityError, models, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _engagement(customer, name="2026 audit"):
    return Engagement.objects.for_customer(customer).create(name=name)


def _device(engagement, name="core-sw1"):
    source = Source.objects.for_customer(engagement.customer).create(
        engagement=engagement, kind="upload", name="show-version.txt"
    )
    device = Device.objects.for_customer(engagement.customer).create(
        engagement=engagement, source=source, name=name
    )
    return device, source


def test_a_device_cant_use_another_customers_engagement(acme):
    globex = Customer.objects.create(name="Globex")
    with pytest.raises(CrossCustomerError):
        Device.objects.for_customer(acme).create(engagement=_engagement(globex), name="sw1")


def test_a_link_cant_reach_another_customers_interface(acme):
    globex = Customer.objects.create(name="Globex")
    mine, _ = _device(_engagement(acme))
    theirs, _ = _device(_engagement(globex))
    a = Interface.objects.for_customer(acme).create(device=mine, name="Gi1/0/1")
    b = Interface.objects.for_customer(globex).create(device=theirs, name="Gi1/0/1")
    with pytest.raises(CrossCustomerError):
        Link.objects.for_customer(acme).create(a_interface=a, b_interface=b)


def test_engagements_dont_mix(acme):
    this_year, last_year = _engagement(acme), _engagement(acme, "2025 audit")
    device, source = _device(this_year)
    old_device, old_source = _device(last_year)
    a = Interface.objects.for_customer(acme).create(device=device, name="Gi1/0/1")
    b = Interface.objects.for_customer(acme).create(device=old_device, name="Gi1/0/1")
    scoped = Device.objects.for_customer(acme)

    with pytest.raises(CrossEngagementError):
        scoped.create(engagement=this_year, source=old_source, name="sw2")
    with pytest.raises(CrossEngagementError):
        Link.objects.for_customer(acme).create(a_interface=a, b_interface=b)
    with pytest.raises(CrossEngagementError):
        ConfigSnapshot.objects.for_customer(acme).create(
            device=device, source=old_source, content="hostname core-sw1"
        )
    with pytest.raises(CrossEngagementError):
        RawOutput.objects.for_customer(acme).create(
            source=old_source, device=device, command="show version", output="x"
        )
    device.engagement = last_year
    with pytest.raises(CrossEngagementError):
        device.save()
    # The same host name is a separate record in each engagement.
    assert scoped.filter(name="core-sw1").count() == 2


def test_output_and_configs_keep_their_digest_and_never_change(acme):
    device, source = _device(_engagement(acme))
    snapshot = ConfigSnapshot.objects.for_customer(acme).create(
        device=device, source=source, content="hostname core-sw1\n"
    )
    assert snapshot.sha256 == hashlib.sha256(b"hostname core-sw1\n").hexdigest()
    snapshot.content = "hostname other"
    with pytest.raises(AppendOnlyError):
        snapshot.save()
    with pytest.raises(AppendOnlyError):
        RawOutput.objects.for_customer(acme).update(output="x")

    # A bulk insert computes the digest too, whatever it was given.
    (output,) = RawOutput.objects.for_customer(acme).bulk_create(
        [RawOutput(source=source, command="show version", output="x", sha256="a" * 64)]
    )
    assert RawOutput.objects.for_customer(acme).get(pk=output.pk).sha256 == (
        hashlib.sha256(b"x").hexdigest()
    )


def test_a_live_run_source_names_its_run_and_its_engagement(acme):
    engagement = _engagement(acme)
    with pytest.raises(IntegrityError), transaction.atomic():
        Source.objects.for_customer(acme).create(engagement=engagement, kind="live_run")
    collector = Collector.objects.for_customer(acme).create(name="vm01")
    run = CollectionRun.objects.for_customer(acme).create(job_type="discovery", collector=collector)
    with pytest.raises(CrossEngagementError):
        Source.objects.for_customer(acme).create(engagement=engagement, kind="live_run", run=run)


def test_a_routing_neighbor_stays_in_its_customer_and_engagement(acme):
    globex = Customer.objects.create(name="Globex")
    engagement = _engagement(acme)
    mine, source = _device(engagement)
    later, _ = _device(_engagement(acme, "2027 audit"), "edge-fw1")
    theirs, _ = _device(_engagement(globex), "edge-fw1")
    neighbors = RoutingNeighbor.objects.for_customer(acme)
    with pytest.raises(CrossCustomerError):
        neighbors.create(device=mine, protocol="ospf", address="192.0.2.1", peer=theirs)
    with pytest.raises(CrossEngagementError):
        neighbors.create(device=mine, protocol="ospf", address="192.0.2.1", peer=later)
    other_port = Interface.objects.for_customer(acme).create(device=later, name="port1")
    with pytest.raises(ValueError, match="belong to the device"):
        neighbors.create(device=mine, interface=other_port, protocol="ospf", address="192.0.2.1")
    with transaction.atomic(), pytest.raises(IntegrityError):
        neighbors.create(device=mine, protocol="ospf", address="192.0.2.1", peer=mine)
    neighbors.create(device=mine, protocol="bgp", address="203.0.113.1", source=source)


def test_on_site_customers_refuse_routing_neighbors(acme):
    device, _ = _device(_engagement(acme))
    # Past the data mode action: these rows predate the switch, which would delete them.
    models.QuerySet.update(Customer.objects.filter(pk=acme.pk), data_mode="on_site")
    with pytest.raises(OnSiteDataError):
        RoutingNeighbor.objects.for_customer(acme).create(
            device=device, protocol="ospf", address="192.0.2.1"
        )
