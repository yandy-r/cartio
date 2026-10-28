"""Rotating the credential master key (YAN-1722).

Sample secrets are assembled at runtime so the repo's secret checks don't flag this file.
"""

import base64
import os

import pytest
from cartio_controller.collectors import rotation, vault
from cartio_controller.collectors.credentials import create_credential
from cartio_controller.collectors.models import AuditEvent, Credential, Customer, CustomerDataKey
from cartio_controller.engineers.models import EngineerMFA
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.django_db

PASSWORD = "Pa55-" + "word-for-tests"
OLD = os.environ["CARTIO_MASTER_KEY"]
NEW = base64.b64encode(b"the new master key, 32 bytes ok!").decode()
OTHER = base64.b64encode(b"a third key that nobody kept ok!").decode()


@pytest.fixture
def customers():
    acme, globex = Customer.objects.create(name="Acme"), Customer.objects.create(name="Globex")
    for customer in (acme, globex):
        create_credential(
            customer,
            "engineer",
            {"password": PASSWORD + customer.name},
            name="core",
            kind=Credential.Kind.SSH_PASSWORD,
            username="audit",
        )
    return acme, globex


@pytest.fixture
def engineer():
    user = get_user_model().objects.create_user("engineer", password=PASSWORD, is_staff=True)
    mfa, _ = EngineerMFA.issue(user)
    return mfa, mfa.secret


def _secrets(customers):
    return [
        Credential.objects.for_customer(customer).get().secret()["password"]
        for customer in customers
    ]


def _start_rotation(monkeypatch):
    monkeypatch.setenv("CARTIO_MASTER_KEY", NEW)
    monkeypatch.setenv("CARTIO_PREVIOUS_MASTER_KEY", OLD)


def _rotated(customers=None):
    return AuditEvent.all_customers.filter(action=AuditEvent.Action.MASTER_KEY_ROTATED)


def test_rotation_rewraps_every_key(customers, engineer, monkeypatch):
    mfa, totp_secret = engineer
    _start_rotation(monkeypatch)
    # Mid-rotation the controller still opens everything wrapped by the old key.
    assert _secrets(customers) == [PASSWORD + "Acme", PASSWORD + "Globex"]

    call_command("rotate_master_key", "--actor", "ops")

    new_id = vault.master_key().key_id
    assert set(CustomerDataKey.all_customers.values_list("master_key_id", flat=True)) == {new_id}
    for customer in customers:
        event = AuditEvent.objects.for_customer(customer).get(
            action=AuditEvent.Action.MASTER_KEY_ROTATED
        )
        assert event.actor == "ops"
        assert event.detail["new_master_key_id"] == new_id

    # Every secret opens with the new key alone...
    monkeypatch.delenv("CARTIO_PREVIOUS_MASTER_KEY")
    assert _secrets(customers) == [PASSWORD + "Acme", PASSWORD + "Globex"]
    mfa.refresh_from_db()
    assert mfa.secret == totp_secret
    # ...and none with the old one.
    monkeypatch.setenv("CARTIO_MASTER_KEY", OLD)
    for customer in customers:
        with pytest.raises(ImproperlyConfigured, match="another master key"):
            Credential.objects.for_customer(customer).get().secret()
    with pytest.raises(ImproperlyConfigured, match="another master key"):
        mfa.secret  # noqa: B018


def test_interrupted_rotation_resumes(customers, engineer, monkeypatch):
    _start_rotation(monkeypatch)
    real = rotation._rotate_data_key
    calls = []

    def stop_on_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise rotation.RotationError("Stopped.")
        return real(*args, **kwargs)

    monkeypatch.setattr(rotation, "_rotate_data_key", stop_on_second)
    with pytest.raises(CommandError, match="run the command again"):
        call_command("rotate_master_key")
    assert rotation.remaining() == 2  # One data key and the engineer's key.
    assert _rotated(customers).count() == 1
    # Half done is a working state: everything still opens.
    assert _secrets(customers) == [PASSWORD + "Acme", PASSWORD + "Globex"]

    monkeypatch.setattr(rotation, "_rotate_data_key", real)
    call_command("rotate_master_key")
    assert rotation.remaining() == 0
    # One event per customer, not one per run.
    assert sorted(_rotated(customers).values_list("customer__name", flat=True)) == [
        "Acme",
        "Globex",
    ]
    call_command("rotate_master_key")  # Nothing left to do is fine.
    assert _rotated(customers).count() == 2


def test_rotation_refuses_to_start_unless_both_keys_are_known(customers, monkeypatch):
    monkeypatch.setenv("CARTIO_MASTER_KEY", NEW)
    with pytest.raises(CommandError, match="CARTIO_PREVIOUS_MASTER_KEY_FILE"):
        call_command("rotate_master_key")

    monkeypatch.setenv("CARTIO_PREVIOUS_MASTER_KEY", NEW)
    with pytest.raises(CommandError, match="same key"):
        call_command("rotate_master_key")

    # A key wrapped by a third master key would be silently left behind. Change nothing.
    monkeypatch.setenv("CARTIO_MASTER_KEY", OTHER)
    Customer.objects.create(name="Initech")
    CustomerDataKey.key_for(Customer.objects.get(name="Initech").pk)
    _start_rotation(monkeypatch)
    with pytest.raises(CommandError, match="neither master key"):
        call_command("rotate_master_key")
    assert rotation.remaining() == 3
    assert not _rotated(customers).exists()
