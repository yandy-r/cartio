"""Assessment checks: a fixed list of rules run against one engagement's devices (YAN-1664).

A check looks at one device, with the rest of the engagement's devices for context, and returns
pass, fail, not applicable or unknown, with the evidence it based that on. Missing data is
always "unknown", never "pass": a check that can't see what it needs says so.

Checks are plain code for v1. They read the normalized model (`DeviceFacts`), never a device, so
they run the same on the controller or, for On site customers, next to the customer's own store.
A suggested config snippet is text for the report only: nothing ever pushes it (ADR-0004).

Adding a check: write a function `(device, engagement) -> Outcome` and add it to `CHECKS` with
`Check(...)`. Its id is stored with every result, so never reuse or rename one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from . import lifecycle
from .config_facts import ConfigFacts, read_config
from .masking import Platform

logger = logging.getLogger(__name__)

# Evidence is for people reading a finding, so keep it short.
MAX_EVIDENCE = 20


class Status(StrEnum):
    PASS = "pass"  # noqa: S105 - a check status, not a password
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass(frozen=True)
class DeviceFacts:
    """What the checks may read about one device, as found in one engagement.

    Blank or None means not collected. Configs and outputs are masked (ADR-0003).
    """

    name: str
    platform: str = ""
    manufacturer: str = ""
    model: str = ""
    os_version: str = ""
    # The latest config snapshot, or None when no config was collected.
    config: str | None = None
    # The latest output of each allow-listed command, by command.
    outputs: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    status: Status
    evidence: tuple[str, ...] = ()


def passed(*evidence: str) -> Outcome:
    return Outcome(Status.PASS, evidence)


def failed(*evidence: str) -> Outcome:
    return Outcome(Status.FAIL, evidence)


def unknown(*evidence: str) -> Outcome:
    return Outcome(Status.UNKNOWN, evidence)


def not_applicable(*evidence: str) -> Outcome:
    return Outcome(Status.NOT_APPLICABLE, evidence)


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    severity: Severity
    # Written for the customer: what to do about a failure.
    recommendation: str
    logic: Callable[[DeviceFacts, Sequence[DeviceFacts]], Outcome]
    # The platforms it applies to. Empty means every platform.
    platforms: frozenset[Platform] = frozenset()
    # Text for the report only. Never sent to a device (ADR-0004).
    suggested_config: str = ""

    def run(self, device: DeviceFacts, engagement: Sequence[DeviceFacts]) -> Outcome:
        if self.platforms:
            if not device.platform:
                return unknown("The device's platform wasn't identified.")
            if device.platform not in self.platforms:
                return not_applicable(f"Doesn't apply to {device.platform}.")
        try:
            outcome = self.logic(device, engagement)
            if not isinstance(outcome, Outcome):
                raise TypeError(f"Check {self.id} returned {outcome!r}, not an Outcome.")
            return Outcome(Status(outcome.status), tuple(outcome.evidence[:MAX_EVIDENCE]))
        except Exception:
            # One broken check mustn't stop the others, and it mustn't pass a device either.
            logger.exception("Check %s failed on %s", self.id, device.name)
            return unknown("The check couldn't run on this device's data.")


@dataclass(frozen=True)
class Result:
    device: str
    check: Check
    outcome: Outcome


def run_checks(
    devices: Sequence[DeviceFacts], checks: Iterable[Check] | None = None
) -> list[Result]:
    """Run each check on each device of one engagement."""
    checks = list(CHECKS.values() if checks is None else checks)
    if len({check.id for check in checks}) != len(checks):
        raise ValueError("Each check may run only once.")
    return [
        Result(device.name, check, check.run(device, devices))
        for device in devices
        for check in checks
    ]


# The checks: the v1 list (YAN-1665).


def today() -> date:
    """The date end-of-life milestones are compared with. Tests replace it."""
    return date.today()


def _milestones(what: str, entry: lifecycle.Lifecycle, now: date) -> tuple[list[str], list[str]]:
    """Evidence that a milestone has passed, and evidence about the ones still to come."""
    # Each date is the last day of what it names, so it has passed only the day after.
    past, notes = [], []
    if entry.last_date_of_support and entry.last_date_of_support < now:
        past.append(f"{what} reached end of support on {entry.last_date_of_support}.")
    elif entry.end_of_security_fixes and entry.end_of_security_fixes < now:
        past.append(f"{what} stopped getting security fixes on {entry.end_of_security_fixes}.")
    else:
        dates = [
            f"{label} {day}"
            for label, day in (
                ("sold until", entry.end_of_sale),
                ("getting security fixes until", entry.end_of_security_fixes),
                ("supported until", entry.last_date_of_support),
            )
            if day
        ]
        if dates:
            notes.append(f"{what} is {', '.join(dates)}.")
        else:
            notes.append(f"No end of life announced for {what} (checked {entry.checked}).")
    return past, notes


def _eol_eos(device: DeviceFacts, engagement: Sequence[DeviceFacts]) -> Outcome:
    data = lifecycle.load()
    now = today()
    past, notes, missing = [], [], []
    if not device.model:
        missing.append("The device's model wasn't collected.")
    elif hardware := data.for_model(device.model):
        found, note = _milestones(device.model, hardware, now)
        past += found
        notes += note
    else:
        missing.append(f"{device.model} isn't in the end-of-life data ({data.version}).")
    if not device.os_version:
        missing.append("The device's OS version wasn't collected.")
    elif software := data.for_software(device.platform, device.os_version):
        what = f"{device.platform} {lifecycle.release_train(device.os_version)}"
        found, note = _milestones(what, software, now)
        past += found
        notes += note
    else:
        missing.append(
            f"{device.platform or 'This platform'} {device.os_version} isn't in the end-of-life "
            f"data ({data.version})."
        )
    if past:
        return failed(*past, *notes, *missing)
    if missing:
        return unknown(*missing, *notes)
    return passed(*notes)


def _facts(device: DeviceFacts) -> ConfigFacts | None:
    # An empty collection is no evidence of a clean device.
    if device.config is None or not device.config.strip() or not device.platform:
        return None
    return read_config(device.platform, device.config)


def _insecure_management(device: DeviceFacts, engagement: Sequence[DeviceFacts]) -> Outcome:
    facts = _facts(device)
    if facts is None:
        return unknown("The device's config wasn't collected.")
    access = facts.management
    problems = [*access.telnet, *access.http, *access.snmp_communities]
    if problems:
        return failed(*problems, *access.undetermined)
    if access.undetermined:
        return unknown(*access.undetermined)
    return passed("No Telnet, HTTP admin access or open SNMP write access found.")


def _servers(kind: str, servers: frozenset[str]) -> str:
    return f"{kind}: {', '.join(sorted(servers))}." if servers else f"{kind}: none."


def _ntp_syslog(device: DeviceFacts, engagement: Sequence[DeviceFacts]) -> Outcome:
    facts = _facts(device)
    if facts is None:
        return unknown("The device's config wasn't collected.")
    mine = facts.time_and_logging
    problems, notes = [], []
    if mine.ntp is None:
        notes.append("The config doesn't include the NTP settings.")
    elif not mine.ntp:
        problems.append("No NTP server is configured.")
    if not mine.syslog:
        problems.append("No syslog server is configured.")
    # Compared with every other device whose config says, so each device's finding names the rest.
    for other in engagement:
        if other is device or other.name == device.name:
            continue
        theirs = _facts(other)
        if theirs is None:
            continue
        theirs = theirs.time_and_logging
        if mine.ntp and theirs.ntp and theirs.ntp != mine.ntp:
            problems.append(
                f"{other.name} uses different NTP servers. {_servers('Theirs', theirs.ntp)}"
            )
        if mine.syslog and theirs.syslog and theirs.syslog != mine.syslog:
            problems.append(
                f"{other.name} uses different syslog servers. {_servers('Theirs', theirs.syslog)}"
            )
    own = []
    if mine.ntp is not None:
        own.append(_servers("NTP", mine.ntp))
    own.append(_servers("Syslog", mine.syslog))
    if problems:
        return failed(*own, *problems, *notes)
    if notes:
        return unknown(*notes, *own)
    return passed(*own)


def _snmpv3(device: DeviceFacts, engagement: Sequence[DeviceFacts]) -> Outcome:
    facts = _facts(device)
    if facts is None:
        return unknown("The device's config wasn't collected.")
    snmp = facts.snmp
    if not snmp.enabled:
        return not_applicable("SNMP isn't configured.")
    if snmp.v1_v2c or snmp.v3_without_auth:
        return failed(*snmp.v1_v2c, *snmp.v3_without_auth, *snmp.v3)
    return passed(*snmp.v3)


def _firmware_drift(device: DeviceFacts, engagement: Sequence[DeviceFacts]) -> Outcome:
    if not device.model:
        return unknown("The device's model wasn't collected.")
    if not device.os_version:
        return unknown("The device's OS version wasn't collected.")
    # By model alone: a device whose manufacturer wasn't collected still counts.
    same_model = [other for other in engagement if other.model == device.model]
    different = sorted(
        (other.name, other.os_version)
        for other in same_model
        if other.os_version and other.os_version != device.os_version
    )
    if different:
        return failed(
            f"{device.name} runs {device.os_version}.",
            *(f"{name} ({device.model}) runs {version}." for name, version in different),
        )
    if len(same_model) == 1:
        return passed(f"No other {device.model} in this engagement.")
    return passed(f"Every {device.model} with a known version runs {device.os_version}.")


CHECKS: dict[str, Check] = {
    check.id: check
    for check in (
        Check(
            id="firmware-drift",
            title="Same model, different OS versions",
            severity=Severity.MEDIUM,
            recommendation=(
                "Devices of the same model run different software versions. Choose one approved "
                "version per model and upgrade the others to it, so they behave the same and get "
                "the same fixes."
            ),
            logic=_firmware_drift,
        ),
        Check(
            id="eol-eos",
            title="Hardware or software past end of support",
            severity=Severity.HIGH,
            recommendation=(
                "This device, or the software it runs, no longer gets security fixes or vendor "
                "support. Plan to replace the hardware or upgrade to a supported release, and "
                "until then limit who can reach its management interfaces."
            ),
            logic=_eol_eos,
        ),
        Check(
            id="insecure-management",
            title="Insecure management protocols",
            severity=Severity.HIGH,
            recommendation=(
                "The device can be managed over protocols that send passwords and settings in "
                "clear text, or lets SNMP change its settings from anywhere. Turn off Telnet and "
                "HTTP administration and use SSH and HTTPS instead. Remove SNMP write access, and "
                "limit SNMP to your monitoring servers with an access list."
            ),
            logic=_insecure_management,
            platforms=frozenset(Platform),
        ),
        Check(
            id="ntp-syslog",
            title="NTP and syslog missing or inconsistent",
            severity=Severity.MEDIUM,
            recommendation=(
                "Accurate, shared time and central logging are what make it possible to trace an "
                "incident across devices. Point every device at the same NTP servers and the "
                "same syslog servers."
            ),
            logic=_ntp_syslog,
            platforms=frozenset(Platform),
        ),
        Check(
            id="snmpv3",
            title="SNMPv3 not in use",
            severity=Severity.MEDIUM,
            recommendation=(
                "SNMP versions 1 and 2c send their community string, which works like a "
                "password, in clear text. Move monitoring to SNMPv3 with authentication and "
                "encryption (authPriv), then remove the old communities."
            ),
            logic=_snmpv3,
            platforms=frozenset(Platform),
        ),
    )
}
