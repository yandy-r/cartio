"""Compare: two engagements of one customer, and per-device config diffs (YAN-1667)."""

import datetime

import pytest
from cartio_controller.collectors.models import (
    AssessmentRun,
    AuditEvent,
    CheckResult,
    ConfigSnapshot,
    CrossCustomerError,
    Customer,
    Device,
    Engagement,
    Finding,
    Site,
    Source,
)
from cartio_controller.engineers.compare import Comparison, diff_lines

from tests.test_engineer_ui import _engineer, _signed_in

pytestmark = pytest.mark.django_db

# Placeholders as the collector writes them: key version, then a 16-hex digest.
OLD_PW = "<masked:k1:1111111111111111>"
NEW_PW = "<masked:k1:2222222222222222>"
ROTATED_PW = "<masked:k2:3333333333333333>"
COMMUNITY_K1 = "<masked:k1:4444444444444444>"
COMMUNITY_K2 = "<masked:k2:5555555555555555>"

BASE_CONFIG = f"""hostname core-sw-01
username netops privilege 15 password {OLD_PW}
snmp-server community {COMMUNITY_K1} RO
ip http server
line vty 0 4
 transport input telnet ssh
"""

CURRENT_CONFIG = f"""hostname core-sw-01
username netops privilege 15 password {NEW_PW}
snmp-server community {COMMUNITY_K2} RO
line vty 0 4
 transport input ssh
ntp server 192.0.2.123
"""


@pytest.fixture
def engineer(client):
    user = _engineer()
    _signed_in(client, user)
    return user


@pytest.fixture
def acme():
    return Customer.objects.create(name="Acme")


def _engagement(customer, name, start, status="active"):
    return Engagement.objects.for_customer(customer).create(
        name=name, status=status, start_date=datetime.date(*start)
    )


def _devices(engagement, rows, site=None):
    """Devices from (name, os_version) rows, with an upload source for their configs."""
    customer = engagement.customer
    source = Source.objects.for_customer(customer).create(
        engagement=engagement, kind=Source.Kind.UPLOAD, name="configs.zip"
    )
    devices = {}
    for name, os_version in rows:
        devices[name] = Device.objects.for_customer(customer).create(
            engagement=engagement,
            name=name,
            platform="ios",
            model="C9300-48P",
            os_version=os_version,
            site=site,
            source=source,
        )
    return devices, source


def _config(device, source, text):
    return ConfigSnapshot.objects.for_customer(device.customer).create(
        device=device, source=source, content=text
    )


def _failing(engagement, devices, rows):
    """A run in which each (check, device) in `rows` fails and the rest pass."""
    customer = engagement.customer
    run = AssessmentRun.objects.for_customer(customer).create(engagement=engagement)
    for check_id in ("insecure-management", "ntp-syslog"):
        for name, device in devices.items():
            CheckResult.objects.for_customer(customer).create(
                run=run,
                device=device,
                check_id=check_id,
                severity="high",
                status="fail" if (check_id, name) in rows else "pass",
                evidence=[],
            )


@pytest.fixture
def pair(acme):
    """Q2 and Q4 of Acme, with deliberate changes between them."""
    hq = Site.objects.for_customer(acme).create(name="HQ")
    q2 = _engagement(acme, "Q2 review", (2026, 4, 14), status="completed")
    q4 = _engagement(acme, "Q4 review", (2026, 10, 28))
    old, old_source = _devices(
        q2,
        [("core-sw-01", "17.6.5"), ("core-sw-02", "17.9.4a"), ("legacy-sw", "15.2")],
        site=hq,
    )
    new, new_source = _devices(
        q4,
        [("core-sw-01", "17.9.4a"), ("core-sw-02", "17.9.4a"), ("east-sw-02", "17.9.4a")],
        site=hq,
    )
    _config(old["core-sw-01"], old_source, BASE_CONFIG)
    _config(new["core-sw-01"], new_source, CURRENT_CONFIG)
    same = "hostname core-sw-02\nntp server 192.0.2.1\n"
    _config(old["core-sw-02"], old_source, same)
    _config(new["core-sw-02"], new_source, same)
    _failing(
        q2,
        old,
        {
            ("insecure-management", "core-sw-01"),
            ("ntp-syslog", "core-sw-02"),
            ("ntp-syslog", "legacy-sw"),
        },
    )
    _failing(q4, new, {("ntp-syslog", "core-sw-02"), ("insecure-management", "east-sw-02")})
    return q2, q4


