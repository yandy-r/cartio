"""Store a device's raw outputs and config, and build its inventory records from them (ADR-0008).

The records are the Device, its Interfaces, its Links (L2) and its RoutingNeighbors (L3).

Outputs arrive masked from the collector (or an upload) and are kept as `RawOutput`s, so the
parsers in `cartio_core.parsers` can be re-run on them later. Parsing is idempotent: storing
then re-parsing a `Source` gives the same records, matched by name, whatever order the devices
came in. Configs are kept as `ConfigSnapshot`s.

Everything is scanned with `find_unmasked` on the way in. A hit refuses the whole upload and is
audit-logged with line numbers and kinds only, never the line itself. Call these outside a
transaction that the error would roll back, or the refusal isn't logged.

Hosted customers only. An On site customer's network data stays in their own store, and the
models refuse it too (`OnSiteDataError`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from cartio_core.masking import find_unmasked, placeholder_versions
from cartio_core.parsers import ParsedDevice, expand_interface, parse
from django.db import models, transaction

from . import audit
from .models import (
    AuditEvent,
    ConfigSnapshot,
    Customer,
    Device,
    Interface,
    Link,
    OnSiteDataError,
    RawOutput,
    RoutingNeighbor,
)


class UnmaskedOutputError(ValueError):
    """Output or a config that still holds a secret. The collector should have masked it."""


class UnmaskedConfigError(UnmaskedOutputError):
    """A config that still holds a secret."""


class KeyVersionError(ValueError):
    """A config whose placeholders weren't all made with the key version it names."""


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
        if found := find_unmasked(text, platform):
            _log_refusal(
                source,
                AuditEvent.Action.OUTPUTS_REJECTED,
                found,
                actor=actor,
                object_type="source",
                object_id=source.pk,
                object_name=source.name,
                command=command,
            )
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


def store_config(
    source,
    device: Device,
    platform: str,
    text: str,
    *,
    key_version: str,
    actor: str = "",
) -> ConfigSnapshot:
    """Keep a device's masked config, made with masking key `key_version`, as a new snapshot.

    The first snapshot of a device after a key rotation is marked `key_rotated`, so its new
    placeholders aren't read as changed secrets. The device is matched by name across the
    customer's engagements, as the engagement-to-engagement diff matches it.
    """
    customer = source.customer
    _refuse_on_site(customer)
    if found := find_unmasked(text, platform):
        _log_refusal(
            source,
            AuditEvent.Action.CONFIG_REJECTED,
            found,
            actor=actor,
            object_type="device",
            object_id=device.pk,
            object_name=device.name,
        )
        raise UnmaskedConfigError(f"The config of {device.name} has an unmasked secret.")
    if placeholder_versions(text) - {key_version}:
        raise KeyVersionError(
            f"The config of {device.name} has placeholders from another masking key version."
        )
    snapshots = ConfigSnapshot.objects.for_customer(customer)
    previous = (
        snapshots.filter(device__name__iexact=device.name)
        .order_by("-collected_at", "-pk")
        .values_list("key_version", flat=True)
        .first()
    )
    with transaction.atomic():
        snapshot = snapshots.create(
            device=device,
            source=source,
            platform=platform,
            content=text,
            key_version=key_version,
            # A snapshot from before key versions were kept has none, so it can't tell.
            key_rotated=bool(previous) and previous != key_version,
        )
        audit.record(
            customer,
            AuditEvent.Action.CONFIG_STORED,
            object_type="device",
            object_id=device.pk,
            object_name=device.name,
            actor=actor,
            source=str(source.pk),
            sha256=snapshot.sha256,
            key_version=key_version,
            key_rotated=snapshot.key_rotated,
        )
    return snapshot


def _log_refusal(source, action: str, found, **event) -> None:
    # Line numbers and kinds only: the line itself would put the secret in the log.
    with transaction.atomic():
        audit.record(
            source.customer,
            action,
            source=str(source.pk),
            lines=[f.line for f in found],
            kinds=sorted({f.kind for f in found}),
            **event,
        )


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
    # Other devices' neighbors at one of this device's addresses (found before it arrived), or
    # pointing at it from an address it no longer has: resolve them again.
    addresses = list(
        interfaces.filter(device=device)
        .exclude(ip_address=None)
        .values_list("ip_address", flat=True)
    )
    affected = neighbors.filter(device__engagement=source.engagement).filter(
        models.Q(address__in=addresses) | models.Q(peer=device)
    )
    for neighbor in affected.exclude(device=device).select_related("device"):
        peer = _owner(source, neighbor.address, exclude=neighbor.device)
        if peer != neighbor.peer:
            neighbor.peer = peer
            neighbor.save()


def _owner(source, address: str, *, exclude: Device) -> Device | None:
    """The engagement's one device with an interface at this address, once it's collected.

    An address on more than one device (VRFs, overlapping ranges, a shared VIP) is ambiguous,
    so it resolves to no device rather than an arbitrary one.
    """
    owners = list(
        Device.objects.for_customer(source.customer)
        .filter(engagement=source.engagement, interfaces__ip_address=address)
        .exclude(pk=exclude.pk)
        .distinct()[:2]
    )
    return owners[0] if len(owners) == 1 else None


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
