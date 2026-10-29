"""Collect jobs (YAN-1753): the masking key travels sealed, the collector masks what it collects
over SSH, and the controller scans the result before storing any of it."""

import base64
import io
import json
import os
from types import SimpleNamespace

import pytest
from cartio_collector import jobs as collector_jobs
from cartio_collector.ssh import CONFIG_COMMANDS, ScrapliDriver
from cartio_controller.collectors import jobs
from cartio_controller.collectors.models import (
    MASKING_KEY_VERSION,
    AuditEvent,
    ConfigSnapshot,
    Credential,
    Customer,
    CustomerDataKey,
    Device,
    Engagement,
    Job,
    RawOutput,
    Source,
)
from cartio_core import sealing
from cartio_core.protocol import MASKING_KEY_KIND, job_seal_context
from cartio_core.protocol import Job as JobMessage
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from tests.test_job_channel import Enrolled
from tests.test_parsers import outputs

pytestmark = pytest.mark.django_db

SP = " "
# An IOS line with a secret in it, built here so the repo holds no literal one.
ENABLE = SP.join(["enable", "secret", "5", "$1$" + "a" * 26])
# Documentation addresses (RFC 5737), never a customer's.
HOST = "192.0.2.10"
DOWN = "192.0.2.99"


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


@pytest.fixture
def audit_engagement(acme):
    return Engagement.objects.for_customer(acme).create(name="Audit")


def _ssh(customer, name="ro-ssh", kind="ssh_password"):
    credential = Credential(customer=customer, name=name, kind=kind, username="cartio-ro")
    secret = {"password": "n0t-a-real-pass"} if kind == "ssh_password" else {}
    if kind == "snmp_v2c":
        secret = {"community": "n0t-a-real-community"}
    credential.set_secret(secret)
    credential.save()
    return credential


def _targets(credential, *hosts):
    return {
        "targets": [
            {"host": host, "platform": "ios", "credential": str(credential.pk)} for host in hosts
        ]
    }


def _queue(vm, engagement, credential, *hosts):
    return jobs.queue_job(
        vm.collector,
        "collect",
        "engineer",
        engagement=engagement,
        params=_targets(credential, *hosts),
    )


def _config(name="floor-sw1"):
    return f"hostname {name}\n{ENABLE}\n!\n"


class FakeConnection:
    """A device answering from the sanitized lab outputs, with a config that holds a secret."""

    def __init__(self, platform, name="floor-sw1"):
        self.answers = {**outputs(name), CONFIG_COMMANDS[platform]: _config(name)}

    def send_command(self, command):
        return SimpleNamespace(result=self.answers.get(command, ""), failed=False)

    def close(self):
        pass


@pytest.fixture
def devices(monkeypatch):
    """SSH that reaches every host but DOWN, recording how each was opened."""
    opened = []

    def open_(platform, host, **options):
        opened.append((host, options))
        if host == DOWN:
            raise ConnectionRefusedError(f"{host} refused the connection")
        return ScrapliDriver(platform, FakeConnection(platform))

    monkeypatch.setattr(collector_jobs.ScrapliDriver, "open", staticmethod(open_))
    return opened


def _run(vm, client):
    """Fetch the job as the collector does, run its handler, and upload what it returns."""
    [message] = vm.poll(client)["jobs"]
    job = JobMessage.from_dict(message)
    secrets = json.loads(
        sealing.open_sealed(vm.seal_private, job.credentials, job_seal_context(job.id))
    )
    outcome = collector_jobs.HANDLERS["collect"](job, secrets, lambda *a: None)
    _upload(vm, client, job.id, outcome.payload, outcome.outcome, outcome.device_count)
    return outcome


def _upload(vm, client, job_id, payload, outcome="succeeded", device_count=1):
    assert vm.chunk(client, job_id, 0, payload).status_code == 200
    response = vm.result(client, job_id, [payload], outcome=outcome, device_count=device_count)
    assert response.status_code == 200, response.content
    return Job.objects.for_customer(vm.collector.customer).get(pk=job_id)


def test_the_masking_key_is_per_customer_and_stable(acme):
    other = Customer.objects.create(name="Globex")
    key = CustomerDataKey.masking_key_for(acme.pk)
    assert key.version == MASKING_KEY_VERSION
    assert key == CustomerDataKey.masking_key_for(acme.pk)
    assert key.secret != CustomerDataKey.masking_key_for(other.pk).secret
    assert key.secret != CustomerDataKey.key_for(acme.pk)
    assert "secret=<hidden>" in repr(key)


