"""Changing a customer's data mode (YAN-1712, ADR-0002): only through its audited action, and a
move On site deletes the controller's copy only after the on-site store confirmed the export."""

import hashlib
import json
import uuid

import pytest
from cartio_collector.store import FolderStore, PostgresStore, StoreError
from cartio_controller.collectors import data_mode, db_guards, jobs
from cartio_controller.collectors.models import (
    AssessmentRun,
    AuditEvent,
    CheckResult,
    CollectionRun,
    ConfigSnapshot,
    Credential,
    Customer,
    CustomerDataKey,
    CustomerOwned,
    DataModeLocked,
    Device,
    Engagement,
    Finding,
    Interface,
    Job,
    JobChunk,
    RawOutput,
    Source,
)
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import DatabaseError, IntegrityError, connection, transaction

from tests.test_collect_job import HOST
from tests.test_job_channel import Enrolled
from tests.test_on_site_store import Capture, _worker, postgres_dsn  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db

PASSWORD = "n0t-a-real-pass"


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _network(customer):
    """A little of everything the controller holds for a Hosted customer."""
    engagement = Engagement.objects.for_customer(customer).create(name="Audit")
    source = Source.objects.for_customer(customer).create(
        engagement=engagement, kind="upload", name="lab.zip"
    )
    device = Device.objects.for_customer(customer).create(
        engagement=engagement, source=source, name="floor-sw1", platform="ios", management_ip=HOST
    )
    Interface.objects.for_customer(customer).create(device=device, name="Gi0/1")
    RawOutput.objects.for_customer(customer).create(
        source=source, device=device, platform="ios", command="show version", output="Cisco IOS"
    )
    ConfigSnapshot.objects.for_customer(customer).create(
        device=device, source=source, platform="ios", content="hostname floor-sw1\n"
    )
    run = AssessmentRun.objects.for_customer(customer).create(
        engagement=engagement, check_ids=["firmware-drift"]
    )
    CheckResult.objects.for_customer(customer).create(
        run=run, device=device, check_id="firmware-drift", severity="medium", status="pass"
    )
    Finding.objects.for_customer(customer).create(
        engagement=engagement, kind="manual", title="Old firmware", severity="low"
    )
    credential = Credential(customer=customer, name="ro-ssh", kind="ssh_password", username="x")
    credential.set_secret({"password": PASSWORD})
    credential.save()
    return engagement


def _network_rows(customer):
    return {
        model._meta.model_name: model.objects.for_customer(customer).count()
        for model in data_mode.NETWORK_MODELS + data_mode.SECRET_MODELS
    }


def _actions(customer):
    events = AuditEvent.objects.for_customer(customer).order_by("at", "pk")
    return [e.action for e in events if e.action.startswith("customer.")]


# Only through the action


def test_data_mode_never_changes_through_a_plain_save_or_update(acme, client):
    acme.data_mode = Customer.DataMode.ON_SITE
    with pytest.raises(DataModeLocked):
        acme.save()
    with pytest.raises(DataModeLocked):
        Customer.objects.filter(pk=acme.pk).update(data_mode="on_site")
    loaded = Customer.objects.get(pk=acme.pk)
    loaded.data_mode = Customer.DataMode.ON_SITE
    with pytest.raises(DataModeLocked):
        Customer.objects.bulk_update([loaded], ["data_mode"])
    # Other fields still save, and a customer is still created in either mode.
    loaded.data_mode = Customer.DataMode.HOSTED
    loaded.notes = "pilot"
    loaded.save()
    Customer.objects.create(name="Clinic", data_mode="on_site")
    assert Customer.objects.get(pk=acme.pk).data_mode == "hosted"


def test_an_export_job_cant_be_queued_through_the_jobs_api(acme, client):
    vm = Enrolled(acme)
    client.force_login(_user("add_job", "view_job"))
    response = client.post(
        f"/api/v1/customers/{acme.pk}/jobs",
        {"collector": str(vm.collector.pk), "type": "export"},
        content_type="application/json",
    )
    assert response.status_code == 400 and "data mode" in response.content.decode()


# Hosted to On site


