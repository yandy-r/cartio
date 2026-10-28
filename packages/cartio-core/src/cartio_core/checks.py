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
from enum import StrEnum

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
        except Exception:
            # One broken check mustn't stop the others, and it mustn't pass a device either.
            logger.exception("Check %s failed on %s", self.id, device.name)
            return unknown("The check couldn't run on this device's data.")
        if not isinstance(outcome, Outcome):
            raise TypeError(f"Check {self.id} returned {outcome!r}, not an Outcome.")
        return Outcome(Status(outcome.status), tuple(outcome.evidence[:MAX_EVIDENCE]))


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
    return [
        Result(device.name, check, check.run(device, devices))
        for device in devices
        for check in checks
    ]


# The checks. YAN-1665 adds the rest of the v1 list.


def _firmware_drift(device: DeviceFacts, engagement: Sequence[DeviceFacts]) -> Outcome:
    if not device.model:
        return unknown("The device's model wasn't collected.")
    if not device.os_version:
        return unknown("The device's OS version wasn't collected.")
    same_model = [
        other
        for other in engagement
        if other.manufacturer == device.manufacturer and other.model == device.model
    ]
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
    )
}
