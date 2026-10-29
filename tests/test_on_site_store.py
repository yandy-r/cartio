"""On site mode (YAN-1670, ADR-0002, ADR-0003): the collector keeps its own credentials, targets
and masking key, writes what it collects to the customer's store (a folder or PostgreSQL), and
the controller gets collector health and job status only."""

import base64
import json
import os
import sys
import uuid

import httpx
import pytest
from cartio_collector import cli
from cartio_collector import jobs as collector_jobs
from cartio_collector.client import Session
from cartio_collector.config import CollectorConfig
from cartio_collector.site import MASKING_KEY_VERSION, Site, SiteError
from cartio_collector.store import FolderStore, PostgresStore, StoreError, canonical
from cartio_controller.collectors import jobs
from cartio_controller.collectors.models import (
    Credential,
    Customer,
    CustomerDataKey,
    CustomerOwned,
    Engagement,
    Job,
    OnSiteDataError,
)
from cartio_core.protocol import CollectedDevice, CollectFailure, CollectResult
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management import CommandError, call_command
from django.db import connection

from tests import test_collect_job
from tests.test_collect_job import DOWN, ENABLE, HOST
from tests.test_job_channel import Enrolled

pytestmark = pytest.mark.django_db

PASSWORD = "n0t-a-real-pass"
# SSH that reaches every host but DOWN.
devices = test_collect_job.devices


@pytest.fixture
def site(tmp_path):
    site = Site.for_config(tmp_path / "collector.json")
    site.set_credential("ro-ssh", "ssh_password", "cartio-ro", {"password": PASSWORD})
    return site


def _result(host=HOST, config="hostname floor-sw1\n"):
    device = CollectedDevice(host, "ios", {"show version": "Cisco IOS"}, config)
    return CollectResult(MASKING_KEY_VERSION, [device], [CollectFailure(DOWN, "TimeoutError")])


# The collector's own secrets


