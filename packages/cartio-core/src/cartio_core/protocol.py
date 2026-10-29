"""Messages exchanged between a collector and the controller.

Both sides import these, so the shape of a message is defined once. All traffic is
started by the collector (ADR-0001): it enrolls once, then checks in and asks for jobs.

Enrollment exchanges a one-time token and the collector's public keys for its identity. Every
request after that is signed with the collector's private key (`cartio_core.signing`, ADR-0009).

Jobs (ADR-0009): the collector long-polls `JOBS_NEXT_PATH`, which the controller holds open
until a job is queued or `MAX_WAIT` passes. It reports progress, uploads the result in chunks
(each acknowledged, so an upload resumes after a dropped connection), then finishes the job
with a result id it made once. The controller stores a result id once, so a replay after a
reconnect doesn't duplicate anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

API_PREFIX = "/api/v1"
ENROLL_PATH = f"{API_PREFIX}/enroll"
CHECKIN_PATH = f"{API_PREFIX}/checkin"
JOBS_NEXT_PATH = f"{API_PREFIX}/jobs/next"
# The longest the controller holds a job poll open: under common proxy idle timeouts.
MAX_WAIT = 25
# Results upload in chunks of at most this many bytes.
CHUNK_BYTES = 256 * 1024
MAX_CHUNK_BYTES = 1024 * 1024
MAX_CHUNKS = 1024

# The kinds of job a controller can queue (YAN-1651).
JOB_TYPES = ("discover", "collect", "import_parse", "assess", "health")
# How a collector says a job ended. Cancelling is the controller's to record.
JOB_OUTCOMES = ("succeeded", "partial", "failed")


# A collect job's sealed secrets hold, besides its device credentials, one entry of this kind:
# the customer's masking key, `{"kind": MASKING_KEY_KIND, "version": ..., "secret": base64}`.
# It travels only that way, so it never leaves the controller unsealed (ADR-0003).
MASKING_KEY_KIND = "masking_key"
# The platforms a collect job can reach over SSH, and how many devices one job may name.
COLLECT_PLATFORMS = ("ios", "ios-xe", "fortios")
MAX_COLLECT_TARGETS = 500


def job_seal_context(job_id: str) -> bytes:
    """What a job's sealed credentials are bound to, so they can't be moved into another job."""
    return f"cartio-job:{job_id}".encode()


def job_path(job_id: str, action: str) -> str:
    """The path for `action` ("progress", "result" or "chunks/<n>") on one job."""
    return f"{API_PREFIX}/jobs/{job_id}/{action}"


class ProtocolError(ValueError):
    """A message is missing a field or has a field of the wrong type."""


def _require(data: dict[str, Any], key: str, kind: type) -> Any:
    if key not in data:
        raise ProtocolError(f"missing field: {key}")
    value = data[key]
    if not isinstance(value, kind):
        raise ProtocolError(f"field {key} must be {kind.__name__}")
    return value


@dataclass(frozen=True)
class EnrollRequest:
    token: str
    hostname: str
    # The collector's Ed25519 public key. The private key never leaves the collector host.
    public_key: str
    # The collector's X25519 public key, which job credentials are sealed to
    # (`cartio_core.sealing`).
    seal_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrollRequest:
        return cls(
            token=_require(data, "token", str),
            hostname=_require(data, "hostname", str),
            public_key=_require(data, "public_key", str),
            seal_key=_require(data, "seal_key", str),
        )


@dataclass(frozen=True)
class EnrollResponse:
    collector_id: str
    customer: str
    # Pins of the controller's TLS server keys (`signing.spki_pin`). Empty if the controller
    # doesn't publish any, and the collector then pins the key it saw when it enrolled.
    tls_pins: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrollResponse:
        pins = _require(data, "tls_pins", list)
        if not all(isinstance(pin, str) for pin in pins):
            raise ProtocolError("field tls_pins must be a list of strings")
        return cls(
            collector_id=_require(data, "collector_id", str),
            customer=_require(data, "customer", str),
            tls_pins=pins,
        )


@dataclass(frozen=True)
class CheckIn:
    hostname: str
    version: str
    platform: str
    # The controller's TLS key didn't match the collector's pin: something on the path, such
    # as a TLS-inspecting proxy, re-signed the connection. Reported, not blocked (ADR-0009).
    tls_inspected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckIn:
        return cls(
            hostname=_require(data, "hostname", str),
            version=_require(data, "version", str),
            platform=_require(data, "platform", str),
            tls_inspected=_require(data, "tls_inspected", bool),
        )


@dataclass(frozen=True)
class CheckInResponse:
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckInResponse:
        return cls(status=_require(data, "status", str))


@dataclass(frozen=True)
class JobPoll:
    """Ask for the next job, waiting up to `wait` seconds (at most `MAX_WAIT`) for one."""

    wait: int
    tls_inspected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobPoll:
        wait = _require(data, "wait", int)
        if isinstance(wait, bool) or not 0 <= wait <= MAX_WAIT:
            raise ProtocolError(f"field wait must be 0 to {MAX_WAIT}")
        return cls(wait=wait, tls_inspected=_require(data, "tls_inspected", bool))


@dataclass(frozen=True)
class Job:
    id: str
    type: str
    engagement: int | None = None
    # Settings for the job. Never a secret: those travel sealed, in `credentials`.
    params: dict[str, Any] = field(default_factory=dict)
    # The job's device credentials as JSON, sealed to this collector with the job id as
    # context (`cartio_core.sealing`). Empty when the job needs none.
    credentials: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        engagement = data.get("engagement")
        if engagement is not None and (
            isinstance(engagement, bool) or not isinstance(engagement, int)
        ):
            raise ProtocolError("field engagement must be int or null")
        return cls(
            id=_require(data, "id", str),
            type=_require(data, "type", str),
            engagement=engagement,
            params=_require(data, "params", dict),
            credentials=_require(data, "credentials", str),
        )


@dataclass(frozen=True)
class JobPollResponse:
    jobs: list[Job] = field(default_factory=list)
    # The customer's "Require uninspected path" setting: when set, the collector refuses to
    # run jobs while it sees TLS inspection (ADR-0009).
    require_uninspected_path: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobPollResponse:
        jobs = _require(data, "jobs", list)
        if not all(isinstance(job, dict) for job in jobs):
            raise ProtocolError("field jobs must be a list of objects")
        return cls(
            jobs=[Job.from_dict(job) for job in jobs],
            require_uninspected_path=_require(data, "require_uninspected_path", bool),
        )


@dataclass(frozen=True)
class JobProgress:
    message: str
    percent: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobProgress:
        percent = data.get("percent")
        if percent is not None and (
            isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100
        ):
            raise ProtocolError("field percent must be 0 to 100 or null")
        return cls(message=_require(data, "message", str), percent=percent)


@dataclass(frozen=True)
class JobStatus:
    """The controller's answer to progress: whether an engineer cancelled the job."""

    cancelled: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobStatus:
        return cls(cancelled=_require(data, "cancelled", bool))


