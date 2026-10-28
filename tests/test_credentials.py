"""Credential store and delivery to collectors (YAN-1653, ADR-0003 Hosted mode).

Sample secrets are assembled at runtime so the repo's secret checks don't flag this file.
"""

import base64
import io
import json

import pytest
from cartio_controller.collectors import vault
from cartio_controller.collectors.credentials import (
    CredentialNotAvailable,
    deliver_credentials,
    update_credential,
)
from cartio_controller.collectors.models import (
    AppendOnlyError,
    AuditEvent,
    Collector,
    Credential,
    Customer,
    CustomerDataKey,
)
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

pytestmark = pytest.mark.django_db

CUSTOMERS = "/api/v1/customers"
PASSWORD = "Pa55-" + "word-for-tests"
COMMUNITY = "comm" + "unity-for-tests"


@pytest.fixture
def api(client):
    user = get_user_model().objects.create_user("engineer", password="x", is_staff=True)
    user.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label="collectors",
            content_type__model__in=["customer", "credential"],
        )
    )
    client.force_login(user)
    return client


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


@pytest.fixture
def globex():
    return Customer.objects.create(name="Globex")


def _url(customer, credential=None):
    base = f"{CUSTOMERS}/{customer.pk}/credentials"
    return f"{base}/{credential}" if credential else base


def _send(client, method, path, body=None):
    return getattr(client, method)(
        path, data=json.dumps(body or {}), content_type="application/json"
    )


def _ssh(client, customer, name="core", password=PASSWORD):
    body = {
        "name": name,
        "kind": "ssh_password",
        "username": "audit",
        "secret": {"password": password},
    }
    response = _send(client, "post", _url(customer), body)
    assert response.status_code == 201, response.json()
    return response.json()


def _collector(customer, enrolled=True):
    collector = Collector.objects.for_customer(customer).create(name=f"vm-{customer.pk}")
    if enrolled:
        collector.enrolled_at = timezone.now()
        collector.save()
    return collector


# The API never shows a secret


def test_secret_is_write_only(api, acme):
    created = _ssh(api, acme)
    assert "secret" not in created
    assert created["username"] == "audit"
    assert PASSWORD not in json.dumps(created)

    for response in (api.get(_url(acme)), api.get(_url(acme, created["id"]))):
        assert response.status_code == 200
        assert PASSWORD not in response.content.decode()
        assert "ciphertext" not in response.content.decode()


def test_secret_can_be_replaced_and_deleted(api, acme):
    created = _ssh(api, acme)
    new_password = PASSWORD + "-2"
    response = _send(
        api, "patch", _url(acme, created["id"]), {"secret": {"password": new_password}}
    )
    assert response.status_code == 200, response.json()
    assert new_password not in response.content.decode()
    assert Credential.objects.for_customer(acme).get().secret() == {"password": new_password}

    # Renaming keeps the secret.
    assert _send(api, "patch", _url(acme, created["id"]), {"name": "edge"}).status_code == 200
    assert Credential.objects.for_customer(acme).get().secret() == {"password": new_password}

    assert api.delete(_url(acme, created["id"])).status_code == 204
    assert not Credential.objects.for_customer(acme).exists()


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"kind": "ssh_password", "username": "a"}, "secret"),
        ({"kind": "ssh_password", "secret": {"password": "x"}}, "username"),
        ({"kind": "ssh_password", "username": "a", "secret": {"token": "x"}}, "secret"),
        ({"kind": "snmp_v2c", "username": "a", "secret": {"community": "x"}}, "username"),
        (
            {"kind": "snmp_v3", "username": "a", "auth_protocol": "sha256", "secret": {}},
            "priv_protocol",
        ),
        ({"kind": "api_token", "auth_protocol": "sha", "secret": {"token": "x"}}, "auth_protocol"),
        ({"kind": "telnet", "secret": {"password": "x"}}, "kind"),
    ],
)
def test_bad_credentials_are_refused(api, acme, body, field):
    response = _send(api, "post", _url(acme), {"name": "x", **body})
    assert response.status_code == 400
    assert field in response.json()
    assert not Credential.objects.for_customer(acme).exists()


