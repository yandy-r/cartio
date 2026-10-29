"""The job channel (YAN-1651): queue, long-poll, sealed credentials, progress, chunked results,
cancelling, and the "Require uninspected path" setting."""

import hashlib
import json
import threading
import time
import uuid

import pytest
from cartio_controller.collectors import jobs
from cartio_controller.collectors.models import (
    AuditEvent,
    CollectionRunResult,
    Collector,
    Credential,
    Customer,
    Engagement,
    Job,
    JobChunk,
)
from cartio_core import sealing, signing
from cartio_core.protocol import JOBS_NEXT_PATH, job_path, job_seal_context
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from tests.signed import signed_post

from .test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db


class Enrolled:
    """A collector enrolled with both keys, signing like the real one."""

    def __init__(self, customer, name="vm01"):
        self.private_key = signing.new_private_key()
        self.seal_private = sealing.new_private_key()
        self.collector = Collector.objects.for_customer(customer).create(
            name=name,
            enrolled_at=timezone.now(),
            public_key=signing.public_key_for(self.private_key),
            seal_key=sealing.public_key_for(self.seal_private),
        )

    def post(self, client, path, body):
        return signed_post(client, self.collector.pk, self.private_key, path, body)

    def poll(self, client, wait=0, tls_inspected=False):
        response = self.post(client, JOBS_NEXT_PATH, {"wait": wait, "tls_inspected": tls_inspected})
        assert response.status_code == 200, response.content
        return response.json()

    def chunk(self, client, job_id, index, data):
        auth = signing.sign(
            self.private_key,
            str(self.collector.pk),
            "POST",
            job_path(job_id, f"chunks/{index}"),
            data,
        )
        return client.post(
            job_path(job_id, f"chunks/{index}"),
            data=data,
            content_type="application/octet-stream",
            HTTP_AUTHORIZATION=auth,
        )

    def result(self, client, job_id, chunks=(), outcome="succeeded", result_id=None, **extra):
        body = {
            "result_id": result_id or str(uuid.uuid4()),
            "outcome": outcome,
            "chunks": len(chunks),
            "sha256": hashlib.sha256(b"".join(chunks)).hexdigest(),
            "device_count": 0,
            "error_summary": "",
            **extra,
        }
        return self.post(client, job_path(job_id, "result"), body)


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


@pytest.fixture
def globex():
    return Customer.objects.create(name="Globex")


def _api_user(*codenames):
    user = get_user_model().objects.create_user("engineer", password="x", is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename__in=codenames))
    return user


@pytest.fixture
def api(client):
    client.force_login(_api_user("add_job", "change_job", "view_job"))
    return client


def _queue(api, customer, collector, **body):
    response = api.post(
        f"/api/v1/customers/{customer.pk}/jobs",
        data=json.dumps({"collector": str(collector.pk), "type": "health", **body}),
        content_type="application/json",
    )
    return response


def _credential(customer, name="snmp"):
    credential = Credential(customer=customer, name=name, kind="snmp_v2c")
    credential.set_secret({"community": "n0t-a-real-community"})
    credential.save()
    return credential


def test_sealing_opens_only_with_the_right_key_and_context():
    private = sealing.new_private_key()
    sealed = sealing.seal(sealing.public_key_for(private), b"device password", b"job-1")
    assert b"device password" not in sealed.encode()
    assert sealing.open_sealed(private, sealed, b"job-1") == b"device password"
    with pytest.raises(sealing.SealError):
        sealing.open_sealed(sealing.new_private_key(), sealed, b"job-1")
    with pytest.raises(sealing.SealError):
        sealing.open_sealed(private, sealed, b"job-2")
    tampered = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
    with pytest.raises(sealing.SealError):
        sealing.open_sealed(private, tampered, b"job-1")
    with pytest.raises(sealing.SealError):
        sealing.check_public_key("AAAA")


