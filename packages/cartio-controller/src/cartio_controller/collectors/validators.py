"""Checks shared by the API and the admin."""

import ipaddress
import re

from django.core.exceptions import ValidationError

MAX_SEED_HINTS = 256
# Shortest prefix a seed network may have, so a typo can't turn into a sweep of the internet.
MIN_IPV4_PREFIX = 16
MIN_IPV6_PREFIX = 48

# Letters, digits and the punctuation of addresses, networks and host names. No spaces, control
# characters, "%" scope IDs or shell metacharacters, since hints end up in scanner arguments.
_SEED_CHARS = re.compile(r"[0-9A-Za-z.:/-]{1,255}")
_HOSTNAME_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
# Resolvers that accept inet_aton forms read these as addresses ("0x7f000001" is 127.0.0.1).
_NUMERIC_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")


def normalize_seed_hint(value: str) -> str:
    """Return the canonical form of one seed hint, or raise ValidationError."""
    if not isinstance(value, str) or not _SEED_CHARS.fullmatch(value):
        raise ValidationError(f"{value!r} is not an IP address, a CIDR network or a host name.")
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return _hostname(value)

    minimum = MIN_IPV4_PREFIX if network.version == 4 else MIN_IPV6_PREFIX
    if network.prefixlen < minimum:
        raise ValidationError(f"{value} is too wide; use /{minimum} or narrower.")
    address = network.network_address
    if (
        address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or address.is_loopback
        or address.is_link_local
    ):
        raise ValidationError(f"{value} is not an address on a customer network.")
    if network.num_addresses == 1:
        return str(address)
    return str(network)


def _hostname(value: str) -> str:
    name = value.rstrip(".").lower()
    labels = name.split(".")
    # A numeric last label is an address in disguise, not a host name.
    if (
        len(name) > 253
        or not all(_HOSTNAME_LABEL.fullmatch(label) for label in labels)
        or _NUMERIC_LABEL.fullmatch(labels[-1])
        or name == "localhost"
    ):
        raise ValidationError(f"{value!r} is not an IP address, a CIDR network or a host name.")
    return name


def validate_seed_hints(value) -> None:
    if not isinstance(value, list):
        raise ValidationError("Seed hints must be a list.")
    if len(value) > MAX_SEED_HINTS:
        raise ValidationError(f"At most {MAX_SEED_HINTS} seed hints.")
    for hint in value:
        normalize_seed_hint(hint)


COMPLIANCE = ("hipaa", "pci", "soc2")


def validate_compliance(value) -> None:
    if not isinstance(value, list) or not all(item in COMPLIANCE for item in value):
        raise ValidationError(f"Compliance must be a list of {', '.join(COMPLIANCE)}.")
    if len(set(value)) != len(value):
        raise ValidationError("Each compliance requirement once.")