def test_every_kind_round_trips(api, acme):
    bodies = [
        {"kind": "ssh_password", "username": "a", "secret": {"password": PASSWORD}},
        {"kind": "ssh_key", "username": "a", "secret": {"private_key": "k" * 40}},
        {"kind": "snmp_v2c", "secret": {"community": COMMUNITY}},
        {
            "kind": "snmp_v3",
            "username": "ro",
            "auth_protocol": "sha256",
            "priv_protocol": "aes256",
            "secret": {"auth_key": PASSWORD, "priv_key": PASSWORD + "p"},
        },
        {"kind": "api_token", "secret": {"token": "t" * 30}},
    ]
    for i, body in enumerate(bodies):
        response = _send(api, "post", _url(acme), {"name": f"c{i}", **body})
        assert response.status_code == 201, response.json()
        stored = Credential.objects.for_customer(acme).get(pk=response.json()["id"])
        assert stored.secret() == body["secret"]


def test_kind_cannot_change(api, acme):
    created = _ssh(api, acme)
    response = _send(api, "patch", _url(acme, created["id"]), {"kind": "api_token"})
    assert response.status_code == 400


def test_on_site_customers_keep_credentials_on_site(api):
    on_site = Customer.objects.create(name="Initech", data_mode=Customer.DataMode.ON_SITE)
    body = {"name": "x", "kind": "snmp_v2c", "secret": {"community": COMMUNITY}}
    assert _send(api, "post", _url(on_site), body).status_code == 400


def test_another_customers_credential_is_not_reachable(api, acme, globex):
    created = _ssh(api, acme)
    assert api.get(_url(globex, created["id"])).status_code == 404
    patch = _send(api, "patch", _url(globex, created["id"]), {"secret": {"password": "x"}})
    assert patch.status_code == 404
    assert api.delete(_url(globex, created["id"])).status_code == 404
    assert Credential.objects.for_customer(acme).get().secret() == {"password": PASSWORD}


def test_writes_need_permission(client, acme):
    client.force_login(get_user_model().objects.create_user("viewer", is_staff=True))
    body = {"name": "x", "kind": "snmp_v2c", "secret": {"community": COMMUNITY}}
    assert _send(client, "post", _url(acme), body).status_code == 403


def test_missing_master_key_is_a_clear_error(api, acme, monkeypatch):
    monkeypatch.delenv("CARTIO_MASTER_KEY")
    body = {"name": "x", "kind": "snmp_v2c", "secret": {"community": COMMUNITY}}
    response = _send(api, "post", _url(acme), body)
    assert response.status_code == 503
    assert "CARTIO_MASTER_KEY_FILE" in response.json()["detail"]
    assert not Credential.all_customers.exists()


# Encryption at rest


def test_database_dump_reveals_no_secret(api, acme, globex):
    _ssh(api, acme)
    _send(
        api,
        "post",
        _url(globex),
        {"name": "snmp", "kind": "snmp_v2c", "secret": {"community": COMMUNITY}},
    )
    out = io.StringIO()
    call_command("dumpdata", "collectors", stdout=out)
    dump = out.getvalue()
    assert dump
    for secret in (PASSWORD, COMMUNITY):
        assert secret not in dump
        assert base64.b64encode(secret.encode()).decode() not in dump
    # The data keys in the dump are wrapped: without the master key they open nothing.
    assert CustomerDataKey.all_customers.count() == 2


def test_customers_have_separate_data_keys(api, acme, globex):
    _ssh(api, acme)
    _ssh(api, globex)
    acme_key = CustomerDataKey.key_for(acme.pk)
    assert acme_key != CustomerDataKey.key_for(globex.pk)
    globex_credential = Credential.objects.for_customer(globex).get()
    with pytest.raises(vault.VaultError):
        vault.decrypt(acme_key, globex_credential.ciphertext, globex_credential._context())


