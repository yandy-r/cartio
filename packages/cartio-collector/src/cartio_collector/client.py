"""Talks to the controller. The collector always connects out; it never listens."""

from __future__ import annotations

import platform
import socket
from collections.abc import Iterator
from contextlib import contextmanager

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
    except ValueError:
        detail = response.text
    raise ControllerError(f"controller answered {response.status_code}: {detail}")


def enroll(controller_url: str, token: str, *, http: httpx.Client | None = None) -> CollectorConfig:
    """Exchange a one-time enrollment token for this collector's identity."""
    request = EnrollRequest(token=token, hostname=socket.gethostname())
    with _client(http) as client:
        response = client.post(controller_url.rstrip("/") + ENROLL_PATH, json=request.to_dict())
    _raise_for_status(response)
    enrolled = EnrollResponse.from_dict(response.json())
    return CollectorConfig(
        controller_url=controller_url.rstrip("/"),
        collector_id=enrolled.collector_id,
        secret=enrolled.secret,
        customer=enrolled.customer,
    )


def check_in(config: CollectorConfig, *, http: httpx.Client | None = None) -> CheckInResponse:
    """Tell the controller this collector is alive, and receive any jobs."""
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
    return CheckInResponse.from_dict(response.json())