def _url(engagement, query=""):
    return f"/customers/{engagement.customer_id}/engagements/{engagement.pk}/compare{query}"


def test_summary_of_changes(acme, pair):
    q2, q4 = pair
    found = Comparison(acme, q2, q4)
    groups = {k: [(c.host, c.text) for c in v] for k, v in found.groups.items()}
    assert groups["added"] == [("east-sw-02", "C9300-48P on 17.9.4a")]
    assert groups["removed"] == [("legacy-sw", "In the baseline, not found in this engagement.")]
    assert groups["os"] == [("core-sw-01", "17.6.5 → 17.9.4a")]
    assert groups["fixed"] == [("core-sw-01", "Insecure management protocols")]
    assert [host for host, _ in groups["new"]] == ["east-sw-02"]
    assert [host for host, _ in groups["open"]] == ["core-sw-02"]
    # legacy-sw's NTP finding isn't fixed: the device is gone, and shows as removed instead.
    assert all(c.site == "HQ" for c in found.groups["added"])


def test_manual_findings_match_by_title(acme, pair):
    q2, q4 = pair
    for engagement in (q2, q4):
        Finding.objects.for_customer(acme).create(
            engagement=engagement,
            kind=Finding.Kind.MANUAL,
            title="No spare power supply",
            severity="low",
        )
    Finding.objects.for_customer(acme).create(
        engagement=q2,
        kind=Finding.Kind.MANUAL,
        title="Cabling is unlabeled",
        severity="low",
    )
    found = Comparison(acme, q2, q4)
    assert ("", "No spare power supply") in [(c.host, c.text) for c in found.groups["open"]]
    assert ("", "Cabling is unlabeled") in [(c.host, c.text) for c in found.groups["fixed"]]


def test_secrets_compare_by_digest():
    lines, added, removed = diff_lines(BASE_CONFIG, CURRENT_CONFIG)
    by_text = {(line.sig, line.text): line.flag for line in lines}
    # Same key version, other digest: the secret changed, and only its digest shows.
    assert by_text[("+", f"username netops privilege 15 password {NEW_PW}")] == "secret"
    assert ("-", f"username netops privilege 15 password {OLD_PW}") in by_text
    # Only the key version moved: can't compare, so not counted as a change.
    assert by_text[("", f"snmp-server community {COMMUNITY_K2} RO")] == "rotated"
    assert ("-", "ip http server") in by_text
    assert ("+", "ntp server 192.0.2.123") in by_text
    assert (added, removed) == (3, 3)


def test_unchanged_secret_is_unchanged():
    text = f"username netops password {OLD_PW}\n"
    lines, added, removed = diff_lines(text, text)
    assert (added, removed) == (0, 0)
    assert [line.flag for line in lines] == [""]


def test_only_changed_configs_are_diffed(acme, pair):
    q2, q4 = pair
    found = Comparison(acme, q2, q4)
    assert [d.host for d in found.diffs] == ["core-sw-01"]


def test_a_key_rotation_alone_is_not_a_changed_config(acme):
    q2 = _engagement(acme, "Q2", (2026, 4, 1))
    q4 = _engagement(acme, "Q4", (2026, 10, 1))
    old, old_source = _devices(q2, [("rtr-1", "17.9")])
    new, new_source = _devices(q4, [("rtr-1", "17.9")])
    _config(old["rtr-1"], old_source, f"snmp-server community {COMMUNITY_K1} RO\n")
    _config(new["rtr-1"], new_source, f"snmp-server community {COMMUNITY_K2} RO\n")
    assert Comparison(acme, q2, q4).diffs == []


def test_screen_shows_the_comparison(client, engineer, pair):
    q2, q4 = pair
    page = client.get(_url(q4)).content.decode()
    assert "Compare engagements" in page
    assert "comparisons stay within one customer" in page
    # The baseline defaults to the engagement before the current one.
    assert f'<option value="{q2.pk}" selected>' in page
    for label in ("Devices added", "Devices no longer seen", "OS version changes"):
        assert label in page
    assert "Changed configs · 1" in page
    assert "secret changed" in page and "key rotated" in page
    assert "2222222222222222" in page
    assert 'aria-current="page"' in page


