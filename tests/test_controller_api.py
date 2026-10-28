import json
from datetime import timedelta

import pytest
from cartio_controller.collectors.models import (
    AuditEvent,
    CheckIn,
    Collector,
    Customer,
    RequestNonce,
)
from cartio_core import signing
from cartio_core.protocol import CHECKIN_PATH, ENROLL_PATH
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from tests.signed import CHECKIN, give_key, signed_post

pytestmark = pytest.mark.django_db


def _new_collector(customer="Acme", name="vm01"):
    collector = Collector(customer=Customer.objects.get_or_create(name=customer)[0], name=name)
    token = collector.issue_enrollment_token()
    collector.save()
    return collector, token


def _post(client, path, body, **headers):
    return client.post(path, data=json.dumps(body), content_type="application/json", **headers)


def _enroll_body(token, private_key=None):
    private_key = private_key or signing.new_private_key()
    return {"token": token, "hostname": "vm01", "public_key": signing.public_key_for(private_key)}


def _enrolled():
    collector, _ = _new_collector()
    return collector, give_key(collector)


def test_enroll_stores_the_public_key_and_burns_the_token(client):
    collector, token = _new_collector()
    private_key = signing.new_private_key()

    response = _post(client, ENROLL_PATH, _enroll_body(token, private_key))
    assert response.status_code == 201
    body = response.json()
    assert body == {"collector_id": str(collector.id), "customer": "Acme", "tls_pins": []}

    collector.refresh_from_db()
    assert collector.enrolled_at is not None
    assert collector.enrollment_token_hash == ""
    assert collector.public_key == signing.public_key_for(private_key)
    # Only the public key is sent, so the private key can't be on the controller.
    row = Collector.all_customers.filter(pk=collector.pk).values().get()
    assert private_key not in json.dumps(row, default=str)
    event = AuditEvent.objects.for_customer(collector.customer).get(action="collector.enrolled")
    assert event.detail == {"hostname": "vm01", "public_key": collector.public_key}

    again = _post(client, ENROLL_PATH, _enroll_body(token))
    assert again.status_code == 403
    collector.refresh_from_db()
    assert collector.public_key == signing.public_key_for(private_key)


@override_settings(TLS_PINS=["pin-a", "pin-b"])
def test_enroll_hands_out_the_controllers_tls_pins(client):
    _, token = _new_collector()
    assert _post(client, ENROLL_PATH, _enroll_body(token)).json()["tls_pins"] == ["pin-a", "pin-b"]


def test_an_expired_token_is_refused(client):
    collector, token = _new_collector()
    collector.enrollment_expires_at = timezone.now() - timedelta(seconds=1)
    collector.save()
    response = _post(client, ENROLL_PATH, _enroll_body(token))
    assert response.status_code == 403
    assert "expired" in response.json()["error"]
    collector.refresh_from_db()
    assert collector.enrolled_at is None and collector.public_key == ""


def test_tokens_expire_after_the_configured_lifetime():
    collector = Collector(customer=Customer.objects.create(name="Acme"), name="vm01")
    now = timezone.now()
    collector.issue_enrollment_token(now)
    assert collector.enrollment_expires_at == now + timedelta(hours=24)
    assert collector.enrollment_open(now + timedelta(hours=23))
    assert not collector.enrollment_open(now + timedelta(hours=24))


def test_a_revoked_collectors_token_is_refused(client):
    collector, token = _new_collector()
    collector.revoke(timezone.now())
    collector.save()
    assert _post(client, ENROLL_PATH, _enroll_body(token)).status_code == 403


@pytest.mark.parametrize(
    "public_key", ["", "not base64!", "AAAA", signing.new_private_key() + "AA"]
)
def test_enroll_rejects_a_bad_public_key(client, public_key):
    collector, token = _new_collector()
    body = {"token": token, "hostname": "vm01", "public_key": public_key}
    assert _post(client, ENROLL_PATH, body).status_code == 400
    collector.refresh_from_db()
    assert collector.enrollment_token_hash != ""


def test_enroll_rejects_unknown_token_and_bad_json(client):
    assert _post(client, ENROLL_PATH, _enroll_body("nope")).status_code == 403
    bad = client.post(ENROLL_PATH, data="not json", content_type="application/json")
    assert bad.status_code == 400


def test_tokens_are_stored_hashed():
    collector, token = _new_collector()
    stored = Collector.all_customers.values_list("enrollment_token_hash", flat=True).get(
        pk=collector.pk
    )
    assert token != stored and token not in stored


def test_a_signed_checkin_records_the_collector(client):
    collector, key = _enrolled()

    response = signed_post(client, collector.id, key, CHECKIN_PATH, CHECKIN)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "jobs": []}

    collector.refresh_from_db()
    assert collector.last_check_in is not None
    assert collector.version == "0.1.0"
    assert not collector.tls_inspected
    assert collector.scoped(CheckIn).filter(collector=collector).count() == 1


def test_an_inspected_path_is_recorded(client):
    collector, key = _enrolled()
    body = {**CHECKIN, "tls_inspected": True}
    assert signed_post(client, collector.id, key, CHECKIN_PATH, body).status_code == 200
    collector.refresh_from_db()
    assert collector.tls_inspected
    assert collector.scoped(CheckIn).get().tls_inspected


