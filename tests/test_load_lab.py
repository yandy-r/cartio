"""load_lab gives a local controller the sanitized lab to look at (compose setup)."""

import pytest
from cartio_controller.collectors.models import (
    AssessmentRun,
    AuditEvent,
    ConfigSnapshot,
    Customer,
    Device,
    Engagement,
)
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

pytestmark = pytest.mark.django_db


@pytest.fixture
def engineer():
    return get_user_model().objects.create_user("eng", is_staff=True)


@pytest.fixture
def debug(settings):
    settings.DEBUG = True


def _devices(customer, engagement):
    rows = Device.objects.for_customer(customer).filter(engagement__name=engagement)
    return sorted(rows.values_list("name", "site__name"))


def test_loads_two_engagements_with_configs_and_checks(debug, engineer):
    call_command("load_lab", user="eng")
    lab = Customer.objects.get(name="Lab")
    assert _devices(lab, "Q2 review") == [("core-sw1", "HQ"), ("edge-fw1", "HQ")]
    assert _devices(lab, "Q4 review") == [
        ("core-sw1", "HQ"),
        ("edge-fw1", "HQ"),
        ("floor-sw1", "Floor 2"),
    ]
    assert ConfigSnapshot.objects.for_customer(lab).count() == 5
    runs = AssessmentRun.objects.for_customer(lab)
    assert sorted(runs.values_list("engagement__name", flat=True)) == ["Q2 review", "Q4 review"]
    assert all(run.results.exists() for run in runs)
    actions = set(AuditEvent.objects.for_customer(lab).values_list("action", flat=True))
    assert {"record.created", "outputs.stored", "assessment.run"} <= actions


def test_loading_again_changes_nothing(debug, engineer):
    call_command("load_lab", user="eng")
    before = Device.objects.for_customer(Customer.objects.get(name="Lab")).count()
    call_command("load_lab", user="eng")
    assert Customer.objects.count() == 1
    assert Device.objects.for_customer(Customer.objects.get(name="Lab")).count() == before


def test_lab_stays_in_its_own_customer(debug, engineer):
    other = Customer.objects.create(name="Acme")
    call_command("load_lab", user="eng")
    assert not Engagement.objects.for_customer(other).exists()
    assert not Device.objects.for_customer(other).exists()


def test_refused_outside_development(settings, engineer):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="CARTIO_DEBUG"):
        call_command("load_lab", user="eng")
    assert not Customer.objects.exists()


def test_needs_a_known_user(debug):
    with pytest.raises(CommandError, match="No user"):
        call_command("load_lab", user="nobody")


def test_a_failed_load_leaves_nothing_behind(debug, engineer, monkeypatch):
    from cartio_controller.collectors.management.commands import load_lab

    def fail(*args, **kwargs):
        raise RuntimeError("checks failed")

    monkeypatch.setattr(load_lab, "run_assessment", fail)
    with pytest.raises(RuntimeError):
        call_command("load_lab", user="eng")
    assert not Customer.objects.exists()
