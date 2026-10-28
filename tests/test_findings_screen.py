"""The findings screen and engineer edits (YAN-1738, DESIGN.md section 7)."""

import pytest
from cartio_controller.collectors.models import (
    AssessmentRun,
    AuditEvent,
    CheckResult,
    Customer,
    Device,
    Engagement,
    Finding,
)
from cartio_controller.engineers.findings import Findings

from tests.test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db


@pytest.fixture
def engineer(client):
    user = _engineer()
    _signed_in(client, user)
    return user


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _engagement(customer, name="Q4 review", status="active"):
    return Engagement.objects.for_customer(customer).create(name=name, status=status)


def _assessed(engagement):
    """Three devices and a run: insecure management fails on two, firmware drift on one."""
    customer = engagement.customer
    make = Device.objects.for_customer(customer).create
    devices = [
        make(engagement=engagement, name=name, platform="ios", model="C9300-48P")
        for name in ("core-sw-01", "core-sw-02", "edge-rtr-01")
    ]
    run = AssessmentRun.objects.for_customer(customer).create(engagement=engagement)
    rows = {
        "insecure-management": ["fail", "fail", "pass"],
        "firmware-drift": ["pass", "fail", "not_applicable"],
        "snmpv3": ["unknown", "pass", "pass"],
    }
    for check_id, statuses in rows.items():
        for device, status in zip(devices, statuses, strict=True):
            CheckResult.objects.for_customer(customer).create(
                run=run,
                device=device,
                check_id=check_id,
                severity="high" if check_id == "insecure-management" else "medium",
                status=status,
                evidence=[f"{check_id} on {device.name}"],
            )
    return devices


def _url(engagement, query=""):
    base = f"/customers/{engagement.customer_id}/engagements/{engagement.pk}/findings"
    return base + query


def _events(customer, action):
    return list(AuditEvent.objects.for_customer(customer).filter(action=action).order_by("at"))


def test_checks_with_counts_and_a_finding_per_failing_check(client, engineer, acme):
    engagement = _engagement(acme)
    _assessed(engagement)
    page = client.get(_url(engagement)).content.decode()
    assert "Insecure management protocols" in page
    assert "1 pass · 2 fail · 0 unknown · 0 n/a" in page
    assert "1 pass · 1 fail · 0 unknown · 1 n/a" in page
    found = Findings(acme, engagement)
    assert [i.key for i in found.items] == ["insecure-management", "firmware-drift"]
    assert [a.device.name for a in found.items[0].failing] == ["core-sw-01", "core-sw-02"]
    # snmpv3 had no failure, so there's no finding for it.
    assert found.get("snmpv3") is None


def test_check_filter_and_detail_show_only_visible_findings(client, engineer, acme):
    engagement = _engagement(acme)
    _assessed(engagement)
    page = client.get(_url(engagement, "?check=firmware-drift")).content.decode()
    assert 'aria-current="true"' in page
    assert "Same model, different OS versions" in page
    assert "core-sw-02" in page
    # Selecting a finding the filter hides falls back to the first visible one.
    response = client.get(_url(engagement, "?check=snmpv3"))
    assert "No open findings for this check." in response.content.decode()
    assert response.context["view"].selected is None


def test_a_finding_in_the_url_selects_it_and_clears_the_filter(client, engineer, acme):
    engagement = _engagement(acme)
    _assessed(engagement)
    response = client.get(_url(engagement, "?check=firmware-drift&finding=insecure-management"))
    view = response.context["view"]
    assert view.check == ""
    assert view.selected.key == "insecure-management"


def test_edit_a_check_finding_needs_a_reason_to_change_severity(client, engineer, acme):
    engagement = _engagement(acme)
    _assessed(engagement)
    form = {
        "action": "edit",
        "finding": "insecure-management",
        "title": "Telnet allowed on VTY lines",
        "severity": "critical",
        "recommendation": "Use SSH only.",
        "include_in_report": "on",
    }
    response = client.post(_url(engagement), form)
    assert response.status_code == 400
    assert "Give a reason for the severity change." in response.content.decode()
    assert not Finding.objects.for_customer(acme).exists()

    response = client.post(_url(engagement), form | {"severity_reason": "Reachable from guests"})
    assert response.status_code == 302
    item = Findings(acme, engagement).get("insecure-management")
    assert (item.title, item.severity, item.edited) == (
        "Telnet allowed on VTY lines",
        "critical",
        True,
    )
    assert item.check_severity == "high"
    # Devices and evidence still come from the check.
    assert [a.device.name for a in item.failing] == ["core-sw-01", "core-sw-02"]
    [event] = _events(acme, AuditEvent.Action.FINDING_UPDATED)
    assert event.actor == engineer.get_username()
    assert event.detail["severity"] == "critical"
    assert event.detail["severity_reason"] == "Reachable from guests"