def test_a_job_moves_from_queued_to_running_to_done(client, api, acme):
    vm = Enrolled(acme)
    engagement = Engagement.objects.for_customer(acme).create(name="Audit")
    response = _queue(api, acme, vm.collector, engagement=engagement.pk, params={"seeds": ["a"]})
    assert response.status_code == 201, response.content
    job = response.json()
    assert job["status"] == "queued"
    run = Job.objects.for_customer(acme).get(pk=job["id"]).run
    assert run.job_type == "health" and run.engagement == engagement
    assert AuditEvent.objects.for_customer(acme).filter(action="run.started", actor="engineer")

    delivered = vm.poll(client)["jobs"]
    assert delivered == [
        {
            "id": job["id"],
            "type": "health",
            "engagement": engagement.pk,
            "params": {"seeds": ["a"]},
            "credentials": "",
        }
    ]
    assert api.get(f"/api/v1/customers/{acme.pk}/jobs/{job['id']}").json()["status"] == "running"

    progress = vm.post(client, job_path(job["id"], "progress"), {"message": "Hop 1", "percent": 40})
    assert progress.json() == {"cancelled": False}
    stored = Job.objects.for_customer(acme).get(pk=job["id"])
    assert (stored.progress_message, stored.progress_percent) == ("Hop 1", 40)

    chunks = [b"first part,", b"second part"]
    for index, data in enumerate(chunks):
        assert vm.chunk(client, job["id"], index, data).status_code == 200
    result_id = str(uuid.uuid4())
    response = vm.result(client, job["id"], chunks, result_id=result_id, device_count=3)
    assert response.json() == {"status": "done", "result_id": result_id}

    body = api.get(f"/api/v1/customers/{acme.pk}/jobs/{job['id']}").json()
    assert body["status"] == "done" and body["result_size"] == len(b"".join(chunks))
    assert api.get(f"/api/v1/customers/{acme.pk}/jobs/{job['id']}/result").content == b"".join(
        chunks
    )
    run.refresh_from_db()
    assert run.result.outcome == "succeeded" and run.result.device_count == 3
    # Nothing left to hand over.
    assert vm.poll(client)["jobs"] == []


def test_credentials_travel_sealed_to_the_collector_and_bound_to_the_job(client, api, acme):
    vm = Enrolled(acme)
    credential = _credential(acme)
    job = _queue(api, acme, vm.collector, credentials=[str(credential.pk)]).json()

    [message] = vm.poll(client)["jobs"]
    assert "n0t-a-real-community" not in json.dumps(message)
    opened = json.loads(
        sealing.open_sealed(vm.seal_private, message["credentials"], job_seal_context(job["id"]))
    )
    assert opened[0]["community"] == "n0t-a-real-community"
    with pytest.raises(sealing.SealError):
        sealing.open_sealed(
            vm.seal_private, message["credentials"], job_seal_context(str(uuid.uuid4()))
        )
    used = AuditEvent.objects.for_customer(acme).get(action="credential.used")
    assert used.detail["job"] == job["id"] and used.collector == vm.collector


def test_a_collector_without_a_sealing_key_gets_no_credentials(client, api, acme):
    vm = Enrolled(acme)
    Collector.objects.for_customer(acme).filter(pk=vm.collector.pk).update(seal_key="")
    job = _queue(api, acme, vm.collector, credentials=[str(_credential(acme).pk)]).json()

    assert vm.poll(client)["jobs"] == []
    stored = Job.objects.for_customer(acme).get(pk=job["id"])
    assert stored.status == "failed"
    assert "Enroll it again" in stored.run.result.error_summary
    assert not AuditEvent.objects.for_customer(acme).filter(action="credential.used")