def test_ciphertext_moved_to_another_row_does_not_decrypt(api, acme):
    one = Credential.objects.for_customer(acme).get(pk=_ssh(api, acme, "one")["id"])
    two = Credential.objects.for_customer(acme).get(pk=_ssh(api, acme, "two", PASSWORD + "2")["id"])
    two.ciphertext = one.ciphertext
    with pytest.raises(vault.VaultError):
        two.secret()


def test_wrong_master_key_opens_nothing(api, acme, monkeypatch):
    _ssh(api, acme)
    monkeypatch.setenv(
        "CARTIO_MASTER_KEY", base64.b64encode(b"another key, also 32 bytes long!").decode()
    )
    with pytest.raises(ImproperlyConfigured, match="another master key"):
        Credential.objects.for_customer(acme).get().secret()


def test_master_key_from_file(tmp_path, monkeypatch):
    path = tmp_path / "master.key"
    path.write_text(base64.b64encode(b"k" * 32).decode() + "\n")
    monkeypatch.setenv("CARTIO_MASTER_KEY_FILE", str(path))
    key = vault.master_key()
    assert "k" * 8 not in repr(key)
    wrapped = key.wrap(b"d" * 32, b"ctx")
    assert key.unwrap(wrapped, b"ctx") == b"d" * 32

    path.write_text(base64.b64encode(b"short").decode())
    with pytest.raises(ImproperlyConfigured):
        vault.master_key()


# Delivery to collectors


def test_collector_receives_its_customers_credentials(api, acme):
    created = _ssh(api, acme)
    collector = _collector(acme)
    delivered = deliver_credentials(collector, [created["id"]], job="job-1")
    assert delivered == [
        {
            "id": created["id"],
            "kind": "ssh_password",
            "username": "audit",
            "auth_protocol": "",
            "priv_protocol": "",
            "password": PASSWORD,
        }
    ]
    used = AuditEvent.objects.for_customer(acme).get(action="credential.used")
    assert used.collector == collector
    assert used.detail == {"kind": "ssh_password", "job": "job-1"}


def test_another_customers_collector_cannot_obtain_the_credential(api, acme, globex):
    created = _ssh(api, acme)
    _ssh(api, globex, "theirs")
    globex_collector = _collector(globex)
    with pytest.raises(CredentialNotAvailable):
        deliver_credentials(globex_collector, [created["id"]])
    # Mixing one of its own with another customer's gets nothing either.
    own = Credential.objects.for_customer(globex).get().pk
    with pytest.raises(CredentialNotAvailable):
        deliver_credentials(globex_collector, [own, created["id"]])
    assert not AuditEvent.objects.for_customer(acme).filter(action="credential.used").exists()
    assert not AuditEvent.objects.for_customer(globex).filter(action="credential.used").exists()


def test_unenrolled_collector_gets_nothing(api, acme):
    created = _ssh(api, acme)
    with pytest.raises(CredentialNotAvailable):
        deliver_credentials(_collector(acme, enrolled=False), [created["id"]])


# Audit log


def test_every_change_is_audited_without_the_secret(api, acme):
    created = _ssh(api, acme)
    _send(api, "patch", _url(acme, created["id"]), {"secret": {"password": PASSWORD + "-2"}})
    deliver_credentials(_collector(acme), [created["id"]])
    api.delete(_url(acme, created["id"]))

    events = list(AuditEvent.objects.for_customer(acme).order_by("at", "pk"))
    assert [e.action for e in events] == [
        "credential.created",
        "credential.updated",
        "credential.used",
        "credential.deleted",
    ]
    assert {e.object_id for e in events} == {created["id"]}
    assert [e.actor for e in events] == ["engineer", "engineer", "", "engineer"]
    assert events[1].detail["secret_replaced"] is True
    out = io.StringIO()
    call_command("dumpdata", "collectors.auditevent", stdout=out)
    assert PASSWORD not in out.getvalue()


