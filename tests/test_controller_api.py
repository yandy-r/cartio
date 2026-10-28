import json

import pytest
from cartio_controller.collectors.models import CheckIn, Collector, Customer
from cartio_core.protocol import CHECKIN_PATH, ENROLL_PATH, bearer
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def _new_collector(customer="Acme", name="vm01"):
    collector = Collector(customer=Customer.objects.get_or_create(name=customer)[0], name=name)
    token = collector.issue_enrollment_token()
    collector.save()
    return collector, token


def _post(client, path, body, **headers):
    return client.post(path, data=json.dumps(body), content_type="application/json", **headers)


def test_enroll_issues_identity_and_burns_the_token(client):
    collector, token = _new_collector()

    response = _post(client, ENROLL_PATH, {"token": token, "hostname": "vm01"})
    assert response.status_code == 201
    body = response.json()
    assert body["collector_id"] == str(collector.id)
    assert body["customer"] == "Acme"

    collector.refresh_from_db()
    assert collector.enrolled_at is not None
    assert collector.enrollment_token_hash == ""
    assert collector.check_secret(body["secret"])

    again = _post(client, ENROLL_PATH, {"token": token, "hostname": "vm01"})
    assert again.status_code == 403


def test_enroll_rejects_unknown_token_and_bad_json(client):
    assert _post(client, ENROLL_PATH, {"token": "nope", "hostname": "x"}).status_code == 403
    bad = client.post(ENROLL_PATH, data="not json", content_type="application/json")
    assert bad.status_code == 400


def test_secrets_are_stored_hashed():
    collector, token = _new_collector()
    secret = collector.issue_secret()
    collector.save()
    stored = Collector.all_customers.values("enrollment_token_hash", "secret_hash").get(
        pk=collector.pk
    )
    assert token not in stored.values()
    assert secret not in stored.values()


def test_checkin_records_the_collector(client):
    collector, _ = _new_collector()
    secret = collector.issue_secret()
    collector.save()

    response = _post(
        client,
        CHECKIN_PATH,
        {"hostname": "vm01", "version": "0.1.0", "platform": "Linux"},
        HTTP_AUTHORIZATION=bearer(str(collector.id), secret),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "jobs": []}

    collector.refresh_from_db()
    assert collector.last_check_in is not None
    assert collector.version == "0.1.0"
    assert collector.scoped(CheckIn).filter(collector=collector).count() == 1


@pytest.mark.parametrize(
    "auth",
    [
        None,
        "Bearer not-a-uuid.secret",
        "Bearer 00000000-0000-0000-0000-000000000000.secret",
    ],
)
def test_checkin_rejects_bad_credentials(client, auth):
    headers = {"HTTP_AUTHORIZATION": auth} if auth else {}
    body = {"hostname": "vm01", "version": "0.1.0", "platform": "Linux"}
    response = _post(client, CHECKIN_PATH, body, **headers)
    assert response.status_code in (400, 401)


def test_checkin_rejects_another_collectors_secret(client):
    first, _ = _new_collector(customer="Acme", name="vm01")
    second, _ = _new_collector(customer="Globex", name="vm01")
    first_secret = first.issue_secret()
    first.save()
    second.issue_secret()
    second.save()

    response = _post(
        client,
        CHECKIN_PATH,
        {"hostname": "x", "version": "0.1.0", "platform": "Linux"},
        HTTP_AUTHORIZATION=bearer(str(second.id), first_secret),
    )
    assert response.status_code == 401


def test_create_collector_command_prints_a_working_token(client, capsys):
    call_command("create_collector", customer="Initech", name="laptop")
    token = capsys.readouterr().out.strip().splitlines()[-1]

    response = _post(client, ENROLL_PATH, {"token": token, "hostname": "laptop"})
    assert response.status_code == 201


def test_enrollment_tokens_are_safe_on_a_command_line():
    collector = Collector(customer=Customer.objects.create(name="Acme"), name="vm01")
    for _ in range(200):
        token = collector.issue_enrollment_token()
        assert not token.startswith("-")
        assert token.isalnum()