def test_the_masking_key_travels_only_sealed_in_a_collect_job(client, acme, audit_engagement):
    vm = Enrolled(acme)
    credential = _ssh(acme)
    job = _queue(vm, audit_engagement, credential, HOST)
    other = Enrolled(acme, "vm02")
    jobs.queue_job(other.collector, "health", "engineer")

    [collect_message] = vm.poll(client)["jobs"]
    [health_message] = other.poll(client)["jobs"]
    key = CustomerDataKey.masking_key_for(acme.pk)
    encoded = base64.b64encode(key.secret).decode()
    assert encoded not in json.dumps(collect_message)
    secrets = json.loads(
        sealing.open_sealed(
            vm.seal_private, collect_message["credentials"], job_seal_context(str(job.pk))
        )
    )
    [masking] = [s for s in secrets if s["kind"] == MASKING_KEY_KIND]
    assert masking == {"kind": MASKING_KEY_KIND, "version": key.version, "secret": encoded}
    assert [s["password"] for s in secrets if s.get("id") == str(credential.pk)] == [
        "n0t-a-real-pass"
    ]
    # A job that isn't a collect never carries it.
    assert health_message["credentials"] == ""
    assert not AuditEvent.objects.for_customer(acme).filter(detail__icontains=encoded).exists()


def test_a_collect_job_lands_as_inventory_and_snapshots(client, acme, audit_engagement, devices):
    vm = Enrolled(acme)
    job = _queue(vm, audit_engagement, _ssh(acme), HOST)

    outcome = _run(vm, client)
    assert outcome.outcome == "succeeded"
    assert ENABLE not in outcome.payload.decode()
    assert devices[0][1]["password"] == "n0t-a-real-pass"
    assert devices[0][1]["known_hosts_file"] is True

    job.refresh_from_db()
    assert job.status == "done" and job.run.result.device_count == 1
    source = Source.objects.for_customer(acme).get()
    assert source.kind == "live_run" and source.run == job.run
    device = Device.objects.for_customer(acme).get()
    assert device.name == "floor-sw1" and device.management_ip == HOST
    assert RawOutput.objects.for_customer(acme).filter(device=device).count() == len(
        outputs("floor-sw1")
    )
    snapshot = ConfigSnapshot.objects.for_customer(acme).get()
    assert snapshot.key_version == MASKING_KEY_VERSION
    assert f"<masked:{MASKING_KEY_VERSION}:" in snapshot.content
    assert ENABLE not in snapshot.content


def test_an_unreachable_device_makes_the_job_partial(client, acme, audit_engagement, devices):
    vm = Enrolled(acme)
    job = _queue(vm, audit_engagement, _ssh(acme), HOST, DOWN)

    outcome = _run(vm, client)
    assert outcome.outcome == "partial" and outcome.device_count == 1
    # The kind of error, never its message.
    assert (
        outcome.error_summary
        == f"Couldn't collect 1 of 2 devices: {DOWN} (ConnectionRefusedError)."
    )
    assert "refused the connection" not in outcome.payload.decode()
    job.refresh_from_db()
    assert job.status == "done" and job.run.result.outcome == "partial"
    assert Device.objects.for_customer(acme).count() == 1


def test_no_device_reached_fails_the_job_and_stores_nothing(
    client, acme, audit_engagement, devices
):
    vm = Enrolled(acme)
    job = _queue(vm, audit_engagement, _ssh(acme), DOWN)
    assert _run(vm, client).outcome == "failed"
    job.refresh_from_db()
    assert job.status == "failed"
    assert not Source.objects.for_customer(acme).exists()


def test_a_private_key_reaches_scrapli_as_a_file_that_is_gone_afterwards(
    client, acme, audit_engagement, devices, monkeypatch
):
    vm = Enrolled(acme)
    credential = Credential(customer=acme, name="ro-key", kind="ssh_key", username="cartio-ro")
    credential.set_secret({"private_key": "not-a-real-key", "passphrase": "n0t-a-phrase"})
    credential.save()
    seen = []
    real_open = collector_jobs.ScrapliDriver.open

    def open_(platform, host, **options):
        with open(options["private_key"], encoding="utf-8") as f:
            seen.append(f.read())
        return real_open(platform, host, **options)

    monkeypatch.setattr(collector_jobs.ScrapliDriver, "open", staticmethod(open_))
    _queue(vm, audit_engagement, credential, HOST)
    _run(vm, client)
    assert seen == ["not-a-real-key"]
    path = devices[0][1]["private_key"]
    assert devices[0][1]["passphrase"] == "n0t-a-phrase"
    assert not os.path.exists(path)


def test_a_result_with_an_unmasked_secret_stores_nothing(client, acme, audit_engagement):
    vm = Enrolled(acme)
    job = _queue(vm, audit_engagement, _ssh(acme), HOST)
    vm.poll(client)
    payload = {
        "key_version": MASKING_KEY_VERSION,
        "devices": [
            {
                "host": HOST,
                "platform": "ios",
                "outputs": outputs("floor-sw1"),
                "config": _config(),
            }
        ],
        "failed": [],
    }
    job = _upload(vm, client, str(job.pk), json.dumps(payload).encode())

    assert job.status == "failed"
    assert "unmasked secret" in job.run.result.error_summary
    refused = AuditEvent.objects.for_customer(acme).get(action="config.rejected")
    assert refused.detail["lines"] == [2] and ENABLE not in json.dumps(refused.detail)
    assert not Source.objects.for_customer(acme).exists()
    assert not Device.objects.for_customer(acme).exists()