def test_audit_events_are_append_only(api, acme):
    _ssh(api, acme)
    event = AuditEvent.objects.for_customer(acme).get()
    event.actor = "someone else"
    with pytest.raises(AppendOnlyError):
        event.save()
    with pytest.raises(AppendOnlyError):
        event.delete()
    with pytest.raises(AppendOnlyError):
        AuditEvent.all_customers.update(actor="x")
    with pytest.raises(AppendOnlyError):
        AuditEvent.objects.for_customer(acme).delete()
    assert AuditEvent.all_customers.get().actor == "engineer"


def test_a_failed_write_leaves_no_audit_event(api, acme):
    _ssh(api, acme)
    body = {"name": "core", "kind": "snmp_v2c", "secret": {"community": COMMUNITY}}
    assert _send(api, "post", _url(acme), body).status_code == 400
    assert AuditEvent.objects.for_customer(acme).count() == 1


def test_audit_rows_cannot_be_overwritten_by_insert_or_upsert(api, acme):
    _ssh(api, acme)
    event = AuditEvent.objects.for_customer(acme).get()
    forged = AuditEvent(
        pk=event.pk, customer=acme, action="credential.deleted", object_type="x", object_id="1"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        forged.save()
    upsert = AuditEvent(
        pk=event.pk, customer=acme, action="credential.deleted", object_type="x", object_id="1"
    )
    with pytest.raises(AppendOnlyError):
        AuditEvent.objects.for_customer(acme).bulk_create(
            [upsert], update_conflicts=True, unique_fields=["id"], update_fields=["action"]
        )
    assert AuditEvent.all_customers.get().action == "credential.created"


def test_a_stale_update_keeps_a_newer_secret(api, acme):
    created = _ssh(api, acme)
    stale = Credential.objects.for_customer(acme).get()
    newer = PASSWORD + "-newer"
    _send(api, "patch", _url(acme, created["id"]), {"secret": {"password": newer}})
    update_credential(stale, "someone", name="renamed")
    stored = Credential.objects.for_customer(acme).get()
    assert stored.name == "renamed"
    assert stored.secret() == {"password": newer}


def test_unreadable_key_file_and_rotated_key_are_clear_errors(api, acme, monkeypatch, tmp_path):
    _ssh(api, acme)
    body = {"name": "two", "kind": "snmp_v2c", "secret": {"community": COMMUNITY}}

    monkeypatch.setenv("CARTIO_MASTER_KEY_FILE", str(tmp_path / "missing.key"))
    response = _send(api, "post", _url(acme), body)
    assert response.status_code == 503
    assert "Can't read the master key file" in response.json()["detail"]

    monkeypatch.delenv("CARTIO_MASTER_KEY_FILE")
    other = base64.b64encode(b"another key, also 32 bytes long!").decode()
    monkeypatch.setenv("CARTIO_MASTER_KEY", other)
    response = _send(api, "post", _url(acme), body)
    assert response.status_code == 503
    assert "another master key" in response.json()["detail"]
    assert Credential.objects.for_customer(acme).count() == 1


def test_delivery_refuses_malformed_ids(api, acme):
    _ssh(api, acme)
    with pytest.raises(CredentialNotAvailable):
        deliver_credentials(_collector(acme), ["not-a-uuid"])


def test_an_optional_secret_field_may_be_empty(api, acme):
    body = {
        "name": "key",
        "kind": "ssh_key",
        "username": "a",
        "secret": {"private_key": "k" * 40, "passphrase": ""},
    }
    response = _send(api, "post", _url(acme), body)
    assert response.status_code == 201, response.json()
    assert Credential.objects.for_customer(acme).get().secret() == {"private_key": "k" * 40}