def test_a_replayed_nonce_is_refused(client):
    collector, key = _enrolled()
    data = json.dumps(CHECKIN).encode()
    auth = signing.sign(key, str(collector.id), "POST", CHECKIN_PATH, data)

    def send():
        return client.post(
            CHECKIN_PATH, data=data, content_type="application/json", HTTP_AUTHORIZATION=auth
        )

    assert send().status_code == 200
    replay = send()
    assert replay.status_code == 401
    assert "already used" in replay.json()["error"]
    assert collector.scoped(CheckIn).count() == 1


def test_a_wrong_body_is_refused(client):
    collector, key = _enrolled()
    signed_for = json.dumps(CHECKIN).encode()
    auth = signing.sign(key, str(collector.id), "POST", CHECKIN_PATH, signed_for)
    tampered = json.dumps({**CHECKIN, "hostname": "evil"})
    response = client.post(
        CHECKIN_PATH, data=tampered, content_type="application/json", HTTP_AUTHORIZATION=auth
    )
    assert response.status_code == 401
    assert not collector.scoped(CheckIn).exists()


def test_a_signature_for_another_path_is_refused(client):
    collector, key = _enrolled()
    data = json.dumps(CHECKIN).encode()
    auth = signing.sign(key, str(collector.id), "POST", ENROLL_PATH, data)
    response = client.post(
        CHECKIN_PATH, data=data, content_type="application/json", HTTP_AUTHORIZATION=auth
    )
    assert response.status_code == 401


@pytest.mark.parametrize("skew", [-signing.CLOCK_WINDOW - 5, signing.CLOCK_WINDOW + 5])
def test_a_timestamp_outside_the_clock_window_is_refused(client, skew):
    collector, key = _enrolled()
    now = timezone.now().timestamp() + skew
    response = signed_post(client, collector.id, key, CHECKIN_PATH, CHECKIN, now=now)
    assert response.status_code == 401
    assert "clock window" in response.json()["error"]


def test_a_slightly_skewed_clock_still_works(client):
    collector, key = _enrolled()
    now = timezone.now().timestamp() - signing.CLOCK_WINDOW + 30
    assert signed_post(client, collector.id, key, CHECKIN_PATH, CHECKIN, now=now).status_code == 200


def test_a_revoked_collector_is_cut_off_at_its_next_request(client):
    collector, key = _enrolled()
    assert signed_post(client, collector.id, key, CHECKIN_PATH, CHECKIN).status_code == 200
    collector.revoke(timezone.now())
    collector.save()
    response = signed_post(client, collector.id, key, CHECKIN_PATH, CHECKIN)
    assert response.status_code == 401


def test_old_nonces_are_pruned(client):
    collector, key = _enrolled()
    collector.scoped(RequestNonce).create(
        collector=collector, nonce="0" * 32, expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert signed_post(client, collector.id, key, CHECKIN_PATH, CHECKIN).status_code == 200
    assert list(collector.scoped(RequestNonce).values_list("nonce", flat=True)) != ["0" * 32]
    assert collector.scoped(RequestNonce).count() == 1


@pytest.mark.parametrize(
    "auth",
    [
        None,
        "Bearer 00000000-0000-0000-0000-000000000000.secret",
        "Cartio-Ed25519 id=not-a-uuid,ts=1,nonce=abcdefgh,sig=AAAA",
        "Cartio-Ed25519 id=00000000-0000-0000-0000-000000000000,ts=x,nonce=abcdefgh,sig=AAAA",
        "Cartio-Ed25519 garbage",
    ],
)
def test_checkin_rejects_bad_credentials(client, auth):
    headers = {"HTTP_AUTHORIZATION": auth} if auth else {}
    response = _post(client, CHECKIN_PATH, CHECKIN, **headers)
    assert response.status_code == 401


def test_a_checkin_with_a_bad_body_is_rejected_after_the_signature(client):
    collector, key = _enrolled()
    response = signed_post(client, collector.id, key, CHECKIN_PATH, {"hostname": "vm01"})
    assert response.status_code == 400


def test_another_collectors_key_is_refused(client):
    first, _ = _new_collector(customer="Acme", name="vm01")
    second, _ = _new_collector(customer="Globex", name="vm01")
    first_key = give_key(first)
    give_key(second)

    response = signed_post(client, second.id, first_key, CHECKIN_PATH, CHECKIN)
    assert response.status_code == 401
    assert not CheckIn.all_customers.exists()


def test_create_collector_command_prints_a_working_token(client, capsys):
    call_command("create_collector", customer="Initech", name="laptop")
    token = capsys.readouterr().out.strip().splitlines()[-1]

    response = _post(client, ENROLL_PATH, _enroll_body(token))
    assert response.status_code == 201


def test_enrollment_tokens_are_safe_on_a_command_line():
    collector = Collector(customer=Customer.objects.create(name="Acme"), name="vm01")
    for _ in range(200):
        token = collector.issue_enrollment_token()
        assert not token.startswith("-")
        assert token.isalnum()
