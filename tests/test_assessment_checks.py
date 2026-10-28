"""Assessment checks run on an engagement's stored data and keep results per device (YAN-1664)."""

from datetime import date

import pytest
from cartio_controller.collectors.assessments import AssessmentNotAllowed, run_assessment
from cartio_controller.collectors.inventory import store_outputs
from cartio_controller.collectors.models import (
    AppendOnlyError,
    AssessmentRun,
    AuditEvent,
    CheckResult,
    ConfigSnapshot,
    CrossCustomerError,
    CrossEngagementError,
    Customer,
    Device,
    Engagement,
    OnSiteDataError,
    RawOutput,
    Source,
)
from cartio_core import checks as checks_module
from cartio_core.checks import (
    CHECKS,
    Check,
    DeviceFacts,
    Severity,
    Status,
    failed,
    passed,
    run_checks,
)
from cartio_core.masking import Platform
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.utils import timezone

from tests.test_parsers import FIXTURES, LAB, outputs

DRIFT = CHECKS["firmware-drift"]


def _drift(*devices):
    return {r.device: r.outcome for r in run_checks(list(devices), [DRIFT])}


# The framework, without a database.


def test_missing_data_is_unknown_never_pass():
    outcomes = _drift(
        DeviceFacts("sw1", "ios", "Cisco", "C9300-48P", ""),
        DeviceFacts("sw2", "ios", "Cisco", "", "17.9.4"),
        DeviceFacts("sw3", "ios", "Cisco", "C9300-48P", "17.9.4"),
    )
    assert outcomes["sw1"].status == Status.UNKNOWN
    assert outcomes["sw2"].status == Status.UNKNOWN
    # sw1's version is unknown, so it doesn't count as drift for sw3.
    assert outcomes["sw3"].status == Status.PASS


def test_firmware_drift_fails_every_device_of_the_model_with_evidence():
    outcomes = _drift(
        DeviceFacts("sw1", "ios", "Cisco", "C9300-48P", "17.9.4"),
        DeviceFacts("sw2", "ios", "Cisco", "C9300-48P", "17.6.1"),
        DeviceFacts("fw1", "fortios", "Fortinet", "FortiGate-60F", "7.2.8"),
    )
    assert outcomes["sw1"].status == Status.FAIL
    assert outcomes["sw1"].evidence == (
        "sw1 runs 17.9.4.",
        "sw2 (C9300-48P) runs 17.6.1.",
    )
    assert outcomes["sw2"].status == Status.FAIL
    assert outcomes["fw1"] == passed("No other FortiGate-60F in this engagement.")


def _check(logic, platforms=frozenset()):
    return Check(
        id="sample",
        title="Sample",
        severity=Severity.LOW,
        recommendation="Fix it.",
        logic=logic,
        platforms=platforms,
    )


def test_applies_to_limits_a_check_to_its_platforms():
    check = _check(lambda device, engagement: failed("Bad."), frozenset({Platform.FORTIOS}))
    assert check.run(DeviceFacts("sw1", "ios"), []).status == Status.NOT_APPLICABLE
    assert check.run(DeviceFacts("fw1", "fortios"), []).status == Status.FAIL
    # A device whose platform isn't known yet can't be ruled out.
    assert check.run(DeviceFacts("x1"), []).status == Status.UNKNOWN


def test_a_check_that_breaks_reports_unknown_not_pass():
    def broken(device, engagement):
        raise KeyError("show version")

    assert _check(broken).run(DeviceFacts("sw1", "ios"), []).status == Status.UNKNOWN


def test_every_check_is_complete():
    for check_id, check in CHECKS.items():
        assert check.id == check_id
        assert check.title and check.recommendation
        assert check.severity in Severity


# Runs on stored data.


@pytest.fixture
def acme(db, monkeypatch):
    # End-of-life results depend on the date.
    monkeypatch.setattr(checks_module, "today", lambda: date(2026, 10, 28))
    return Customer.objects.create(name="Acme")


def _engagement(customer, name="Audit"):
    return Engagement.objects.for_customer(customer).get_or_create(name=name)[0]


