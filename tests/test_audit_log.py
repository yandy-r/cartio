"""Collection runs and the audit log (YAN-1654)."""

import csv
import io
import json

import pytest
from cartio_controller.collectors.models import (
    AppendOnlyError,
    AuditEvent,
    CollectionRun,
    CollectionRunResult,
    Collector,
    CrossCustomerError,
    Customer,
    Engagement,
    LoginEvent,
)
from cartio_controller.collectors.runs import RunNotAllowed, finish_run, start_run
from cartio_core.protocol import ENROLL_PATH
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

pytestmark = pytest.mark.django_db

CUSTOMERS = "/api/v1/customers"


def _engineer(*models, username="engineer"):
    user = get_user_model().objects.create_user(username, password="x", is_staff=True)
    user.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label="collectors", content_type__model__in=models
        )
    )
    return user


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


@pytest.fixture
def globex():
    return Customer.objects.create(name="Globex")


def _collector(customer, name="vm01"):
    return Collector.objects.for_customer(customer).create(name=name, enrolled_at=timezone.now())


def _actions(customer):
    return list(
        AuditEvent.objects.for_customer(customer)
        .order_by("at", "pk")
        .values_list("action", flat=True)
    )


def test_a_run_is_recorded_once_and_never_changed(acme):
    collector = _collector(acme)
    engagement = Engagement.objects.for_customer(acme).create(name="Audit")

    run = start_run(collector, "discovery", started_by="engineer", engagement=engagement)
    result = finish_run(run, "partial", device_count=12, error_summary="2 devices timed out")

    assert result.run == run and result.customer == acme
    assert _actions(acme) == ["run.started", "run.finished"]
    with pytest.raises(IntegrityError), transaction.atomic():
        finish_run(run, "succeeded")

    run.job_type = "other"
    for change in (
        run.save,
        run.delete,
        result.delete,
        lambda: CollectionRun.all_customers.update(job_type="x"),
        lambda: CollectionRunResult.all_customers.filter(pk=run.pk).delete(),
    ):
        with pytest.raises(AppendOnlyError):
            change()
    assert CollectionRunResult.all_customers.get().outcome == "partial"


def test_a_run_cannot_use_another_customers_engagement(acme, globex):
    theirs = Engagement.objects.for_customer(globex).create(name="Audit")
    with pytest.raises(CrossCustomerError):
        start_run(_collector(acme), "discovery", engagement=theirs)
    assert not CollectionRun.all_customers.exists()


def test_a_run_needs_an_enrolled_collector_and_live_records(acme):
    pending = Collector.objects.for_customer(acme).create(name="new")
    with pytest.raises(RunNotAllowed):
        start_run(pending, "discovery")
    engagement = Engagement.objects.for_customer(acme).create(
        name="Old", archived_at=timezone.now()
    )
    with pytest.raises(RunNotAllowed):
        start_run(_collector(acme), "discovery", engagement=engagement)


def test_engineers_filter_runs_by_engagement_and_collector(client, acme, globex):
    client.force_login(_engineer("collectionrun"))
    vm01, vm02 = _collector(acme, "vm01"), _collector(acme, "vm02")
    audit = Engagement.objects.for_customer(acme).create(name="Audit")
    first = start_run(vm01, "discovery", engagement=audit)
    finish_run(first, "succeeded", device_count=4)
    start_run(vm02, "config_collection", engagement=audit)
    start_run(vm01, "discovery")
    start_run(_collector(globex), "discovery")

    def ids(query=""):
        response = client.get(f"{CUSTOMERS}/{acme.pk}/runs{query}")
        assert response.status_code == 200, response.json()
        return response.json()["results"]

    assert len(ids()) == 3
    assert len(ids(f"?engagement={audit.pk}")) == 2
    by_both = ids(f"?engagement={audit.pk}&collector={vm01.pk}")
    assert [run["id"] for run in by_both] == [str(first.pk)]
    assert by_both[0]["outcome"] == "succeeded" and by_both[0]["device_count"] == 4
    assert {run["outcome"] for run in ids(f"?collector={vm02.pk}")} == {"running"}
    assert client.get(f"{CUSTOMERS}/{acme.pk}/runs?collector=nope").status_code == 400
    # Another customer's run isn't reachable through this customer's URL.
    globex_run = CollectionRun.all_customers.get(customer=globex)
    assert client.get(f"{CUSTOMERS}/{acme.pk}/runs/{globex_run.pk}").status_code == 404


