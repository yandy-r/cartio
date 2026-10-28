"""Messages exchanged between a collector and the controller.

Both sides import these, so the shape of a message is defined once. All traffic is
started by the collector (ADR-0001): it enrolls once, then checks in.

Enrollment exchanges a one-time token and the collector's public key for its identity. Every
request after that is signed with the collector's private key (`cartio_core.signing`, ADR-0009).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

API_PREFIX = "/api/v1"
ENROLL_PATH = f"{API_PREFIX}/enroll"
CHECKIN_PATH = f"{API_PREFIX}/checkin"


class ProtocolError(ValueError):
    """A message is missing a field or has a field of the wrong type."""


def _require(data: dict[str, Any], key: str, kind: type) -> Any:
    if key not in data:
        raise ProtocolError(f"missing field: {key}")
    value = data[key]
    if not isinstance(value, kind):
        raise ProtocolError(f"field {key} must be {kind.__name__}")
    return value


@dataclass(frozen=True)
class EnrollRequest:
    token: str
    hostname: str
    # The collector's Ed25519 public key. The private key never leaves the collector host.
    public_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrollRequest:
        return cls(
            token=_require(data, "token", str),
            hostname=_require(data, "hostname", str),
            public_key=_require(data, "public_key", str),
        )


@dataclass(frozen=True)
class EnrollResponse:
    collector_id: str
    customer: str
    # Pins of the controller's TLS server keys (`signing.spki_pin`). Empty if the controller
    # doesn't publish any, and the collector then pins the key it saw when it enrolled.
    tls_pins: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrollResponse:
        pins = _require(data, "tls_pins", list)
        if not all(isinstance(pin, str) for pin in pins):
            raise ProtocolError("field tls_pins must be a list of strings")
        return cls(
            collector_id=_require(data, "collector_id", str),
            customer=_require(data, "customer", str),
            tls_pins=pins,
        )


@dataclass(frozen=True)
class CheckIn:
    hostname: str
    version: str
    platform: str
    # The controller's TLS key didn't match the collector's pin: something on the path, such
    # as a TLS-inspecting proxy, re-signed the connection. Reported, not blocked (ADR-0009).
    tls_inspected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckIn:
        return cls(
            hostname=_require(data, "hostname", str),
            version=_require(data, "version", str),
            platform=_require(data, "platform", str),
            tls_inspected=_require(data, "tls_inspected", bool),
        )


@dataclass(frozen=True)
class CheckInResponse:
    status: str
    # Jobs for the collector to run. Always empty until discovery lands (M2).
    jobs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckInResponse:
        return cls(status=_require(data, "status", str), jobs=_require(data, "jobs", list))
