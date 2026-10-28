"""Collectors, jobs and audit (YAN-1736, DESIGN.md section 4)."""

import json
import re
from datetime import timedelta

import pytest
from cartio_controller.collectors.credentials import CredentialNotAvailable, deliver_credentials
from cartio_controller.collectors.models import (
    AuditEvent,
    CollectionRun,
    Collector,
    Customer,
    Site,
)
from cartio_controller.collectors.runs import RunNotAllowed, start_run
from cartio_core.protocol import CHECKIN_PATH, ENROLL_PATH, bearer
from django.contrib.auth.models import Permission
from django.utils import timezone

from .test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db


def _grant(user, *codenames):
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))


@pytest.fixture
def engineer(client):
    user = _engineer()
    _grant(user, "add_collector", "change_collector", "view_collectionrun", "view_auditevent")
    _signed_in(client, user)
    return user


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


@pytest.fixture
def globex():
    return Customer.objects.create(name="Globex")


def _post_json(client, path, body, **headers):
    return client.post(path, data=json.dumps(body), content_type="application/json", **headers)


def _enrolled(customer, name="vm01"):
    collector = Collector(customer=customer, name=name)
    token = collector.issue_enrollment_token()
    collector.save()
    return collector, token


def test_collectors_tab_shows_each_collectors_state(client, engineer, acme):
    hq = Site.objects.for_customer(acme).create(name="HQ")
    now = timezone.now()
    collectors = Collector.objects.for_customer(acme)
    collectors.create(
        name="acme-hq-vm",
        site=hq,
        enrolled_at=now,
        last_check_in=now,
        hostname="hq-host",
        version="0.4.0",
    )
    collectors.create(name="acme-east-vm", enrolled_at=now, last_check_in=now - timedelta(days=2))
    collectors.create(name="acme-west-vm")
    collectors.create(name="acme-old-vm", enrolled_at=now, revoked_at=now)

    page = client.get(f"/customers/{acme.pk}/collectors").content.decode()
    for text in (
        "Collectors &amp; jobs",
        "acme-hq-vm",
        "HQ · hq-host",
        "0.4.0",
        "Online",
        "Offline",
        "acme-east-vm is offline.",
        "Not enrolled",
        "Revoked",
        "Certificate",
        "Queue",
    ):
        assert text in page
    # Tabs, and the sidebar's links to the collectors and audit tabs.
    assert f'href="/customers/{acme.pk}/jobs"' in page
    assert f'href="/customers/{acme.pk}/audit"' in page


def test_add_a_collector_shows_its_token_once(client, engineer, acme):
    hq = Site.objects.for_customer(acme).create(name="HQ")
    url = f"/customers/{acme.pk}/collectors"
    assert "Create and show token" in client.get(url + "?add=1").content.decode()

    response = client.post(url, {"action": "add", "name": "acme-hq-vm", "site": str(hq.pk)})
    assert response.status_code == 201
    page = response.content.decode()
    token = re.search(r"--token ([0-9a-f]{64})", page).group(1)
    assert "uv run cartio-collector enroll --controller http://testserver --token" in page
    assert "Waiting for acme-hq-vm to enroll" in page

    collector = Collector.objects.for_customer(acme).get(name="acme-hq-vm")
    assert collector.site == hq
    assert Collector.for_enrollment_token(token) == collector
    # Only the hash is stored, and the page doesn't show the token again.
    assert token not in client.get(url).content.decode()
    event = AuditEvent.objects.for_customer(acme).get(action=AuditEvent.Action.COLLECTOR_CREATED)
    assert event.actor == engineer.get_username()
    assert event.detail == {"site": "HQ", "via": "collectors"}
    assert token not in json.dumps(event.detail)

    # The token enrolls the collector, and the waiting panel says so.
    assert _post_json(client, ENROLL_PATH, {"token": token, "hostname": "hq"}).status_code == 201
    status = client.get(f"{url}/{collector.pk}/status").content.decode()
    assert "acme-hq-vm enrolled" in status and "hx-trigger" not in status


