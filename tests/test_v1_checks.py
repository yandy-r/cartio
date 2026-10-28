"""The v1 assessment checks on the lab's sanitized configs (YAN-1665)."""

from datetime import date

import pytest
from cartio_core import checks, lifecycle
from cartio_core.checks import CHECKS, DeviceFacts, Status, run_checks
from cartio_core.config_facts import read_config

from tests.test_parsers import FIXTURES

CONFIG_CHECKS = ["insecure-management", "ntp-syslog", "snmpv3"]


def _config(platform, name, file="config.cfg"):
    return (FIXTURES / platform / name / file).read_text(encoding="utf-8")


# The lab: two hardened configs (IOS-XE and FortiOS) and two weak ones (IOS and FortiOS).
CORE = DeviceFacts(
    "core-sw1", "ios-xe", "Cisco", "C9300-48P", "17.9.4a", _config("ios-xe", "core-sw1")
)
EDGE = DeviceFacts(
    "edge-fw1", "fortios", "Fortinet", "FortiGate-60F", "7.2.8", _config("fortios", "edge-fw1")
)
FLOOR = DeviceFacts(
    "floor-sw1", "ios", "Cisco", "WS-C2960X-48FPD-L", "15.2(7)E8", _config("ios", "floor-sw1")
)
EDGE_WEAK = DeviceFacts(
    "edge-fw2",
    "fortios",
    "Fortinet",
    "FortiGate-60F",
    "7.2.8",
    _config("fortios", "edge-fw1", "config-weak.cfg"),
)


def _outcomes(devices, check_ids):
    return {
        (r.device, r.check.id): r.outcome
        for r in run_checks(devices, [CHECKS[c] for c in check_ids])
    }


@pytest.fixture
def on(monkeypatch):
    def set_today(day):
        monkeypatch.setattr(checks, "today", lambda: day)

    return set_today


@pytest.mark.parametrize("check_id", CONFIG_CHECKS)
def test_hardened_configs_pass(check_id):
    outcomes = _outcomes([CORE, EDGE], [check_id])
    assert {key: o.status for key, o in outcomes.items()} == {
        ("core-sw1", check_id): Status.PASS,
        ("edge-fw1", check_id): Status.PASS,
    }


@pytest.mark.parametrize("device", [FLOOR, EDGE_WEAK], ids=["ios", "fortios"])
@pytest.mark.parametrize("check_id", CONFIG_CHECKS)
def test_weak_configs_fail(check_id, device):
    outcome = _outcomes([device], [check_id])[(device.name, check_id)]
    assert outcome.status == Status.FAIL
    assert outcome.evidence


def test_insecure_management_evidence():
    outcomes = _outcomes([FLOOR, EDGE_WEAK], ["insecure-management"])
    assert outcomes[("floor-sw1", "insecure-management")].evidence == (
        "line vty 0 4 accepts Telnet.",
        "The HTTP server is on (ip http server).",
        "An SNMPv1/v2c community has no access list.",
        "An SNMPv1/v2c community has write (RW) access.",
        "An SNMPv1/v2c community has no access list.",
        "line vty 5 15 doesn't set transport input.",
    )
    assert outcomes[("edge-fw2", "insecure-management")].evidence == (
        "internal allows Telnet admin access.",
        "wan1 allows HTTP admin access.",
        "internal allows HTTP admin access.",
    )


def test_evidence_never_shows_a_masked_value():
    for result in run_checks([CORE, EDGE, FLOOR, EDGE_WEAK]):
        for line in result.outcome.evidence:
            assert "<masked:" not in line


def test_vty_lines_without_transport_input_are_unknown_not_pass():
    config = "hostname sw\nline vty 0 4\n login local\nntp server 192.0.2.10\n"
    device = DeviceFacts("sw", "ios-xe", config=config)
    outcome = CHECKS["insecure-management"].run(device, [device])
    assert outcome.status == Status.UNKNOWN


def test_missing_config_is_unknown_never_pass():
    device = DeviceFacts("sw", "ios", "Cisco", "C9300-48P", "17.9.4a")
    for check_id in CONFIG_CHECKS:
        assert CHECKS[check_id].run(device, [device]).status == Status.UNKNOWN


def test_ntp_and_syslog_must_match_across_devices():
    other = DeviceFacts(
        "core-sw2",
        "ios-xe",
        config=CORE.config.replace("192.0.2.11", "192.0.2.12"),
    )
    outcomes = _outcomes([CORE, EDGE, other], ["ntp-syslog"])
    core = outcomes[("core-sw1", "ntp-syslog")]
    assert core.status == Status.FAIL
    assert core.evidence == (
        "NTP: 192.0.2.10, 192.0.2.11.",
        "Syslog: 192.0.2.20.",
        "core-sw2 uses different NTP servers. Theirs: 192.0.2.10, 192.0.2.12.",
    )
    assert outcomes[("edge-fw1", "ntp-syslog")].status == Status.FAIL
    # A device whose config was not collected is left out of the comparison.
    assert _outcomes([CORE, DeviceFacts("x", "ios")], ["ntp-syslog"])[
        ("core-sw1", "ntp-syslog")
    ].status == (Status.PASS)