def test_queueing_refuses_bad_jobs(api, acme, globex):
    vm = Enrolled(acme)
    theirs = Enrolled(globex)
    assert _queue(api, acme, theirs.collector).status_code == 400
    assert _queue(api, acme, vm.collector, type="reboot").status_code == 400
    their_credential = _credential(globex)
    assert (
        _queue(api, acme, vm.collector, credentials=[str(their_credential.pk)]).status_code == 400
    )
    their_engagement = Engagement.objects.for_customer(globex).create(name="Audit")
    assert _queue(api, acme, vm.collector, engagement=their_engagement.pk).status_code == 400
    waiting = Collector.objects.for_customer(acme).create(name="not-enrolled")
    assert _queue(api, acme, waiting).status_code == 400
    assert not Job.all_customers.exists()


def test_queueing_and_cancelling_need_permissions(client, acme):
    vm = Enrolled(acme)
    client.force_login(_api_user("view_job"))
    assert _queue(client, acme, vm.collector).status_code == 403
    job = jobs.queue_job(vm.collector, "health", "someone")
    response = client.post(f"/api/v1/customers/{acme.pk}/jobs/{job.pk}/cancel")
    assert response.status_code == 403


def test_another_customers_collector_cant_see_or_touch_a_job(client, api, acme, globex):
    vm = Enrolled(acme)
    theirs = Enrolled(globex)
    other_vm = Enrolled(acme, name="vm02")
    job = _queue(api, acme, vm.collector).json()

    assert theirs.poll(client)["jobs"] == []
    assert other_vm.poll(client)["jobs"] == []
    for intruder in (theirs, other_vm):
        progress = intruder.post(client, job_path(job["id"], "progress"), {"message": "x"})
        assert progress.status_code == 404
        assert intruder.chunk(client, job["id"], 0, b"x").status_code == 404
        assert intruder.result(client, job["id"]).status_code == 404
    # And the engineer API only shows a job under its own customer.
    assert api.get(f"/api/v1/customers/{globex.pk}/jobs/{job['id']}").status_code == 404
    assert vm.poll(client)["jobs"][0]["id"] == job["id"]


def test_a_revoked_collector_is_cut_off(client, api, acme):
    vm = Enrolled(acme)
    job = _queue(api, acme, vm.collector).json()
    vm.poll(client)
    Collector.objects.for_customer(acme).filter(pk=vm.collector.pk).update(
        revoked_at=timezone.now(), public_key=""
    )
    response = vm.post(client, JOBS_NEXT_PATH, {"wait": 0, "tls_inspected": False})
    assert response.status_code == 401
    assert vm.result(client, job["id"]).status_code == 401


def test_cancelling_a_queued_job(client, api, acme):
    vm = Enrolled(acme)
    job = _queue(api, acme, vm.collector).json()
    response = api.post(f"/api/v1/customers/{acme.pk}/jobs/{job['id']}/cancel")
    assert response.json()["status"] == "cancelled"
    assert vm.poll(client)["jobs"] == []

    stored = Job.objects.for_customer(acme).get(pk=job["id"])
    assert stored.run.result.outcome == CollectionRunResult.Outcome.CANCELLED
    event = AuditEvent.objects.for_customer(acme).get(action="job.cancelled")
    assert event.actor == "engineer" and event.object_id == job["id"]
    # Cancelling twice is refused, and logs nothing more.
    assert api.post(f"/api/v1/customers/{acme.pk}/jobs/{job['id']}/cancel").status_code == 400
    assert AuditEvent.objects.for_customer(acme).filter(action="job.cancelled").count() == 1


def test_cancelling_a_running_job_stops_the_collector(client, api, acme):
    vm = Enrolled(acme)
    job = _queue(api, acme, vm.collector).json()
    vm.poll(client)
    api.post(f"/api/v1/customers/{acme.pk}/jobs/{job['id']}/cancel")

    progress = vm.post(client, job_path(job["id"], "progress"), {"message": "Hop 2"})
    assert progress.json() == {"cancelled": True}
    closed = vm.chunk(client, job["id"], 0, b"late")
    assert closed.status_code == 409 and closed.json()["cancelled"] is True
    assert vm.result(client, job["id"]).status_code == 409
    assert not JobChunk.objects.for_customer(acme).exists()
    assert Job.objects.for_customer(acme).get(pk=job["id"]).status == "cancelled"