def test_add_refuses_bad_input_and_another_customers_site(client, engineer, acme, globex):
    url = f"/customers/{acme.pk}/collectors"
    Collector.objects.for_customer(acme).create(name="vm01")
    theirs = Site.objects.for_customer(globex).create(name="Their HQ")

    for form, error in (
        ({"name": ""}, "Give the collector a name."),
        ({"name": "VM01"}, "already has a collector with this name"),
        ({"name": "x" * 201}, "Use at most 200 characters."),
        ({"name": "vm02", "site": str(theirs.pk)}, "Pick one of this customer&#x27;s sites."),
    ):
        response = client.post(url, {"action": "add", **form})
        assert response.status_code == 400
        assert error in response.content.decode()
    assert Collector.objects.for_customer(acme).count() == 1
    assert not Collector.objects.for_customer(globex).exists()


def test_adding_and_revoking_need_permissions(client, acme):
    _signed_in(client, _engineer())
    collector, _ = _enrolled(acme)
    url = f"/customers/{acme.pk}/collectors"

    page = client.get(url + f"?add=1&revoke={collector.pk}").content.decode()
    assert "Create and show token" not in page and "Add collector" not in page
    assert "Revoke" not in page
    assert client.post(url, {"action": "add", "name": "vm02"}).status_code == 403
    response = client.post(url, {"action": "revoke", "collector": str(collector.pk)})
    assert response.status_code == 403
    collector.refresh_from_db()
    assert collector.revoked_at is None


def test_revoke_asks_first_then_stops_the_collector(client, engineer, acme):
    collector, token = _enrolled(acme)
    enrolled = _post_json(client, ENROLL_PATH, {"token": token, "hostname": "vm01"}).json()
    auth = bearer(enrolled["collector_id"], enrolled["secret"])
    checkin = {"hostname": "vm01", "version": "0.4.0", "platform": "linux"}
    assert _post_json(client, CHECKIN_PATH, checkin, HTTP_AUTHORIZATION=auth).status_code == 200

    url = f"/customers/{acme.pk}/collectors"
    stale = Collector.objects.for_customer(acme).get(pk=collector.pk)
    page = client.get(f"{url}?revoke={collector.pk}").content.decode()
    assert "Revoke vm01? Its secret stops working at once" in page
    assert not AuditEvent.objects.for_customer(acme).filter(action="collector.revoked").exists()

    response = client.post(url, {"action": "revoke", "collector": str(collector.pk)})
    assert response.status_code == 302 and response.url == url
    page = client.get(url).content.decode()
    assert "vm01 revoked · logged in the audit log" in page

    collector.refresh_from_db()
    assert collector.revoked_at is not None
    event = AuditEvent.objects.for_customer(acme).get(action=AuditEvent.Action.COLLECTOR_REVOKED)
    assert event.actor == engineer.get_username()
    assert event.collector == collector and event.detail == {"was_enrolled": True}
    # A revoked collector can't check in or start a run any more.
    assert _post_json(client, CHECKIN_PATH, checkin, HTTP_AUTHORIZATION=auth).status_code == 401
    # Even through a collector object loaded before the revoke.
    with pytest.raises(RunNotAllowed):
        start_run(stale, "discovery")
    with pytest.raises(CredentialNotAvailable):
        deliver_credentials(stale, [])
    # Revoking twice is a no-op, and logs nothing more.
    client.post(url, {"action": "revoke", "collector": str(collector.pk)})
    assert AuditEvent.objects.for_customer(acme).filter(action="collector.revoked").count() == 1


def test_cancelling_an_enrollment_burns_the_token(client, engineer, acme):
    collector, token = _enrolled(acme)
    page = client.get(f"/customers/{acme.pk}/collectors?revoke={collector.pk}").content.decode()
    assert "Cancel vm01's enrollment?" in page
    client.post(
        f"/customers/{acme.pk}/collectors", {"action": "revoke", "collector": str(collector.pk)}
    )
    assert _post_json(client, ENROLL_PATH, {"token": token, "hostname": "vm01"}).status_code == 403


