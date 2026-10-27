"""Talks to the controller. The collector always connects out; it never listens."""

from __future__ import annotations

import ipaddress
import platform
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

import httpx
from cartio_core.protocol import (
    CHECKIN_PATH,
    ENROLL_PATH,
    CheckIn,
    CheckInResponse,
    EnrollRequest,
    EnrollResponse,
    bearer,
)

from . import __version__
from .config import CollectorConfig


class ControllerError(RuntimeError):
    """The controller refused a request or answered with something unexpected."""


def _require_secure(url: str) -> None:
    """Credentials travel on every request, so plain http is only allowed to this machine."""
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and _is_loopback(parsed.hostname):
        return
    raise ControllerError(
        f"controller URL must use https (http is allowed only for localhost): {url}"
    )


def _is_loopback(host: str | None) -> bool:
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@contextmanager
def _client(http: httpx.Client | None) -> Iterator[httpx.Client]:
    """Use the caller's client, or open one for this call and close it after."""
    if http is not None:
        yield http
        return
    with httpx.Client(timeout=30) as client:
        yield client


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("error", response.text)
    except (ValueError, AttributeError):
        detail = response.text
    raise ControllerError(f"controller answered {response.status_code}: {detail}")


def _parse(response: httpx.Response, kind: Any) -> Any:
    """Read a successful answer, which a proxy on a customer network may have replaced."""
    try:
        return kind.from_dict(response.json())
    except (ValueError, TypeError) as exc:
        raise ControllerError(f"controller sent an unexpected answer: {exc}") from exc


def enroll(controller_url: str, token: str, *, http: httpx.Client | None = None) -> CollectorConfig:
    """Exchange a one-time enrollment token for this collector's identity."""
    _require_secure(controller_url)
    request = EnrollRequest(token=token, hostname=socket.gethostname())
    with _client(http) as client:
        response = client.post(controller_url.rstrip("/") + ENROLL_PATH, json=request.to_dict())
    _raise_for_status(response)
    enrolled = _parse(response, EnrollResponse)
    return CollectorConfig(
        controller_url=controller_url.rstrip("/"),
        collector_id=enrolled.collector_id,
        secret=enrolled.secret,
        customer=enrolled.customer,
    )


def check_in(config: CollectorConfig, *, http: httpx.Client | None = None) -> CheckInResponse:
    """Tell the controller this collector is alive, and receive any jobs."""
    _require_secure(config.controller_url)
    message = CheckIn(
        hostname=socket.gethostname(),
        version=__version__,
        platform=platform.platform(),
    )
    with _client(http) as client:
        response = client.post(
            config.controller_url + CHECKIN_PATH,
            json=message.to_dict(),
            headers={"Authorization": bearer(config.collector_id, config.secret)},
        )
    _raise_for_status(response)
    return _parse(response, CheckInResponse)