@dataclass(frozen=True)
class JobResult:
    """How a job ended. Its result, if any, was uploaded first as `chunks` chunks."""

    # Made once by the collector. The controller stores a result id once.
    result_id: str
    outcome: str
    chunks: int = 0
    # SHA-256 of the whole result, the chunks joined in order.
    sha256: str = ""
    device_count: int = 0
    # A short reason for people reading the log. Never device output or a secret.
    error_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobResult:
        outcome = _require(data, "outcome", str)
        if outcome not in JOB_OUTCOMES:
            raise ProtocolError(f"field outcome must be one of {', '.join(JOB_OUTCOMES)}")
        chunks = _require(data, "chunks", int)
        if isinstance(chunks, bool) or not 0 <= chunks <= MAX_CHUNKS:
            raise ProtocolError(f"field chunks must be 0 to {MAX_CHUNKS}")
        device_count = _require(data, "device_count", int)
        if isinstance(device_count, bool) or not 0 <= device_count <= 2**31 - 1:
            raise ProtocolError("field device_count is out of range")
        return cls(
            result_id=_require(data, "result_id", str),
            outcome=outcome,
            chunks=chunks,
            sha256=_require(data, "sha256", str),
            device_count=device_count,
            error_summary=_require(data, "error_summary", str),
        )


@dataclass(frozen=True)
class CollectedDevice:
    """One device a collect job reached: its masked outputs, by command, and masked config."""

    host: str
    platform: str
    outputs: dict[str, str]
    config: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollectedDevice:
        outputs = _require(data, "outputs", dict)
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in outputs.items()):
            raise ProtocolError("field outputs must map commands to text")
        platform = _require(data, "platform", str)
        if platform not in COLLECT_PLATFORMS:
            raise ProtocolError(f"field platform must be one of {', '.join(COLLECT_PLATFORMS)}")
        return cls(
            host=_require(data, "host", str),
            platform=platform,
            outputs=outputs,
            config=_require(data, "config", str),
        )


@dataclass(frozen=True)
class CollectFailure:
    """A device a collect job couldn't reach or read. `error` is the kind of error only."""

    host: str
    error: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollectFailure:
        return cls(host=_require(data, "host", str), error=_require(data, "error", str))


@dataclass(frozen=True)
class CollectResult:
    """A collect job's uploaded result, as JSON. Everything in it is masked with `key_version`."""

    key_version: str
    devices: list[CollectedDevice] = field(default_factory=list)
    failed: list[CollectFailure] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollectResult:
        devices = _require(data, "devices", list)
        failed = _require(data, "failed", list)
        if not all(isinstance(d, dict) for d in devices + failed):
            raise ProtocolError("fields devices and failed must be lists of objects")
        return cls(
            key_version=_require(data, "key_version", str),
            devices=[CollectedDevice.from_dict(d) for d in devices],
            failed=[CollectFailure.from_dict(f) for f in failed],
        )