def test_another_customers_collectors_are_out_of_reach(client, engineer, acme, globex):
    theirs, _ = _enrolled(globex, "globex-vm")
    CollectionRun.objects.for_customer(globex).create(collector=theirs, job_type="discovery")
    AuditEvent.objects.for_customer(globex).create(
        action=AuditEvent.Action.COLLECTOR_CREATED,
        object_type="collector",
        object_id=str(theirs.pk),
        object_name="globex-vm",
    )
    url = f"/customers/{acme.pk}/collectors"

    for page_url in (url, f"{url}?revoke={theirs.pk}", f"/customers/{acme.pk}/jobs"):
        page = client.get(page_url).content.decode()
        assert "globex-vm" not in page
    page = client.get(f"/customers/{acme.pk}/audit").content.decode()
    assert "globex-vm" not in page
    assert client.get(f"{url}/{theirs.pk}/status").status_code == 404

    response = client.post(url, {"action": "revoke", "collector": str(theirs.pk)})
    assert response.status_code == 302
    theirs.refresh_from_db()
    assert theirs.revoked_at is None
    assert not AuditEvent.objects.for_customer(acme).exists()
    # A garbled id is ignored, not an error.
    assert client.post(url, {"action": "revoke", "collector": "nope"}).status_code == 302
    assert client.get(f"{url}?revoke=nope").status_code == 200


def test_jobs_tab_shows_state_and_progress(client, engineer, acme):
    collector, _ = _enrolled(acme)
    collector.enrolled_at = timezone.now()
    collector.save()
    start_run(collector, "discovery", started_by="engineer")

    page = client.get(f"/customers/{acme.pk}/jobs").content.decode()
    for text in ("Discovery", "vm01", "Running", "Waiting for results", "Run health check"):
        assert text in page


def test_audit_tab_filters_by_kind(client, engineer, acme):
    def log(action, name):
        AuditEvent.objects.for_customer(acme).create(
            action=action, object_type="collector", object_id="1", object_name=name, actor="dana"
        )

    log(AuditEvent.Action.COLLECTOR_CREATED, "made-vm")
    log(AuditEvent.Action.CREDENTIAL_UPDATED, "ro-audit")
    log(AuditEvent.Action.RUN_STARTED, "a-run")
    url = f"/customers/{acme.pk}/audit"

    page = client.get(url).content.decode()
    for text in ("made-vm", "ro-audit", "a-run", "append-only", "Credential updated"):
        assert text in page
    page = client.get(url + "?kind=credentials").content.decode()
    assert "ro-audit" in page and "made-vm" not in page and "a-run" not in page
    page = client.get(url + "?kind=jobs").content.decode()
    assert "a-run" in page and "ro-audit" not in page
    # An unknown kind shows everything.
    assert "made-vm" in client.get(url + "?kind=nope").content.decode()


def test_audit_tab_and_export_need_the_audit_permission(client, acme):
    user = _engineer()
    _signed_in(client, user)
    assert client.get(f"/customers/{acme.pk}/audit").status_code == 403
    assert client.post(f"/customers/{acme.pk}/audit/export").status_code == 403
    assert (
        f'href="/customers/{acme.pk}/audit"'
        not in client.get(f"/customers/{acme.pk}/collectors").content.decode()
    )

    _grant(user, "view_auditevent")
    response = client.post(f"/customers/{acme.pk}/audit/export")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    body = b"".join(response.streaming_content).decode()
    assert "audit.exported" in body
    assert AuditEvent.objects.for_customer(acme).get().actor == user.get_username()


def test_inside_access_only_customers_still_show_collector_health(client, engineer):
    plant = Customer.objects.create(name="Plant", data_mode=Customer.DataMode.ON_SITE)
    Collector.objects.for_customer(plant).create(name="plant-vm")
    page = client.get(f"/customers/{plant.pk}/collectors").content.decode()
    assert "plant-vm" in page and "inside access only" in page