def test_results_are_stored_once(client, api, acme):
    vm = Enrolled(acme)
    job = _queue(api, acme, vm.collector).json()
    vm.poll(client)

    assert vm.chunk(client, job["id"], 0, b"one").status_code == 200
    # A replayed chunk is acknowledged again; a different one at the same index is refused.
    assert vm.chunk(client, job["id"], 0, b"one").status_code == 200
    assert vm.chunk(client, job["id"], 0, b"two").status_code == 409
    missing = vm.result(client, job["id"], [b"one", b"two"])
    assert missing.status_code == 409 and missing.json()["missing"] == [1]
    assert vm.chunk(client, job["id"], 1, b"two").status_code == 200
    wrong = vm.result(client, job["id"], [b"one", b"TWO"])
    assert wrong.status_code == 409

    result_id = str(uuid.uuid4())
    assert vm.result(client, job["id"], [b"one", b"two"], result_id=result_id).status_code == 200
    # The same result again is a no-op; another result for the job is refused.
    assert vm.result(client, job["id"], [b"one", b"two"], result_id=result_id).status_code == 200
    assert vm.result(client, job["id"], [b"one", b"two"]).status_code == 409
    assert JobChunk.objects.for_customer(acme).count() == 2
    assert CollectionRunResult.objects.for_customer(acme).count() == 1
    assert AuditEvent.objects.for_customer(acme).filter(action="run.finished").count() == 1


def test_a_failed_job_says_why(client, api, acme):
    vm = Enrolled(acme)
    job = _queue(api, acme, vm.collector).json()
    vm.poll(client)
    vm.result(client, job["id"], outcome="failed", error_summary="Seed unreachable")
    stored = Job.objects.for_customer(acme).get(pk=job["id"])
    assert stored.status == "failed"
    assert stored.run.result.error_summary == "Seed unreachable"


def test_an_on_site_customers_results_stay_on_site(client, api):
    on_site = Customer.objects.create(name="Clinic", data_mode="on_site")
    vm = Enrolled(on_site)
    job = _queue(api, on_site, vm.collector).json()
    vm.poll(client)
    assert vm.chunk(client, job["id"], 0, b"device data").status_code == 409
    # The outcome still comes back, without the data.
    assert vm.result(client, job["id"], device_count=4).status_code == 200
    assert Job.objects.for_customer(on_site).get(pk=job["id"]).status == "done"


def test_require_uninspected_path_holds_jobs_back_from_an_inspected_collector(client, api, acme):
    vm = Enrolled(acme)
    acme.require_uninspected_path = True
    acme.save()
    job = _queue(api, acme, vm.collector).json()

    answer = vm.poll(client, tls_inspected=True)
    assert answer == {"jobs": [], "require_uninspected_path": True}
    assert Job.objects.for_customer(acme).get(pk=job["id"]).status == "queued"
    assert vm.poll(client)["jobs"][0]["id"] == job["id"]


def test_the_setting_is_part_of_the_customer_api(acme):
    from django.test import Client

    client = Client()
    user = get_user_model().objects.create_user("admin", password="x", is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename="change_customer"))
    client.force_login(user)
    response = client.patch(
        f"/api/v1/customers/{acme.pk}",
        data={"require_uninspected_path": True},
        content_type="application/json",
    )
    assert response.status_code == 200 and response.json()["require_uninspected_path"] is True
    event = AuditEvent.objects.for_customer(acme).get(action="record.updated")
    assert event.detail["fields"] == ["require_uninspected_path"]


