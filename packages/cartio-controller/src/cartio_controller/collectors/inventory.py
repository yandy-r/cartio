"""Store a device's raw outputs and build its inventory records from them (ADR-0008).

The records are the Device, its Interfaces, its Links (L2) and its RoutingNeighbors (L3).

Outputs arrive masked from the collector (or an upload) and are kept as `RawOutput`s, so the
parsers in `cartio_core.parsers` can be re-run on them later. Parsing is idempotent: storing
then re-parsing a `Source` gives the same records, matched by name, whatever order the devices
came in.

Hosted customers only. An On site customer's network data stays in their own store, and the
models refuse it too (`OnSiteDataError`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from cartio_core.masking import find_unmasked
from cartio_core.parsers import ParsedDevice, expand_interface, parse
from django.db import transaction

from . import audit
from .models import (
    AuditEvent,
    Customer,
    Device,
    Interface,
    Link,
    OnSiteDataError,
    RawOutput,
    RoutingNeighbor,
)


class UnmaskedOutputError(ValueError):
    """Output that still holds a secret. The collector should have masked it."""


def store_outputs(
    source,
    platform: str,
    outputs: Mapping[str, str],
    *,
    management_ip: str | None = None,
    actor: str = "",
) -> Device:
    """Keep one device's masked outputs, by command, and parse them into the inventory."""
    _refuse_on_site(source.customer)
    for command, text in outputs.items():
        if find_unmasked(text, platform):
            raise UnmaskedOutputError(f"The output of {command!r} has an unmasked secret.")
    parsed = parse(platform, outputs)
    with transaction.atomic():
        device = _apply(source, parsed, management_ip=management_ip)
        RawOutput.objects.for_customer(source.customer).bulk_create(
            RawOutput(source=source, device=device, platform=platform, command=command, output=text)
            for command, text in outputs.items()
        )
        audit.record(
            source.customer,
            AuditEvent.Action.OUTPUTS_STORED,
            object_type="device",
            object_id=device.pk,
            object_name=device.name,
            actor=actor,
            source=str(source.pk),
            commands=sorted(outputs),
        )
    return device


def reparse(source, *, actor: str = "") -> list[Device]:
    """Parse a source's stored outputs again, after a parser fix. Returns the devices touched."""
    _refuse_on_site(source.customer)
    batches: dict[tuple, dict[str, str]] = defaultdict(dict)
    outputs = RawOutput.objects.for_customer(source.customer).filter(source=source)
    for raw in outputs.exclude(device=None).select_related("device").order_by("collected_at"):
        # The latest output of a command wins.
        key = (raw.device_id, raw.platform, raw.device.management_ip)
        batches[key][raw.command] = raw.output
    devices = []
    with transaction.atomic():
        for (_, platform, management_ip), batch in batches.items():
            parsed = parse(platform, batch)
            devices.append(_apply(source, parsed, management_ip=management_ip))
        audit.record(
            source.customer,
            AuditEvent.Action.SOURCE_REPARSED,
            object_type="source",
            object_id=source.pk,
            object_name=source.name,
            actor=actor,
            devices=len(devices),
        )
    return devices


def _refuse_on_site(customer: Customer) -> None:
    if customer.data_mode == Customer.DataMode.ON_SITE:
        raise OnSiteDataError(
            "This customer is On site: device data stays in the customer's own store."
        )


def _apply(source, parsed: ParsedDevice, *, management_ip: str | None) -> Device:
    customer = source.customer
    fields = {
        "platform": parsed.platform.value,
        "manufacturer": parsed.manufacturer,
        "model": parsed.model,
        "serial": parsed.serial,
        "os_version": parsed.os_version,
        "management_ip": management_ip,
        "last_seen": source.created_at,
    }
    # `source` is where the device was first found, so only a new device takes it.
    device, _ = Device.objects.for_customer(customer).update_or_create(
        engagement=source.engagement,
        name=parsed.name,
        defaults=fields,
        create_defaults={**fields, "source": source},
    )
    interfaces = Interface.objects.for_customer(customer)
    for found in parsed.interfaces:
        interfaces.update_or_create(
            device=device,
            name=found.name,
            defaults={
                "description": found.description,
                "enabled": found.enabled,
                "mac_address": found.mac_address,
                "mtu": found.mtu,
                "speed": found.speed,
                "ip_address": found.ip_address or None,
                "prefix_length": found.prefix_length,
            },
        )

    links = Link.objects.for_customer(customer)
    # This source's links from this device are rebuilt from its outputs.
    links.filter(source=source, a_interface__device=device).delete()
    for neighbor in parsed.neighbors:
        local, _ = interfaces.get_or_create(device=device, name=neighbor.local_interface)
        far = _interface(source, neighbor.device_name, neighbor.interface_name)
        links.create(
            a_interface=local,
            b_interface=far,
            b_device_name="" if far else neighbor.device_name,
            b_interface_name="" if far else neighbor.interface_name,
            protocol=neighbor.protocol,
            source=source,
        )
    # Links that other devices found to this one before it was collected.
    for link in links.filter(
        a_interface__device__engagement=source.engagement,
        b_interface=None,
        b_device_name=device.name,
    ):
        far = _interface(source, device.name, link.b_interface_name)
        if far is not None and far != link.a_interface:
            link.b_interface, link.b_device_name, link.b_interface_name = far, "", ""
            link.save()
    _apply_routing(source, parsed, device)
    return device


def _apply_routing(source, parsed: ParsedDevice, device: Device) -> None:
    customer = source.customer
    neighbors = RoutingNeighbor.objects.for_customer(customer)
    # This source's routing neighbors of this device are rebuilt from its outputs.
    neighbors.filter(source=source, device=device).delete()
    interfaces = Interface.objects.for_customer(customer)
    for found in parsed.routing_neighbors:
        local = (
            interfaces.filter(device=device, name=found.local_interface).first()
            if found.local_interface
            else None
        )
        neighbors.create(
            device=device,
            interface=local,
            protocol=found.protocol,
            address=found.address,
            router_id=found.router_id,
            remote_as=found.remote_as,
            state=found.state,
            peer=_owner(source, found.address, exclude=device),
            source=source,
        )
    # Neighbors that other devices found at one of this device's addresses before it arrived.
    addresses = list(
        interfaces.filter(device=device)
        .exclude(ip_address=None)
        .values_list("ip_address", flat=True)
    )
    for neighbor in neighbors.filter(
        device__engagement=source.engagement, peer=None, address__in=addresses
    ).exclude(device=device):
        neighbor.peer = device
        neighbor.save()


def _owner(source, address: str, *, exclude: Device) -> Device | None:
    """The engagement's device with an interface at this address, if it has been collected."""
    return (
        Device.objects.for_customer(source.customer)
        .filter(engagement=source.engagement, interfaces__ip_address=address)
        .exclude(pk=exclude.pk)
        .first()
    )


def _interface(source, device_name: str, interface_name: str) -> Interface | None:
    """The engagement's interface with these names, if the device has been collected."""
    names = list(
        Interface.objects.for_customer(source.customer)
        .filter(device__engagement=source.engagement, device__name=device_name)
        .values_list("name", flat=True)
    )
    if not names:
        return None
    name = expand_interface(interface_name, names)
    return (
        Interface.objects.for_customer(source.customer)
        .filter(device__engagement=source.engagement, device__name=device_name, name=name)
        .first()
    )