@pytest.mark.parametrize("key_version", ["k1", MASKING_KEY_VERSION])
def test_a_result_masked_with_another_key_stores_nothing(
    client, acme, audit_engagement, key_version
):
    vm = Enrolled(acme)
    job = _queue(vm, audit_engagement, _ssh(acme), HOST)
    vm.poll(client)
    # The lab config was masked with "k1": wrong whatever key version the result names.
    lab_config = open("tests/fixtures/sanitized/ios/floor-sw1/config.cfg", encoding="utf-8").read()
    payload = {
        "key_version": key_version,
        "devices": [{"host": HOST, "platform": "ios", "outputs": {}, "config": lab_config}],
        "failed": [],
    }
    job = _upload(vm, client, str(job.pk), json.dumps(payload).encode())
    assert job.status == "failed"
    assert "masking key version" in job.run.result.error_summary
    assert not Device.objects.for_customer(acme).exists()


def test_an_unreadable_result_fails_the_job(client, acme, audit_engagement):
    vm = Enrolled(acme)
    job = _queue(vm, audit_engagement, _ssh(acme), HOST)
    vm.poll(client)
    job = _upload(vm, client, str(job.pk), b"not json")
    assert job.status == "failed"
    assert job.run.result.error_summary == "The collect result isn't readable."


def test_a_collector_without_the_masking_key_collects_nothing(devices):
    job = JobMessage(
        id="00000000-0000-0000-0000-000000000001",
        type="collect",
        params={"targets": [{"host": HOST, "platform": "ios", "credential": "x"}]},
    )
    with pytest.raises(collector_jobs.MissingMaskingKey):
        collector_jobs.HANDLERS["collect"](job, [], lambda *a: None)
    assert devices == []


def test_queueing_a_collect_job_refuses_what_it_cant_run(acme, audit_engagement):
    vm = Enrolled(acme)
    ssh = _ssh(acme)
    globex = Customer.objects.create(name="Globex")
    theirs = _ssh(globex)
    snmp = _ssh(acme, "snmp", kind="snmp_v2c")

    def refused(engagement=audit_engagement, params=None, **extra):
        with pytest.raises(jobs.JobError) as error:
            jobs.queue_job(
                vm.collector,
                "collect",
                "engineer",
                engagement=engagement,
                params=_targets(ssh, HOST) if params is None else params,
                **extra,
            )
        return str(error.value)

    assert "needs an engagement" in refused(engagement=None)
    assert "1 to 500 targets" in refused(params={"targets": []})
    assert "credentials" in refused(params=_targets(theirs, HOST))
    assert "SSH credentials" in refused(params=_targets(snmp, HOST))
    assert "from its targets" in refused(credential_ids=[ssh.pk])
    bad = _targets(ssh, HOST)
    bad["targets"][0]["platform"] = "junos"
    assert "platform" in refused(params=bad)
    bad = _targets(ssh, "admin@" + HOST)
    assert "isn't a host" in refused(params=bad)
    assert "isn't a host" in refused(params=_targets(ssh, "-oProxyCommand=x"))

    assert not Job.all_customers.exists()


def test_an_on_site_customer_cant_name_targets_on_the_controller():
    clinic = Customer.objects.create(name="Clinic", data_mode="on_site")
    engagement = Engagement.objects.for_customer(clinic).create(name="Audit")
    vm = Enrolled(clinic)
    made_up = SimpleNamespace(pk="00000000-0000-4000-8000-000000000001")
    with pytest.raises(jobs.JobError, match="On site"):
        _queue(vm, engagement, made_up, HOST)


def test_queue_collect_command(acme, audit_engagement, tmp_path):
    get_user_model().objects.create_user("engineer")
    vm = Enrolled(acme)
    _ssh(acme)
    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps([{"host": HOST, "platform": "ios", "credential": "ro-ssh"}]))
    common = ["--customer", "acme", "--engagement", "audit", "--collector", vm.collector.name]
    out = io.StringIO()
    call_command(
        "queue_collect", *common, "--targets", str(targets), "--user", "engineer", stdout=out
    )
    job = Job.objects.for_customer(acme).get()
    assert job.type == "collect" and job.params["verify_host_keys"] is True
    assert "for 1 devices" in out.getvalue()

    targets.write_text(json.dumps([{"host": HOST, "platform": "ios", "credential": "nope"}]))
    with pytest.raises(CommandError, match="no credential"):
        call_command("queue_collect", *common, "--targets", str(targets), "--user", "engineer")


def test_the_most_targets_with_the_longest_host_names_fit(acme, audit_engagement):
    vm = Enrolled(acme)
    longest = "h" * 63 + "." + "a" * 63 + "." + "b" * 63 + "." + "c" * 61
    job = _queue(vm, audit_engagement, _ssh(acme), *[longest] * 500)
    assert len(job.params["targets"]) == 500