def test_moving_on_site_exports_then_deletes_the_controllers_copy(acme, client, tmp_path):
    engagement = _network(acme)
    vm = Enrolled(acme)
    health = jobs.queue_job(vm.collector, "health", "engineer")
    discover = jobs.queue_job(vm.collector, "discover", "engineer")
    for job in (health, discover):
        jobs.store_chunk(job, 0, b"collector output")
    CustomerDataKey.key_for(acme.pk)
    store = FolderStore(tmp_path / "share")
    store.root.mkdir()
    expected = data_mode.export_bytes(acme)

    job = data_mode.start_on_site(acme, vm.collector, "engineer", live_view_allowed=True)
    assert job.params["sha256"] == hashlib.sha256(expected).hexdigest()
    # Nothing changes until the store confirms it.
    assert Customer.objects.get(pk=acme.pk).data_mode == "hosted"
    Job.objects.for_customer(acme).filter(pk__in=[health.pk, discover.pk]).update(
        status="cancelled"
    )

    assert _worker(vm, Capture(client), tmp_path, None, store).poll_once(0) == 1

    job.refresh_from_db()
    assert job.status == "done", job.run.result.error_summary
    acme.refresh_from_db()
    assert acme.data_mode == "on_site" and acme.live_view_allowed
    # In the store, whole: the bytes the controller built, under the same checksum.
    folder = store.root / "exports" / str(job.pk)
    assert (folder / "export.json").read_bytes() == expected
    assert (folder / "export.sha256").read_text().split()[0] == job.params["sha256"]
    stored = json.loads(expected)
    assert stored["tables"]["device"][0]["name"] == "floor-sw1"
    assert stored["tables"]["checkresult"][0]["status"] == "pass"
    assert PASSWORD not in expected.decode() and "credential" not in stored["tables"]
    # Gone from the controller: network data, secrets and collected results.
    assert not any(_network_rows(acme).values())
    chunks = JobChunk.objects.for_customer(acme)
    assert list(chunks.values_list("job__type", flat=True).order_by("job__type")) == [
        "export",
        "health",
    ]
    # Kept: the engagement, the runs and the audit log, which say what happened.
    assert Engagement.objects.for_customer(acme).get() == engagement
    assert CollectionRun.objects.for_customer(acme).count() == 3
    assert _actions(acme) == [
        "customer.data_mode_change_started",
        "customer.data_mode_changed",
    ]
    changed = AuditEvent.objects.for_customer(acme).get(action="customer.data_mode_changed")
    assert changed.actor == "engineer" and changed.collector == vm.collector
    assert changed.detail["from"] == "hosted" and changed.detail["to"] == "on_site"
    assert changed.detail["sha256"] == job.params["sha256"]
    assert changed.detail["deleted"]["device"] == 1 and changed.detail["deleted"]["credential"] == 1
    # And it stays that way: an On site customer's network data is refused.
    with pytest.raises(Exception, match="On site"):
        Finding.objects.for_customer(acme).create(engagement=engagement, kind="manual")


def test_a_store_that_fails_keeps_the_controllers_copy_and_the_change_can_be_retried(
    acme, client, tmp_path
):
    _network(acme)
    before = _network_rows(acme)
    vm = Enrolled(acme)
    broken = tmp_path / "unmounted"
    broken.write_text("a file, not a folder")

    failed = data_mode.start_on_site(acme, vm.collector, "engineer")
    _worker(vm, Capture(client), tmp_path / "1", None, FolderStore(broken)).poll_once(0)

    failed.refresh_from_db()
    assert failed.status == "failed"
    summary = failed.run.result.error_summary
    assert "Nothing was stored" in summary and "kept its copy" in summary
    assert Customer.objects.get(pk=acme.pk).data_mode == "hosted"
    assert _network_rows(acme) == before
    assert _actions(acme) == [
        "customer.data_mode_change_started",
        "customer.data_mode_change_failed",
    ]

    # Started again with a store that works.
    store = FolderStore(tmp_path / "share")
    store.root.mkdir()
    retried = data_mode.start_on_site(acme, vm.collector, "engineer")
    _worker(vm, Capture(client), tmp_path / "2", None, store).poll_once(0)
    retried.refresh_from_db()
    assert retried.status == "done"
    assert Customer.objects.get(pk=acme.pk).data_mode == "on_site"
    assert not any(_network_rows(acme).values())
    assert _actions(acme)[-1] == "customer.data_mode_changed"