def test_admin_events_are_logged(client):
    client.force_login(_engineer("customer"))
    response = client.post(CUSTOMERS, data={"name": "Acme"}, content_type="application/json")
    assert response.status_code == 201
    acme = Customer.objects.get(name="Acme")

    call_command("create_collector", customer="Acme", name="vm01", stdout=io.StringIO())
    call_command("create_collector", customer="Initech", name="vm01", stdout=io.StringIO())
    collector = Collector.all_customers.get(customer=acme)
    token = collector.issue_enrollment_token()
    collector.save()
    response = client.post(
        ENROLL_PATH,
        data={"token": token, "hostname": "vm01"},
        content_type="application/json",
    )
    assert response.status_code == 201

    assert _actions(acme) == ["record.created", "collector.created", "collector.enrolled"]
    created = AuditEvent.objects.for_customer(acme).get(action="record.created")
    assert created.actor == "engineer" and created.detail == {"data_mode": "hosted"}
    assert client.post(f"{CUSTOMERS}/{acme.pk}/archive").status_code == 200
    assert _actions(acme)[-1] == "record.archived"
    initech = Customer.objects.get(name="Initech")
    assert _actions(initech) == ["record.created", "collector.created"]


def test_sign_ins_are_logged_without_the_password(client):
    get_user_model().objects.create_user("engineer", password="right-one", is_staff=True)
    wrong = "wrong-" + "guess"
    assert not client.login(username="engineer", password=wrong)
    # A username that names no account may be a mistyped password, so it isn't kept.
    assert not client.login(username=wrong, password=wrong)
    assert client.login(username="engineer", password="right-one")
    client.logout()

    events = list(LoginEvent.objects.order_by("at", "pk").values_list("kind", "username"))
    assert events == [
        ("failed", "engineer"),
        ("failed", ""),
        ("logged_in", "engineer"),
        ("logged_out", "engineer"),
    ]
    assert wrong not in json.dumps(list(LoginEvent.objects.values()), default=str)

    event = LoginEvent.objects.first()
    event.username = "someone-else"
    for change in (event.save, event.delete, LoginEvent.objects.all().delete):
        with pytest.raises(AppendOnlyError):
            change()
    with pytest.raises(AppendOnlyError):
        LoginEvent.objects.update(username="x")


def test_audit_export_is_per_customer_and_needs_permission(client, acme, globex):
    collector = _collector(acme)
    run = start_run(collector, '=HYPERLINK("http://evil")')
    finish_run(run, "failed", error_summary="unreachable")
    start_run(_collector(globex), "discovery")
    url = f"{CUSTOMERS}/{acme.pk}/audit-events/export"

    client.force_login(_engineer("customer", username="plain"))
    assert client.post(url).status_code == 403
    assert client.get(f"{CUSTOMERS}/{acme.pk}/audit-events").status_code == 403

    # The export is a read: the view permission alone is enough.
    viewer = _engineer(username="viewer")
    viewer.user_permissions.add(Permission.objects.get(codename="view_auditevent"))
    client.force_login(viewer)
    assert client.post(url).status_code == 200

    client.force_login(_engineer("auditevent", username="auditor"))
    # Exporting writes to the log, so a plain link (a GET) can't do it.
    assert client.get(url).status_code == 405
    response = client.post(url)
    assert response.status_code == 200
    assert "attachment" in response["Content-Disposition"]
    exported = json.loads(b"".join(response.streaming_content))
    actions = ["run.started", "run.finished", "audit.exported", "audit.exported"]
    assert [e["action"] for e in exported] == actions
    assert [e["actor"] for e in exported[-2:]] == ["viewer", "auditor"]

    response = client.post(f"{url}?type=csv")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(b"".join(response.streaming_content).decode())))
    assert {row["action"] for row in rows} == {"run.started", "run.finished", "audit.exported"}
    # A value that would run as a spreadsheet formula is defused.
    started = next(row for row in rows if row["action"] == "run.started")
    assert started["object_name"].startswith("'=")
    assert client.post(f"{url}?type=xml").status_code == 400
    assert _actions(globex) == ["run.started"]