def _lab(customer, engagement="Audit"):
    """The lab devices, stored from their sanitized outputs as an upload."""
    engagement = _engagement(customer, engagement)
    source = Source.objects.for_customer(customer).create(
        engagement=engagement, kind="upload", name="lab outputs"
    )
    for name, platform in LAB.items():
        store_outputs(source, platform.value, outputs(name))
        config = (FIXTURES / platform.value / name / "config.cfg").read_text(encoding="utf-8")
        device = Device.objects.for_customer(customer).get(engagement=engagement, name=name)
        ConfigSnapshot.objects.for_customer(customer).create(
            device=device, source=source, content=config
        )
    return engagement


def _results(run):
    return {
        (r.device.name, r.check_id): (r.status, r.severity)
        for r in CheckResult.objects.for_customer(run.customer).filter(run=run)
    }


def test_the_checks_run_on_lab_data_and_store_results_per_device(acme):
    engagement = _lab(acme)

    run = run_assessment(engagement, started_by="dana")

    assert run.check_ids == list(CHECKS)
    # core-sw1 and edge-fw1 are hardened; floor-sw1 is not. Three models: nothing to drift from.
    assert _results(run) == {
        ("core-sw1", "firmware-drift"): ("pass", "medium"),
        ("core-sw1", "eol-eos"): ("fail", "high"),
        ("core-sw1", "insecure-management"): ("pass", "high"),
        ("core-sw1", "ntp-syslog"): ("pass", "medium"),
        ("core-sw1", "snmpv3"): ("pass", "medium"),
        ("edge-fw1", "firmware-drift"): ("pass", "medium"),
        ("edge-fw1", "eol-eos"): ("fail", "high"),
        ("edge-fw1", "insecure-management"): ("pass", "high"),
        ("edge-fw1", "ntp-syslog"): ("pass", "medium"),
        ("edge-fw1", "snmpv3"): ("pass", "medium"),
        ("floor-sw1", "firmware-drift"): ("pass", "medium"),
        ("floor-sw1", "eol-eos"): ("pass", "high"),
        ("floor-sw1", "insecure-management"): ("fail", "high"),
        ("floor-sw1", "ntp-syslog"): ("fail", "medium"),
        ("floor-sw1", "snmpv3"): ("fail", "medium"),
    }
    event = AuditEvent.objects.for_customer(acme).get(action="assessment.run")
    assert (event.actor, event.object_id, event.object_name) == ("dana", str(run.pk), "Audit")
    assert event.detail["device_count"] == 3
    assert event.detail["results"] == {"pass": 10, "fail": 5, "not_applicable": 0, "unknown": 0}


def test_checks_rerun_on_stored_data_without_collecting_again(acme):
    engagement = _lab(acme)
    first = run_assessment(engagement)
    # Data changes after the first run, as a later import would change it.
    switches = Device.objects.for_customer(acme).filter(name__in=["core-sw1", "floor-sw1"])
    switches.update(manufacturer="Cisco", model="C9300-48P")
    switches.filter(name="core-sw1").update(os_version="17.6.1")
    switches.filter(name="floor-sw1").update(os_version="17.9.4")

    second = run_assessment(engagement)

    assert AssessmentRun.objects.for_customer(acme).latest() == second
    assert _results(first)[("core-sw1", "firmware-drift")][0] == "pass"
    assert _results(second)[("core-sw1", "firmware-drift")][0] == "fail"
    assert _results(second)[("floor-sw1", "firmware-drift")][0] == "fail"
    # The first run's results stay as they were.
    drift = CheckResult.objects.for_customer(acme).filter(run=first, check_id="firmware-drift")
    assert drift.filter(status="pass").count() == 3


def test_results_are_scoped_to_one_customer_and_engagement(acme):
    globex = Customer.objects.create(name="Globex")
    _lab(globex)
    _lab(acme, "Last year")
    engagement = _engagement(acme)
    Device.objects.for_customer(acme).create(
        engagement=engagement, name="sw9", platform="ios", model="C9300-48P", os_version="17.9.4"
    )

    run = run_assessment(engagement)

    # Only this engagement's one device, none from the other engagement or customer.
    assert set(_results(run)) == {("sw9", check_id) for check_id in CHECKS}
    assert not CheckResult.objects.for_customer(globex).exists()
    assert (
        not CheckResult.objects.for_customer(acme)
        .filter(run=run)
        .exclude(device__engagement=engagement)
    )


def test_a_result_cant_mix_engagements(acme):
    run = run_assessment(_engagement(acme))
    other = Device.objects.for_customer(acme).create(
        engagement=_engagement(acme, "Other"), name="x"
    )
    with pytest.raises(CrossEngagementError):
        CheckResult.objects.for_customer(acme).create(
            run=run, device=other, check_id="firmware-drift", severity="medium", status="pass"
        )