def test_fortios_ntp_settings_left_out_of_the_config_are_unknown():
    device = DeviceFacts(
        "fw",
        "fortios",
        config='config log syslogd setting\nset status enable\nset server "192.0.2.20"\nend\n',
    )
    assert CHECKS["ntp-syslog"].run(device, [device]).status == Status.UNKNOWN
    # FortiGuard's servers by default.
    facts = read_config("fortios", "config system ntp\n    set ntpsync enable\nend\n")
    assert facts.time_and_logging.ntp == {"fortiguard"}


def test_snmp_off_is_not_applicable():
    device = DeviceFacts("sw", "ios", config="hostname sw\nntp server 192.0.2.10\n")
    assert CHECKS["snmpv3"].run(device, [device]).status == Status.NOT_APPLICABLE


def test_snmpv3_evidence():
    outcomes = _outcomes([FLOOR, EDGE_WEAK, CORE], ["snmpv3"])
    assert outcomes[("floor-sw1", "snmpv3")].evidence == (
        "SNMP community with RO access.",
        "SNMP community with RW access.",
    )
    assert outcomes[("edge-fw2", "snmpv3")].evidence == (
        "SNMP community 1.",
        "SNMPv3 user legacy uses no-auth-no-priv.",
    )
    assert outcomes[("core-sw1", "snmpv3")] == checks.passed("SNMPv3 group MONITOR uses priv.")


def test_ios_community_access_lists():
    facts = read_config(
        "ios",
        "snmp-server community <masked:k1:0011223344556677> RO SNMP-POLLERS\n"
        "snmp-server community <masked:k1:0011223344556688> view V RO ROUTERS\n",
    )
    assert facts.management.snmp_communities == ()


# End of life.


def test_eol_on_the_lab(on):
    on(date(2026, 10, 28))
    outcomes = _outcomes([CORE, EDGE, FLOOR], ["eol-eos"])
    assert outcomes[("core-sw1", "eol-eos")] == checks.failed(
        "ios-xe 17.9 stopped getting security fixes on 2026-09-30.",
        "No end of life announced for C9300-48P (checked 2026-10-28).",
    )
    assert outcomes[("edge-fw1", "eol-eos")] == checks.failed(
        "fortios 7.2 reached end of support on 2026-09-30.",
        "FortiGate-60F is sold until 2027-01-13, supported until 2032-01-13.",
    )
    assert outcomes[("floor-sw1", "eol-eos")] == checks.passed(
        "WS-C2960X-48FPD-L is sold until 2022-10-31, supported until 2027-10-31.",
        "ios 15.2(7)E is supported until 2027-10-31.",
    )
    on(date(2027, 11, 1))
    assert CHECKS["eol-eos"].run(FLOOR, [FLOOR]) == checks.failed(
        "WS-C2960X-48FPD-L reached end of support on 2027-10-31.",
        "ios 15.2(7)E reached end of support on 2027-10-31.",
    )


def test_eol_passes_before_the_milestones(on):
    on(date(2024, 1, 1))
    assert CHECKS["eol-eos"].run(CORE, [CORE]).status == Status.PASS


def test_eol_unknown_when_not_in_the_data_or_not_collected(on):
    on(date(2026, 10, 28))
    unlisted = DeviceFacts("sw", "ios-xe", "Cisco", "C9500-48Y4C", "17.12.4")
    outcome = CHECKS["eol-eos"].run(unlisted, [unlisted])
    assert outcome.status == Status.UNKNOWN
    assert "C9500-48Y4C isn't in the end-of-life data (2026-10-28)." in outcome.evidence
    blank = DeviceFacts("sw", "ios-xe")
    assert CHECKS["eol-eos"].run(blank, [blank]).status == Status.UNKNOWN
    # A past milestone still fails when the other half is unknown.
    half = DeviceFacts("fw", "fortios", "Fortinet", "FortiGate-999Z", "7.2.8")
    assert CHECKS["eol-eos"].run(half, [half]).status == Status.FAIL


def test_release_trains():
    assert lifecycle.release_train("17.09.04a") == "17.9"
    assert lifecycle.release_train("17.9.4a") == "17.9"
    assert lifecycle.release_train("v7.2.8,build1639") == "7.2"
    assert lifecycle.release_train("15.2(7)E8") == "15.2(7)E"
    assert lifecycle.release_train("unknown") == ""


def test_lifecycle_data_covers_the_lab_and_names_its_sources():
    data = lifecycle.load()
    assert data.version
    for device in (CORE, EDGE, FLOOR):
        assert data.for_model(device.model), device.model
        assert data.for_software(device.platform, device.os_version), device.os_version
    for _, entry in data.hardware:
        assert entry.source and entry.checked
    for _, _, entry in data.software:
        assert entry.source and entry.checked