def test_on_site_secrets_are_encrypted_and_never_listed(site):
    site.add_target(HOST, "ios", "ro-ssh")
    raw = site.paths.secrets.read_bytes()
    assert PASSWORD.encode() not in raw and HOST.encode() not in raw
    assert site.credential_list() == [
        {"name": "ro-ssh", "kind": "ssh_password", "username": "cartio-ro"}
    ]
    targets, credentials, key = site.for_collect()
    assert targets[0]["host"] == HOST and credentials["ro-ssh"]["password"] == PASSWORD
    assert key.version == MASKING_KEY_VERSION
    # Made once, then the same key every job.
    assert site.for_collect()[2] == key
    audit = site.paths.audit.read_text()
    assert PASSWORD not in audit and key.secret.hex() not in audit
    assert [json.loads(line)["action"] for line in audit.splitlines()] == [
        "credential.created",
        "target.set",
        "masking_key.created",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_on_site_files_are_readable_by_their_owner_only(site):
    for path in (site.paths.secrets, site.paths.key, site.paths.audit):
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_on_site_secrets_need_their_own_key(site, tmp_path):
    site.paths.key.write_text(base64.b64encode(bytes(32)).decode(), encoding="ascii")
    with pytest.raises(SiteError, match="can't be opened"):
        site.credential_list()


def test_on_site_secrets_refuse_what_they_cant_use(site):
    with pytest.raises(SiteError, match="kind"):
        site.set_credential("snmp", "snmp_v2c", "x", {"community": "x"})
    with pytest.raises(SiteError, match="missing"):
        site.set_credential("empty", "ssh_password", "x", {})
    with pytest.raises(SiteError, match="isn't a host"):
        site.add_target("-oProxyCommand=x", "ios", "ro-ssh")
    with pytest.raises(SiteError, match="no credential"):
        site.add_target(HOST, "ios", "missing")
    site.add_target(HOST, "ios", "ro-ssh")
    with pytest.raises(SiteError, match="still use"):
        site.remove_credential("ro-ssh")
    site.remove_target(HOST)
    site.remove_credential("ro-ssh")
    assert site.credential_list() == []


# The two stores


def test_the_folder_store_writes_once_and_checks_what_it_wrote(tmp_path):
    store = FolderStore(tmp_path)
    store.check()
    job = str(uuid.uuid4())
    receipt = store.write(job, 7, _result())
    folder = tmp_path / "engagement-7" / job
    stored = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    assert stored["devices"][0]["host"] == HOST and stored["job"] == job
    assert (folder / "result.sha256").read_text().split()[0] == receipt.sha256
    assert receipt.devices == 1
    # A retried job writes nothing new; other content for it is refused.
    assert store.write(job, 7, _result()) == receipt
    with pytest.raises(StoreError, match="other content"):
        store.write(job, 7, _result(config="hostname changed\n"))


def test_the_folder_store_refuses_a_folder_that_isnt_there(tmp_path):
    with pytest.raises(StoreError, match="isn't a folder"):
        FolderStore(tmp_path / "unmounted").check()


@pytest.fixture
def postgres_dsn():
    if connection.vendor != "postgresql":
        pytest.skip("needs PostgreSQL")
    db = connection.settings_dict
    schema = f"cartio_onsite_{uuid.uuid4().hex[:8]}"
    parts = {
        "dbname": db["NAME"],
        "user": db.get("USER"),
        "password": db.get("PASSWORD"),
        "host": db.get("HOST"),
        "port": db.get("PORT"),
    }
    dsn = " ".join(f"{k}={v}" for k, v in parts.items() if v)
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            conn.execute(f"CREATE SCHEMA {schema}")
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("this database role can't create a schema")
        yield f"{dsn} options=-csearch_path={schema}"
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


def test_the_postgres_store_writes_once_and_checks_what_it_wrote(postgres_dsn):
    import psycopg

    store = PostgresStore(postgres_dsn)
    store.check()
    job = str(uuid.uuid4())
    receipt = store.write(job, 7, _result())
    with psycopg.connect(postgres_dsn) as conn:
        engagement, result = conn.execute(
            "SELECT engagement, result FROM cartio_collect_results WHERE job = %s", [job]
        ).fetchone()
    assert engagement == 7 and result["devices"][0]["host"] == HOST
    assert store.write(job, 7, _result()) == receipt
    with pytest.raises(StoreError, match="other content"):
        store.write(job, 7, _result(config="hostname changed\n"))


def test_the_postgres_store_never_repeats_the_connection_string():
    with pytest.raises(StoreError) as error:
        PostgresStore("host=192.0.2.1 port=1 password=hunter2 connect_timeout=1").check()
    assert "hunter2" not in str(error.value)


def test_the_canonical_form_is_stable_across_a_round_trip():
    doc = {"b": "é", "a": [1, {"d": None, "c": True}]}
    assert canonical(json.loads(canonical(doc))) == canonical(doc)


# The controller


@pytest.fixture
def clinic():
    return Customer.objects.create(name="Clinic", data_mode="on_site")


@pytest.fixture
def clinic_engagement(clinic):
    return Engagement.objects.for_customer(clinic).create(name="Audit")


def test_the_controller_keeps_no_credentials_or_keys_for_an_on_site_customer(clinic):
    credential = Credential(customer=clinic, name="ro-ssh", kind="ssh_password", username="x")
    with pytest.raises(OnSiteDataError):
        credential.set_secret({"password": PASSWORD})
    with pytest.raises(OnSiteDataError):
        CustomerDataKey.masking_key_for(clinic.pk)
    assert not CustomerDataKey.objects.for_customer(clinic).exists()


def test_an_on_site_collect_job_names_no_targets_and_carries_no_secrets(
    client, clinic, clinic_engagement
):
    vm = Enrolled(clinic)
    job = jobs.queue_job(vm.collector, "collect", "engineer", engagement=clinic_engagement)
    assert job.params == {"on_site": True, "verify_host_keys": True}
    assert job.credential_ids == []
    [message] = vm.poll(client)["jobs"]
    assert message["credentials"] == "" and message["params"] == job.params
    with pytest.raises(jobs.JobError, match="kept on its collector"):
        jobs.queue_job(
            vm.collector,
            "collect",
            "engineer",
            engagement=clinic_engagement,
            params={"targets": [{"host": HOST}]},
        )


class Capture:
    """Forwards the collector's requests to the controller, keeping every byte both ways."""

    def __init__(self, client):
        self.client = client
        self.seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        response = self.client.generic(
            request.method,
            request.url.raw_path.decode(),
            data=body,
            content_type=request.headers.get("content-type", ""),
            HTTP_AUTHORIZATION=request.headers.get("authorization", ""),
        )
        self.seen += [body, response.content]
        return httpx.Response(response.status_code, content=response.content)


def _worker(vm, capture, tmp_path, site, store):
    config = CollectorConfig(
        controller_url="http://localhost",
        collector_id=str(vm.collector.pk),
        customer=vm.collector.customer.name,
        private_key=vm.private_key,
        seal_key=vm.seal_private,
    )
    session = Session(config, http=httpx.Client(transport=httpx.MockTransport(capture)))
    on_site = collector_jobs.OnSite(site, store) if store else None
    return collector_jobs.Worker(session, collector_jobs.Journal(tmp_path / "jobs"), on_site)


def test_an_on_site_collect_leaves_nothing_on_the_controller_or_the_wire(
    client,
    clinic,
    clinic_engagement,
    devices,
    site,
    tmp_path,
):
    """The acceptance check: a capture of the traffic and the controller's database show no
    network data or credentials, and the data is in the on-site store."""
    site.add_target(HOST, "ios", "ro-ssh")
    site.add_target(DOWN, "ios", "ro-ssh")
    store = FolderStore(tmp_path / "share")
    store.root.mkdir()
    vm = Enrolled(clinic)
    job = jobs.queue_job(vm.collector, "collect", "engineer", engagement=clinic_engagement)
    capture = Capture(client)

    assert _worker(vm, capture, tmp_path, site, store).poll_once(0) == 1

    job.refresh_from_db()
    assert job.status == "done" and job.result_size == 0
    assert job.run.result.outcome == "partial" and job.run.result.device_count == 1
    assert "ConnectionRefusedError (1)" in job.run.result.error_summary
    assert devices[0][1]["password"] == PASSWORD
    # In the customer's store, masked with the collector's own key.
    [result_file] = (store.root / f"engagement-{clinic_engagement.pk}").glob("*/result.json")
    stored = json.loads(result_file.read_text(encoding="utf-8"))
    assert stored["devices"][0]["host"] == HOST
    assert f"<masked:{MASKING_KEY_VERSION}:" in stored["devices"][0]["config"]
    assert ENABLE not in result_file.read_text(encoding="utf-8")

    # Nothing that identifies the network, or any secret, crossed the wire...
    _, _, key = site.for_collect()
    forbidden = [HOST, DOWN, "floor-sw1", PASSWORD, "cartio-ro", "<masked:", key.secret.hex()]
    wire = b"".join(capture.seen).decode()
    assert wire and not [f for f in forbidden if f in wire]
    # ...or is anywhere in the controller's records for this customer.
    rows = []
    for model in apps.get_models():
        if issubclass(model, CustomerOwned):
            rows += model.objects.for_customer(clinic)
    dump = serializers.serialize("json", rows)
    assert not [f for f in forbidden if f in dump]


def test_an_on_site_health_check_still_reports_the_collectors_health(client, clinic, tmp_path):
    vm = Enrolled(clinic)
    job = jobs.queue_job(vm.collector, "health", "engineer")
    _worker(vm, Capture(client), tmp_path, None, None).poll_once(0)
    job.refresh_from_db()
    assert job.status == "done" and job.result_size > 0


def test_a_collector_with_an_on_site_store_never_uploads_device_data(
    client,
    devices,
    site,
    tmp_path,
):
    acme = Customer.objects.create(name="Acme")
    engagement = Engagement.objects.for_customer(acme).create(name="Audit")
    credential = Credential(customer=acme, name="ro-ssh", kind="ssh_password", username="x")
    credential.set_secret({"password": PASSWORD})
    credential.save()
    vm = Enrolled(acme)
    params = {"targets": [{"host": HOST, "platform": "ios", "credential": str(credential.pk)}]}
    job = jobs.queue_job(vm.collector, "collect", "engineer", engagement=engagement, params=params)
    store = FolderStore(tmp_path)

    _worker(vm, Capture(client), tmp_path, site, store).poll_once(0)

    job.refresh_from_db()
    assert job.status == "failed" and "keeps device data on site" in job.run.result.error_summary
    assert devices == []


def test_an_on_site_job_fails_on_a_collector_without_a_store(
    client, clinic, clinic_engagement, tmp_path
):
    vm = Enrolled(clinic)
    job = jobs.queue_job(vm.collector, "collect", "engineer", engagement=clinic_engagement)
    _worker(vm, Capture(client), tmp_path, None, None).poll_once(0)
    job.refresh_from_db()
    assert job.status == "failed" and "no on-site store" in job.run.result.error_summary


def test_a_store_that_cant_be_written_stores_nothing_and_fails_the_job(
    client,
    clinic,
    clinic_engagement,
    devices,
    site,
    tmp_path,
):
    site.add_target(HOST, "ios", "ro-ssh")
    vm = Enrolled(clinic)
    job = jobs.queue_job(vm.collector, "collect", "engineer", engagement=clinic_engagement)
    store = FolderStore(tmp_path / "unmounted")
    (tmp_path / "unmounted").write_text("a file, not a folder")

    _worker(vm, Capture(client), tmp_path, site, store).poll_once(0)

    job.refresh_from_db()
    assert job.status == "failed" and "Nothing was stored" in job.run.result.error_summary
    assert Job.objects.for_customer(clinic).get().result_size == 0


# The collector's commands


def test_on_site_commands(tmp_path, monkeypatch, capsys):
    path = tmp_path / "collector.json"
    CollectorConfig(
        controller_url="https://controller.example",
        collector_id=str(uuid.uuid4()),
        customer="Clinic",
        private_key="unused",
        seal_key="unused",
    ).save(path)
    share = tmp_path / "share"
    share.mkdir()
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: PASSWORD)

    def run(*args):
        return cli.main(["--config", str(path), *args])

    assert run("store", "set", "--folder", str(tmp_path / "missing")) == 1
    assert "isn't a folder" in capsys.readouterr().err
    assert run("store", "set", "--folder", str(share)) == 0
    assert CollectorConfig.load(path).store == {"kind": "folder", "path": str(share.resolve())}
    assert run("store", "check") == 0
    assert run("credential", "add", "ro-ssh", "--username", "cartio-ro") == 0
    assert run("target", "add", HOST, "--platform", "ios", "--credential", "ro-ssh") == 0
    capsys.readouterr()
    assert run("credential", "list") == 0
    assert run("target", "list") == 0
    out = capsys.readouterr().out
    assert "ro-ssh\tssh_password\tcartio-ro" in out and f"{HOST}\tios\t22\tro-ssh" in out
    assert PASSWORD not in out
    assert run("credential", "remove", "ro-ssh") == 1
    assert "still use" in capsys.readouterr().err


def test_queue_collect_for_an_on_site_customer_takes_no_targets(
    clinic, clinic_engagement, tmp_path
):
    get_user_model().objects.create_user("engineer")
    vm = Enrolled(clinic)
    options = {"customer": "Clinic", "engagement": "Audit", "collector": "vm01", "user": "engineer"}
    targets = tmp_path / "targets.json"
    targets.write_text("[]")
    with pytest.raises(CommandError, match="kept on its collector"):
        call_command("queue_collect", targets=targets, **options)
    call_command("queue_collect", **options)
    assert Job.objects.for_customer(clinic).get(collector=vm.collector).params["on_site"]
