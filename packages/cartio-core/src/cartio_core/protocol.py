"""Messages exchanged between a collector and the controller.

Both sides import these, so the shape of a message is defined once. All traffic is
started by the collector (ADR-0001): it enrolls once, then checks in.

Authentication here is a placeholder. A one-time enrollment token is exchanged for a
collector secret, sent as a bearer token. It will be replaced by per-collector mTLS
certificates (ADR-0001, YAN-1640).
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrollRequest:
        return cls(token=_require(data, "token", str), hostname=_require(data, "hostname", str))


@dataclass(frozen=True)
class EnrollResponse:
    collector_id: str
    secret: str
    customer: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrollResponse:
        return cls(
            collector_id=_require(data, "collector_id", str),
            secret=_require(data, "secret", str),
            customer=_require(data, "customer", str),
        )


@dataclass(frozen=True)
class CheckIn:
    hostname: str
    version: str
    platform: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckIn:
        return cls(
            hostname=_require(data, "hostname", str),
            version=_require(data, "version", str),
            platform=_require(data, "platform", str),
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


def bearer(collector_id: str, secret: str) -> str:
    """The Authorization header value a collector sends after enrolling."""
    return f"Bearer {collector_id}.{secret}"


def parse_bearer(header: str) -> tuple[str, str]:
    """Split an Authorization header into collector id and secret."""
    scheme, _, credentials = header.partition(" ")
    collector_id, _, secret = credentials.partition(".")
    if scheme != "Bearer" or not collector_id or not secret:
        raise ProtocolError("expected 'Bearer <collector_id>.<secret>'")
    return collector_id, secret
