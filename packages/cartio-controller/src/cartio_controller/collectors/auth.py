"""Checks a collector's signed request (ADR-0009): signature, clock window, nonce, revocation.

Every collector endpoint after enrollment calls `signed_collector` first. It runs on every
request, so revoking a collector cuts it off at its next request.
"""

from datetime import UTC, datetime

from cartio_core import signing
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone

from .models import Collector, RequestNonce


class NotAuthenticated(Exception):
    """The request isn't a valid signed request from a live collector."""


def signed_collector(request: HttpRequest) -> Collector:
    """The collector that signed `request`, or NotAuthenticated."""
    try:
        signed = signing.parse(request.headers.get("Authorization", ""))
    except signing.SignatureError as exc:
        raise NotAuthenticated(str(exc)) from exc

    # Unscoped on purpose: the signed id is what tells us the customer. The key check below
    # makes sure the id belongs to whoever signed.
    collector = Collector.for_id(signed.collector_id)
    if collector is None or collector.revoked_at is not None or not collector.public_key:
        raise NotAuthenticated("unknown or revoked collector")
    try:
        signing.verify(
            collector.public_key, signed, request.method, request.get_full_path(), request.body
        )
    except signing.SignatureError as exc:
        raise NotAuthenticated(str(exc)) from exc

    now = timezone.now()
    expires = datetime.fromtimestamp(signed.ts + signing.CLOCK_WINDOW, tz=UTC)
    nonces = collector.scoped(RequestNonce)
    try:
        with transaction.atomic():
            nonces.create(collector=collector, nonce=signed.nonce, expires_at=expires)
    except IntegrityError as exc:
        raise NotAuthenticated("this request was already used") from exc
    # Nonces past the clock window can't be replayed anyway, since their timestamp is refused.
    nonces.filter(collector=collector, expires_at__lt=now).delete()
    return collector
