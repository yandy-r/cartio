"""Running jobs the controller queues (YAN-1651, ADR-0009).

The collector long-polls for a job, runs it, uploads the result in acknowledged chunks and then
reports how it ended. A local journal makes this safe across dropped connections and restarts:

- A job id is written to the journal before the job runs, so a job the controller hands over
  again after a reconnect is never run twice. One that was interrupted is reported as failed.
- A finished job's result stays on disk with the last acknowledged chunk, so an upload resumes
  where it stopped, and its result id is made once, so the controller stores it once.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cartio_core import sealing
from cartio_core.protocol import CHUNK_BYTES, MAX_WAIT, Job, JobResult, job_seal_context

from . import __version__
from .client import JobClosed, Session

# Attempts for each request before a dropped connection is given up on, and the pause between.
ATTEMPTS = 5
RETRY_DELAY = 1.0
# Reported jobs remembered, so one handed over again is still recognised.
KEEP_REPORTED = 500


class Cancelled(Exception):
    """An engineer cancelled the job while it ran."""


@dataclass(frozen=True)
class Outcome:
    outcome: str
    payload: bytes = b""
    device_count: int = 0
    error_summary: str = ""


Report = Callable[[str, "int | None"], None]


def _health(job: Job, credentials: list[dict[str, Any]], report: Report) -> Outcome:
    """Say how this collector is: enough to check the channel end to end."""
    report("Checking the collector", 50)
    payload = {
        "hostname": socket.gethostname(),
        "version": __version__,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
    }
    return Outcome("succeeded", json.dumps(payload).encode())


# The job types this collector version can run. Others are reported as failed, not dropped.
HANDLERS: dict[str, Callable[[Job, list[dict[str, Any]], Report], Outcome]] = {"health": _health}


class Journal:
    """The jobs this collector has started, in a file only its owner can read."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory
        self.path = directory / "journal.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._load().get(job_id)

    def put(self, job_id: str, entry: dict[str, Any]) -> None:
        entries = self._load()
        entries.pop(job_id, None)
        entries[job_id] = entry
        reported = [k for k, v in entries.items() if v["state"] == "reported"]
        for old in reported[:-KEEP_REPORTED]:
            del entries[old]
        # Written whole and swapped in, so a crash never leaves half a journal.
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def payload_path(self, job_id: str) -> Path:
        return self.directory / f"{uuid.UUID(job_id)}.result"


class Worker:
    """Fetches jobs over a session and runs them one at a time."""

    def __init__(self, session: Session, journal: Journal) -> None:
        self.session = session
        self.journal = journal

    def _call(self, request, *args):
        """Send one request, again after a dropped connection: every request is safe to repeat."""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return request(*args)
            except httpx.TransportError as exc:
                if attempt == ATTEMPTS:
                    raise
                print(f"warning: connection dropped, retrying: {exc}", file=sys.stderr)
                time.sleep(RETRY_DELAY * attempt)
        raise AssertionError("unreachable")

    def poll_once(self, wait: int = MAX_WAIT) -> int:
        """Wait up to `wait` seconds for jobs and run them. Returns how many arrived."""
        answer = self._call(self.session.poll, wait)
        for job in answer.jobs:
            self.handle(job, answer.require_uninspected_path)
        return len(answer.jobs)

    def handle(self, job: Job, require_uninspected_path: bool = False) -> None:
        entry = self.journal.get(job.id)
        if entry is None:
            if require_uninspected_path and self.session.inspected:
                entry = self._finished(
                    job.id,
                    Outcome(
                        "failed",
                        error_summary="Not run: this customer requires an uninspected path, and "
                        "something on the path to the controller inspects TLS.",
                    ),
                )
            else:
                self.journal.put(job.id, {"state": "started"})
                entry = self._run(job)
        elif entry["state"] == "started":
            # Handed over again after this collector stopped mid-job. Never run it twice.
            entry = self._finished(
                job.id,
                Outcome(
                    "failed",
                    error_summary="The collector stopped while running this job, so it wasn't "
                    "run again.",
                ),
            )
        if entry is not None and entry["state"] == "finished":
            self._report(job.id, entry)

    def _run(self, job: Job) -> dict[str, Any] | None:
        def report(message: str, percent: int | None = None) -> None:
            if self._call(self.session.progress, job.id, message, percent).cancelled:
                raise Cancelled

        handler = HANDLERS.get(job.type)
        try:
            if handler is None:
                outcome = Outcome(
                    "failed", error_summary=f"This collector version can't run {job.type} jobs."
                )
            else:
                outcome = handler(job, self._open_credentials(job), report)
        except (Cancelled, JobClosed):
            # The controller already recorded how it ended.
            self.journal.put(job.id, {"state": "reported"})
            return None
        except sealing.SealError:
            outcome = Outcome(
                "failed", error_summary="The job's credentials weren't sealed to this collector."
            )
        except httpx.TransportError:
            raise
        except Exception as exc:  # noqa: BLE001 (any failure ends the job, never the collector)
            # Only the kind of error: a message could carry device output.
            outcome = Outcome("failed", error_summary=f"The job failed: {type(exc).__name__}.")
        return self._finished(job.id, outcome)

    def _open_credentials(self, job: Job) -> list[dict[str, Any]]:
        if not job.credentials:
            return []
        opened = sealing.open_sealed(
            self.session.config.seal_key, job.credentials, job_seal_context(job.id)
        )
        return json.loads(opened)

    def _finished(self, job_id: str, outcome: Outcome) -> dict[str, Any]:
        payload = outcome.payload
        path = self.journal.payload_path(job_id)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        entry = {
            "state": "finished",
            "result_id": str(uuid.uuid4()),
            "outcome": outcome.outcome,
            "device_count": outcome.device_count,
            "error_summary": outcome.error_summary,
            "chunks": -(-len(payload) // CHUNK_BYTES),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "acked": 0,
        }
        self.journal.put(job_id, entry)
        return entry

    def _report(self, job_id: str, entry: dict[str, Any]) -> None:
        path = self.journal.payload_path(job_id)
        try:
            with path.open("rb") as f:
                for index in range(entry["acked"], entry["chunks"]):
                    f.seek(index * CHUNK_BYTES)
                    self._call(self.session.upload_chunk, job_id, index, f.read(CHUNK_BYTES))
                    entry["acked"] = index + 1
                    self.journal.put(job_id, entry)
            result = JobResult(
                result_id=entry["result_id"],
                outcome=entry["outcome"],
                chunks=entry["chunks"],
                sha256=entry["sha256"],
                device_count=entry["device_count"],
                error_summary=entry["error_summary"],
            )
            self._call(self.session.finish, job_id, result)
        except JobClosed:
            pass  # Cancelled meanwhile: the controller already recorded how it ended.
        self.journal.put(job_id, {"state": "reported"})
        path.unlink(missing_ok=True)