def test_add_edit_and_delete_a_manual_finding(client, engineer, acme):
    engagement = _engagement(acme)
    devices = _assessed(engagement)
    form = {
        "action": "add",
        "title": "No login banner",
        "severity": "low",
        "check_id": "insecure-management",
        "devices": [str(devices[2].pk)],
        "evidence": "edge-rtr-01 (no banner login)",
        "recommendation": "Add the standard banner.",
        "include_in_report": "on",
    }
    assert client.post(_url(engagement), form | {"devices": []}).status_code == 400
    response = client.post(_url(engagement), form)
    assert response.status_code == 302
    row = Finding.objects.for_customer(acme).get(kind="manual")
    assert response["Location"].endswith(f"?finding={row.pk}")
    page = client.get(response["Location"]).content.decode()
    assert "Added by engineer" in page and "No login banner" in page

    client.post(
        _url(engagement), form | {"action": "edit", "finding": str(row.pk), "title": "Banner"}
    )
    row.refresh_from_db()
    assert row.title == "Banner"

    assert (
        client.post(_url(engagement), {"action": "remove", "finding": str(row.pk)}).status_code
        == 400
    )
    client.post(
        _url(engagement), {"action": "remove", "finding": str(row.pk), "reason": "Duplicate"}
    )
    found = Findings(acme, engagement)
    assert [i.key for i in found.removed] == [str(row.pk)]
    assert str(row.pk) not in [i.key for i in found.items]

    client.post(_url(engagement), {"action": "restore", "finding": str(row.pk)})
    assert str(row.pk) in [i.key for i in Findings(acme, engagement).items]
    actions = list(
        AuditEvent.objects.for_customer(acme)
        .filter(object_type="finding")
        .order_by("at", "pk")
        .values_list("action", flat=True)
    )
    assert actions == ["finding.added", "finding.updated", "finding.removed", "finding.restored"]
    removed = _events(acme, AuditEvent.Action.FINDING_REMOVED)[0]
    assert removed.detail["how"] == "deleted" and removed.detail["reason"] == "Duplicate"


def test_dismiss_include_and_note_shape_the_report(client, engineer, acme):
    engagement = _engagement(acme)
    _assessed(engagement)
    post = lambda data: client.post(_url(engagement), data)  # noqa: E731
    post({"action": "exclude", "finding": "firmware-drift"})
    post({"action": "note", "finding": "insecure-management", "note": "Agreed with IT."})
    found = Findings(acme, engagement)
    assert [i.key for i in found.for_report()] == ["insecure-management"]
    assert found.for_report()[0].note == "Agreed with IT."

    post({"action": "remove", "finding": "insecure-management", "reason": "Lab only"})
    found = Findings(acme, engagement)
    assert found.for_report() == []
    assert found.removed[0].removed_reason == "Lab only"
    dismissed = _events(acme, AuditEvent.Action.FINDING_REMOVED)[0]
    assert dismissed.detail["how"] == "dismissed"
    assert len(_events(acme, AuditEvent.Action.FINDING_UPDATED)) == 2


def test_completed_engagements_are_read_only(client, engineer, acme):
    engagement = _engagement(acme, status="completed")
    _assessed(engagement)
    page = client.get(_url(engagement)).content.decode()
    assert "is completed and read-only" in page
    assert "Add finding" not in page and "Dismiss" not in page
    response = client.post(_url(engagement), {"action": "exclude", "finding": "firmware-drift"})
    assert response.status_code == 403
    assert not Finding.objects.for_customer(acme).exists()


def test_rerun_checks_is_audit_logged(client, engineer, acme):
    engagement = _engagement(acme, status="completed")
    _assessed(engagement)
    response = client.post(_url(engagement), {"action": "run"})
    assert response.status_code == 302
    assert AssessmentRun.objects.for_customer(acme).count() == 2
    assert _events(acme, AuditEvent.Action.ASSESSMENT_RUN)[0].actor == engineer.get_username()


def test_another_customers_finding_is_not_found(client, engineer, acme):
    engagement = _engagement(acme)
    _assessed(engagement)
    other = Customer.objects.create(name="Bluepeak")
    theirs = _engagement(other)
    devices = _assessed(theirs)
    row = Finding.objects.for_customer(other).create(
        engagement=theirs,
        kind="manual",
        title="Theirs",
        severity="low",
        recommendation="x",
        device_ids=[str(devices[0].pk)],
    )
    # Their finding id under our customer's URL, and our engagement under theirs.
    response = client.post(_url(engagement), {"action": "exclude", "finding": str(row.pk)})
    assert response.status_code == 404
    response = client.get(f"/customers/{other.pk}/engagements/{engagement.pk}/findings")
    assert response.status_code == 404
    # A manual finding can't name another customer's device.
    client.post(
        _url(engagement),
        {
            "action": "add",
            "title": "Mixed",
            "severity": "low",
            "recommendation": "x",
            "devices": [str(devices[0].pk)],
        },
    )
    assert not Finding.objects.for_customer(acme).exists()
    page = client.get(_url(engagement)).content.decode()
    assert "Theirs" not in page


def test_on_site_customers(client, engineer):
    locked = Customer.objects.create(name="Ostrander", data_mode=Customer.DataMode.ON_SITE)
    engagement = _engagement(locked)
    page = client.get(_url(engagement)).content.decode()
    assert "inside access only" in page.lower()
    assert client.post(_url(engagement), {"action": "run"}).status_code == 404

    live = Customer.objects.create(
        name="Bluepeak", data_mode=Customer.DataMode.ON_SITE, live_view_allowed=True
    )
    engagement = _engagement(live)
    page = client.get(_url(engagement)).content.decode()
    assert "aren't kept on the controller" in page
    assert "Add finding" not in page
    response = client.post(_url(engagement), {"action": "note", "finding": "x", "note": "n"})
    assert response.status_code == 403


def test_nav_and_switcher_keep_the_findings_screen(client, engineer, acme):
    engagement = _engagement(acme)
    page = client.get(f"/customers/{acme.pk}/findings").content.decode()
    assert (
        f'href="/customers/{acme.pk}/engagements/{engagement.pk}/findings" aria-current="page"'
        in page
    )
    assert "The checks haven't run on this engagement yet." in page