def test_results_are_append_only(acme):
    run = run_assessment(_lab(acme))
    result = CheckResult.objects.for_customer(acme).filter(run=run).first()
    result.status = "fail"
    with pytest.raises(AppendOnlyError):
        result.save()
    with pytest.raises(AppendOnlyError):
        CheckResult.objects.for_customer(acme).filter(run=run).delete()


def test_on_site_customers_keep_results_in_their_own_store(acme):
    engagement = _engagement(acme)
    Customer.objects.filter(pk=acme.pk).update(data_mode=Customer.DataMode.ON_SITE)
    with pytest.raises(OnSiteDataError):
        run_assessment(engagement)
    assert not AssessmentRun.all_customers.exists()


def test_archived_engagements_dont_run(acme):
    engagement = _engagement(acme)
    engagement.archived_at = timezone.now()
    engagement.save()
    with pytest.raises(AssessmentNotAllowed):
        run_assessment(engagement)
    assert not AuditEvent.objects.for_customer(acme).filter(action="assessment.run").exists()


def test_run_checks_command(acme, capsys):
    get_user_model().objects.create_user("dana")
    _lab(acme)

    call_command("run_checks", customer="acme", engagement="audit", user="dana")

    assert "Results: 5 fail, 10 pass." in capsys.readouterr().out
    assert AssessmentRun.objects.for_customer(acme).get().started_by == "dana"
    with pytest.raises(CommandError, match="no engagement"):
        call_command("run_checks", customer="Acme", engagement="Nope", user="dana")


def test_a_device_without_a_manufacturer_still_counts_for_drift():
    outcomes = _drift(
        DeviceFacts("sw1", "ios", "Cisco", "C9300-48P", "17.9.4"),
        DeviceFacts("sw2", "ios", "", "C9300-48P", "17.6.1"),
    )
    assert {o.status for o in outcomes.values()} == {Status.FAIL}


def test_a_check_returning_the_wrong_type_is_unknown_and_ids_run_once():
    check = _check(lambda device, engagement: "pass")
    assert check.run(DeviceFacts("sw1", "ios"), []).status == Status.UNKNOWN
    with pytest.raises(ValueError, match="only once"):
        run_checks([DeviceFacts("sw1")], [DRIFT, DRIFT])


def test_checks_read_the_latest_config_and_output(acme):
    engagement = _engagement(acme)
    source = Source.objects.for_customer(acme).create(engagement=engagement, kind="upload")
    device = Device.objects.for_customer(acme).create(engagement=engagement, name="sw1")
    for text in ("hostname old", "hostname new"):
        ConfigSnapshot.objects.for_customer(acme).create(device=device, source=source, content=text)
        RawOutput.objects.for_customer(acme).create(
            source=source, device=device, command="show version", output=text
        )
    seen = []

    def spy(device, engagement):
        seen.append((device.config, dict(device.outputs)))
        return passed()

    run_assessment(engagement, checks=[_check(spy)])

    assert seen == [("hostname new", {"show version": "hostname new"})]


def test_a_run_never_crosses_customers(acme):
    globex = Customer.objects.create(name="Globex")
    theirs = Device.objects.for_customer(globex).create(engagement=_engagement(globex), name="x")
    run = run_assessment(_engagement(acme))
    with pytest.raises(CrossCustomerError):
        CheckResult.objects.for_customer(acme).create(
            run=run, device=theirs, check_id="firmware-drift", severity="medium", status="pass"
        )
    with pytest.raises(CrossCustomerError):
        CheckResult.objects.for_customer(globex).create(
            run=run, device=theirs, check_id="firmware-drift", severity="medium", status="pass"
        )
    # A run on Globex's engagement only ever holds Globex's devices.
    globex_run = run_assessment(theirs.engagement)
    assert globex_run.customer == globex
    assert {r.device for r in CheckResult.objects.for_customer(globex)} == {theirs}
    assert not CheckResult.objects.for_customer(acme).exists()


def test_archived_customers_dont_run(acme):
    engagement = _engagement(acme)
    acme.archived_at = timezone.now()
    acme.save()
    with pytest.raises(AssessmentNotAllowed):
        run_assessment(engagement)