def test_data_that_changed_after_the_export_keeps_the_controllers_copy(acme, client, tmp_path):
    engagement = _network(acme)
    vm = Enrolled(acme)
    store = FolderStore(tmp_path / "share")
    store.root.mkdir()
    job = data_mode.start_on_site(acme, vm.collector, "engineer")
    # A finding added after the export was made, but before the collector fetched it.
    Finding.objects.for_customer(acme).create(engagement=engagement, kind="manual", title="New")
    before = _network_rows(acme)

    _worker(vm, Capture(client), tmp_path, None, store).poll_once(0)

    job.refresh_from_db()
    assert job.status == "failed" and "checksum doesn't match" in job.run.result.error_summary
    assert Customer.objects.get(pk=acme.pk).data_mode == "hosted"
    assert _network_rows(acme) == before


def test_a_receipt_that_doesnt_match_deletes_nothing(acme, client):
    _network(acme)
    before = _network_rows(acme)
    vm = Enrolled(acme)
    job = data_mode.start_on_site(acme, vm.collector, "engineer")
    vm.poll(client)
    receipt = json.dumps({"sha256": "0" * 64}).encode()
    assert vm.chunk(client, job.pk, 0, receipt).status_code == 200
    assert vm.result(client, job.pk, [receipt]).status_code == 200

    job.refresh_from_db()
    assert job.status == "failed"
    assert "store's checksum doesn't match" in job.run.result.error_summary
    assert Customer.objects.get(pk=acme.pk).data_mode == "hosted"
    assert _network_rows(acme) == before
    failed = AuditEvent.objects.for_customer(acme).get(action="customer.data_mode_change_failed")
    assert failed.detail["from"] == "hosted" and failed.detail["to"] == "on_site"


def test_only_one_move_at_a_time_and_only_through_the_customers_own_collector(acme):
    vm = Enrolled(acme)
    other = Enrolled(Customer.objects.create(name="Globex"))
    with pytest.raises(data_mode.DataModeError, match="No such collector"):
        data_mode.start_on_site(acme, other.collector, "engineer")
    data_mode.start_on_site(acme, vm.collector, "engineer")
    with pytest.raises(data_mode.DataModeError, match="already under way"):
        data_mode.start_on_site(acme, vm.collector, "engineer")


def test_the_export_is_only_for_the_export_jobs_own_collector(acme, client):
    _network(acme)
    vm = Enrolled(acme)
    job = data_mode.start_on_site(acme, vm.collector, "engineer")
    other = Enrolled(acme, name="vm02")
    path = f"/api/v1/jobs/{job.pk}/export"
    assert other.post(client, path, {}).status_code == 404
    response = vm.post(client, path, {})
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == job.params["sha256"]
    health = jobs.queue_job(vm.collector, "health", "engineer")
    assert vm.post(client, f"/api/v1/jobs/{health.pk}/export", {}).status_code == 404


# On site to Hosted


def test_moving_back_to_hosted_moves_nothing_and_names_who_approved(client):
    clinic = Customer.objects.create(name="Clinic", data_mode="on_site", live_view_allowed=True)
    with pytest.raises(data_mode.DataModeError, match="approved"):
        data_mode.move_hosted(clinic, "engineer", "  ")
    data_mode.move_hosted(clinic, "engineer", "Dana Lee, IT director")
    clinic.refresh_from_db()
    assert clinic.data_mode == "hosted" and not clinic.live_view_allowed
    event = AuditEvent.objects.for_customer(clinic).get(action="customer.data_mode_changed")
    assert event.actor == "engineer"
    assert event.detail["from"] == "on_site" and event.detail["to"] == "hosted"
    assert event.detail["confirmed_by"] == "Dana Lee, IT director"
    with pytest.raises(data_mode.DataModeError, match="already Hosted"):
        data_mode.move_hosted(clinic, "engineer", "Dana Lee")


# The API action


def _user(*codenames):
    user = get_user_model().objects.create_user(
        f"engineer-{uuid.uuid4().hex[:6]}", password="x", is_staff=True
    )
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