@pytest.mark.django_db(transaction=True)
def test_a_waiting_poll_wakes_when_a_job_is_queued(live_server):
    """The long-poll answers as soon as a job is queued, not when its wait runs out."""
    import httpx

    acme = Customer.objects.create(name="Acme")
    vm = Enrolled(acme)
    answers = []

    def poll():
        body = json.dumps({"wait": 20, "tls_inspected": False}).encode()
        auth = signing.sign(vm.private_key, str(vm.collector.pk), "POST", JOBS_NEXT_PATH, body)
        started = time.monotonic()
        response = httpx.post(
            live_server.url + JOBS_NEXT_PATH,
            content=body,
            headers={"Authorization": auth, "Content-Type": "application/json"},
            timeout=30,
        )
        answers.append((time.monotonic() - started, response.json()))

    waiting = threading.Thread(target=poll)
    waiting.start()
    time.sleep(1.5)
    job = jobs.queue_job(vm.collector, "health", "engineer")
    waiting.join(timeout=30)

    [(took, answer)] = answers
    assert [j["id"] for j in answer["jobs"]] == [str(job.pk)]
    assert took < 10


def test_jobs_screen_shows_state_and_cancels(client, acme):
    user = _engineer()
    user.user_permissions.add(
        *Permission.objects.filter(codename__in=["view_collectionrun", "change_job"])
    )
    _signed_in(client, user)
    vm = Enrolled(acme)
    queued = jobs.queue_job(vm.collector, "health", "engineer")

    page = client.get(f"/customers/{acme.pk}/jobs").content.decode()
    assert "Queued" in page and "Waiting for vm01" in page and "Cancel" in page
    collectors = client.get(f"/customers/{acme.pk}/collectors").content.decode()
    assert "1 job" in collectors

    response = client.post(
        f"/customers/{acme.pk}/jobs", {"action": "cancel", "job": str(queued.pk)}
    )
    assert response.status_code == 302
    queued.refresh_from_db()
    assert queued.status == "cancelled"
    assert AuditEvent.objects.for_customer(acme).filter(action="job.cancelled", actor=user.username)
    assert "Cancelled" in client.get(f"/customers/{acme.pk}/jobs").content.decode()


def test_jobs_screen_cancel_needs_the_permission(client, acme, globex):
    user = _engineer()
    user.user_permissions.add(*Permission.objects.filter(codename="view_collectionrun"))
    _signed_in(client, user)
    vm = Enrolled(acme)
    queued = jobs.queue_job(vm.collector, "health", "engineer")
    assert "Cancel</button>" not in client.get(f"/customers/{acme.pk}/jobs").content.decode()
    response = client.post(
        f"/customers/{acme.pk}/jobs", {"action": "cancel", "job": str(queued.pk)}
    )
    assert response.status_code == 403
    # Another customer's URL can't reach the job either.
    user.user_permissions.add(*Permission.objects.filter(codename="change_job"))
    user = get_user_model().objects.get(pk=user.pk)
    response = client.post(
        f"/customers/{globex.pk}/jobs", {"action": "cancel", "job": str(queued.pk)}
    )
    assert response.status_code == 404
    queued.refresh_from_db()
    assert queued.status == "queued"


def test_a_low_order_sealing_key_is_refused():
    with pytest.raises(sealing.SealError):
        sealing.check_public_key(sealing._b64encode(bytes(32)))


def test_audit_export_streams_under_asgi(acme):
    """Under uvicorn, Django would read a plain iterator whole; the export hands it an async one."""
    from asgiref.sync import async_to_sync
    from django.test import AsyncClient

    user = get_user_model().objects.create_user("auditor", password="x", is_staff=True)
    user.user_permissions.set(Permission.objects.filter(codename="view_auditevent"))
    client = AsyncClient()
    async_to_sync(client.aforce_login)(user)

    async def export():
        response = await client.post(f"/api/v1/customers/{acme.pk}/audit-events/export?type=csv")
        assert response.status_code == 200 and response.is_async
        return b"".join([chunk async for chunk in response.streaming_content])

    assert b"audit.exported" in async_to_sync(export)()