def test_tiles_pick_the_group_and_diff_lines_can_hide(client, engineer, pair):
    q2, q4 = pair
    page = client.get(_url(q4, f"?baseline={q2.pk}&group=os")).content.decode()
    assert "17.6.5 → 17.9.4a" in page
    assert "In the baseline, not found" not in page
    full = client.get(_url(q4, f"?baseline={q2.pk}")).content.decode()
    changes = client.get(_url(q4, f"?baseline={q2.pk}&changes=1")).content.decode()
    assert "hostname core-sw-01" in full
    assert "hostname core-sw-01" not in changes
    assert "ntp server 192.0.2.123" in changes


def test_one_engagement_gets_an_empty_state(client, engineer, acme):
    only = _engagement(acme, "Q4 review", (2026, 10, 28))
    page = client.get(_url(only)).content.decode()
    assert "Nothing to compare yet" in page
    assert "Acme has one engagement, Q4 review." in page


def test_first_engagement_asks_for_another(client, engineer, pair):
    q2, _ = pair
    page = client.get(_url(q2)).content.decode()
    assert "is Acme's first engagement" in page
    assert "Changed configs" not in page


def test_picking_the_current_engagement_moves_to_its_url(client, engineer, pair):
    q2, q4 = pair
    response = client.get(_url(q2, f"?baseline={q2.pk}&current={q4.pk}"))
    assert response.status_code == 302
    assert response["Location"] == _url(q4, f"?baseline={q2.pk}")


def test_compare_stays_within_one_customer(client, engineer, acme, pair):
    q2, q4 = pair
    other = Customer.objects.create(name="Globex")
    theirs = _engagement(other, "Their review", (2026, 5, 1))
    assert client.get(_url(q4, f"?baseline={theirs.pk}")).status_code == 404
    assert client.get(_url(q4, f"?current={theirs.pk}")).status_code == 404
    assert client.get(f"/customers/{other.pk}/engagements/{q4.pk}/compare").status_code == 404
    with pytest.raises(CrossCustomerError):
        Comparison(acme, theirs, q4)
    with pytest.raises(CrossCustomerError):
        Comparison(other, q2, q4)
    response = client.post(
        f"/customers/{other.pk}/engagements/{q4.pk}/compare/export",
        {"baseline": q2.pk, "device": "core-sw-01"},
    )
    assert response.status_code == 404


def test_export_is_masked_and_audit_logged(client, engineer, acme, pair):
    q2, q4 = pair
    response = client.post(_url(q4, "/export"), {"baseline": q2.pk, "device": "core-sw-01"})
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith(
        'attachment; filename="cartio-diff-Acme-Q4-review-core-sw-01-'
    )
    text = response.content.decode()
    assert "--- Q2 review/core-sw-01" in text
    assert "+++ Q4 review/core-sw-01" in text
    assert f"+username netops privilege 15 password {NEW_PW}" in text
    assert "-ip http server" in text
    assert "# Cartio config diff · Acme · core-sw-01" in text
    event = AuditEvent.objects.for_customer(acme).get(action="compare.exported")
    assert event.actor == engineer.get_username()
    assert event.object_name == "core-sw-01"
    assert event.detail["baseline"] == q2.pk and event.detail["current"] == q4.pk


def test_export_needs_a_changed_device(client, engineer, acme, pair):
    q2, q4 = pair
    response = client.post(_url(q4, "/export"), {"baseline": q2.pk, "device": "core-sw-02"})
    assert response.status_code == 400
    assert not AuditEvent.objects.for_customer(acme).filter(action="compare.exported").exists()


def test_on_site_customers(client, engineer):
    locked = Customer.objects.create(name="Ostrander", data_mode=Customer.DataMode.ON_SITE)
    plant = _engagement(locked, "Plant review", (2026, 9, 1))
    _engagement(locked, "Plant 2025", (2025, 9, 1))
    page = client.get(_url(plant)).content.decode()
    assert "inside access only" in page.lower()
    assert "Changed configs" not in page
    live = Customer.objects.create(
        name="Bluepeak", data_mode=Customer.DataMode.ON_SITE, live_view_allowed=True
    )
    annual = _engagement(live, "Annual 2026", (2026, 9, 1))
    older = _engagement(live, "Annual 2025", (2025, 9, 1))
    page = client.get(_url(annual)).content.decode()
    assert "configs stay in its on-site store" in page
    assert "Export .diff" not in page
    response = client.post(_url(annual, "/export"), {"baseline": older.pk, "device": "x"})
    assert response.status_code == 404