def test_the_api_action_needs_its_own_permission(acme, client):
    vm = Enrolled(acme)
    path = f"/api/v1/customers/{acme.pk}/data-mode"
    body = {"to": "on_site", "collector": str(vm.collector.pk)}
    client.force_login(_user("change_customer", "view_customer"))
    assert client.post(path, body, content_type="application/json").status_code == 403
    assert not Job.objects.for_customer(acme).exists()

    user = _user("change_data_mode")
    client.force_login(user)
    response = client.post(path, body, content_type="application/json")
    assert response.status_code == 202, response.content
    assert response.json()["type"] == "export"
    started = AuditEvent.objects.for_customer(acme).get(action="customer.data_mode_change_started")
    assert started.actor == user.username

    response = client.post(path, {"to": "hosted"}, content_type="application/json")
    assert response.status_code == 400 and "confirmed_by" in response.json()
    response = client.post(
        path, {"to": "on_site", "collector": str(uuid.uuid4())}, content_type="application/json"
    )
    assert response.status_code == 400


def test_the_api_moves_an_on_site_customer_back_to_hosted(client):
    clinic = Customer.objects.create(name="Clinic", data_mode="on_site")
    client.force_login(_user("change_data_mode"))
    response = client.post(
        f"/api/v1/customers/{clinic.pk}/data-mode",
        {"to": "hosted", "confirmed_by": "Dana Lee"},
        content_type="application/json",
    )
    assert response.status_code == 200 and response.json()["data_mode"] == "hosted"


# What the purge covers


def test_every_kind_of_network_data_is_exported_or_deleted():
    covered = set(data_mode.NETWORK_MODELS + data_mode.SECRET_MODELS)
    flagged = {
        model
        for model in apps.get_models()
        if issubclass(model, CustomerOwned) and model.network_data is True
    }
    assert flagged == covered
    append_only = {m._meta.db_table for m in data_mode.NETWORK_MODELS if m.append_only}
    assert append_only == set(db_guards.PURGEABLE_TABLES)


@pytest.fixture
def postgres():
    if connection.vendor != "postgresql":
        pytest.skip("needs PostgreSQL")


def _purge_rows(table, customer, event):
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {db_guards.PURGE_FUNCTION}(%s, %s, %s)", [table, customer.pk, event.pk]
        )


def test_the_database_deletes_append_only_rows_only_for_a_customer_that_moved(postgres, acme):
    _network(acme)
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM collectors_rawoutput WHERE customer_id = %s", [acme.pk])
    other = AuditEvent.objects.for_customer(acme).create(
        action="record.created", object_type="customer", object_id=acme.pk
    )
    # Still Hosted, or not the change's own event, or not network data: refused.
    with pytest.raises(IntegrityError):
        _purge_rows("collectors_rawoutput", acme, other)
    clinic = Customer.objects.create(name="Clinic", data_mode="on_site")
    event = AuditEvent.objects.for_customer(clinic).create(
        action="customer.data_mode_changed", object_type="customer", object_id=clinic.pk
    )
    with pytest.raises(IntegrityError):
        _purge_rows("collectors_rawoutput", acme, event)
    with pytest.raises(IntegrityError):
        _purge_rows("collectors_auditevent", clinic, event)
    assert RawOutput.objects.for_customer(acme).count() == 1


# The on-site stores


def test_the_folder_store_writes_an_export_once(tmp_path):
    store = FolderStore(tmp_path)
    job = str(uuid.uuid4())
    digest = store.write_export(job, {"tables": {"device": [{"name": "a"}]}})
    assert store.write_export(job, {"tables": {"device": [{"name": "a"}]}}) == digest
    with pytest.raises(StoreError, match="another export"):
        store.write_export(job, {"tables": {}})


def test_the_postgres_store_writes_an_export_once(postgres_dsn):  # noqa: F811
    store = PostgresStore(postgres_dsn)
    job = str(uuid.uuid4())
    doc = {"tables": {"device": [{"name": "a", "mtu": 1500, "enabled": None}]}}
    digest = store.write_export(job, doc)
    assert store.write_export(job, doc) == digest
    with pytest.raises(StoreError, match="another export"):
        store.write_export(job, {"tables": {}})
