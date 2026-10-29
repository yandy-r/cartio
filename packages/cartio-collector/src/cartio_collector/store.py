"""The on-site store: where an On site collector writes what it collects (ADR-0002).

There are two kinds, chosen when the customer is onboarded: a PostgreSQL database, or a folder
on a file share (SMB or NFS, mounted on the collector host). Both take the same `CollectResult`
the controller would get for a Hosted customer, already masked, and both check what they wrote
by reading it back and comparing its SHA-256 before saying it was stored.

A job's result is written once: writing the same job again with the same content is a no-op,
and with other content is refused, so a retried job never changes what was stored.

When a Hosted customer moves On site (YAN-1712), the controller hands its copy of their network
data to the collector as one export document. `write_export` keeps it the same way, write-once
and read back, and returns its checksum: the controller deletes its own copy only when that
checksum matches the one it takes of the data it is about to delete.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cartio_core.protocol import CollectResult, canonical_json

KINDS = ("postgres", "folder")
TABLE = "cartio_collect_results"
EXPORTS_TABLE = "cartio_exports"


class StoreError(RuntimeError):
    """The store can't be reached or written, or holds other content for this job."""


@dataclass(frozen=True)
class Receipt:
    """What a store confirmed it holds for one job."""

    job: str
    sha256: str
    devices: int


def document(job_id: str, engagement: int | None, result: CollectResult) -> dict[str, Any]:
    return {
        "job": str(uuid.UUID(job_id)),
        "engagement": engagement,
        "collected_at": datetime.now(UTC).isoformat(),
        **result.to_dict(),
    }


# The bytes a store's checksum covers: the same for a document read back.
canonical = canonical_json


def _digest(doc: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(doc)).hexdigest()


class Store(Protocol):
    kind: str

    def check(self) -> None:
        """Raise StoreError unless the store can be written and read back."""

    def write(self, job_id: str, engagement: int | None, result: CollectResult) -> Receipt:
        """Store one job's result and confirm it by reading it back."""

    def write_export(self, job_id: str, doc: dict[str, Any]) -> str:
        """Store an export from the controller, read it back, and return its checksum."""


class FolderStore:
    """One folder per job under `root`, each holding `result.json` and its `result.sha256`."""

    kind = "folder"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _write_file(self, path: Path, data: bytes) -> None:
        # Written whole and swapped in, so a reader never sees half a file.
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def check(self) -> None:
        if not self.root.is_dir():
            raise StoreError(f"{self.root} isn't a folder. Mount the share and try again.")
        probe = self.root / f".cartio-check-{uuid.uuid4().hex}"
        try:
            self._write_file(probe, b"cartio")
            if probe.read_bytes() != b"cartio":
                raise StoreError(f"What was written to {self.root} didn't read back the same.")
        except OSError as exc:
            raise StoreError(f"Can't write to {self.root}: {exc.strerror or exc}.") from exc
        finally:
            probe.unlink(missing_ok=True)

    def write(self, job_id: str, engagement: int | None, result: CollectResult) -> Receipt:
        doc = document(job_id, engagement, result)
        folder = self.root / f"engagement-{engagement}" if engagement else self.root / "jobs"
        folder = folder / doc["job"]
        path, digest_path = folder / "result.json", folder / "result.sha256"
        try:
            if digest_path.exists():
                return self._existing(doc, path, digest_path)
            folder.mkdir(parents=True, exist_ok=True)
            self._write_file(path, canonical(doc))
            stored = json.loads(path.read_bytes())
            digest = _digest(doc)
            if _digest(stored) != digest:
                raise StoreError("What was written to the folder didn't read back the same.")
            # Written last: a job with a digest file was stored whole.
            self._write_file(digest_path, f"{digest}  result.json\n".encode())
        except OSError as exc:
            raise StoreError(f"Can't write to {self.root}: {exc.strerror or exc}.") from exc
        return Receipt(doc["job"], digest, len(result.devices))

    def write_export(self, job_id: str, doc: dict[str, Any]) -> str:
        folder = self.root / "exports" / str(uuid.UUID(job_id))
        path, digest_path = folder / "export.json", folder / "export.sha256"
        digest = _digest(doc)
        try:
            if not digest_path.exists():
                folder.mkdir(parents=True, exist_ok=True)
                self._write_file(path, canonical(doc))
            stored = json.loads(path.read_bytes())
            if _digest(stored) != digest:
                if digest_path.exists():
                    raise StoreError(f"The store already holds another export for job {job_id}.")
                raise StoreError("What was written to the folder didn't read back the same.")
            if not digest_path.exists():
                # Written last: an export with a digest file was stored whole.
                self._write_file(digest_path, f"{digest}  export.json\n".encode())
            elif digest_path.read_text(encoding="ascii").split()[0] != digest:
                raise StoreError(f"{path} doesn't match its checksum.")
        except OSError as exc:
            raise StoreError(f"Can't write to {self.root}: {exc.strerror or exc}.") from exc
        return digest

    def _existing(self, doc: dict[str, Any], path: Path, digest_path: Path) -> Receipt:
        stored = json.loads(path.read_bytes())
        digest = digest_path.read_text(encoding="ascii").split()[0]
        if _digest(stored) != digest:
            raise StoreError(f"{path} doesn't match its checksum.")
        doc = {**doc, "collected_at": stored.get("collected_at")}
        if _digest(doc) != digest:
            raise StoreError(f"The store already holds other content for job {doc['job']}.")
        return Receipt(doc["job"], digest, len(doc["devices"]))


class PostgresStore:
    """One row per job in `cartio_collect_results`, with the result as JSONB.

    The connection string is libpq's, so a password can stay out of it (PGPASSWORD or a
    .pgpass file) and `options=-csearch_path=...` picks a schema.
    """

    kind = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self):
        import psycopg

        try:
            return psycopg.connect(self.dsn, connect_timeout=10)
        except psycopg.Error as exc:
            # The kind of error only: libpq's message can repeat the connection string.
            raise StoreError(
                f"Can't connect to the on-site database: {type(exc).__name__}."
            ) from exc

    def _ensure_table(self, conn) -> None:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                job uuid PRIMARY KEY,
                engagement integer,
                collected_at timestamptz NOT NULL,
                key_version text NOT NULL,
                result jsonb NOT NULL,
                sha256 char(64) NOT NULL
            )
            """
        )

    def check(self) -> None:
        import psycopg

        with self._connect() as conn:
            try:
                self._ensure_table(conn)
                conn.execute(f"SELECT 1 FROM {TABLE} LIMIT 1")  # noqa: S608 (a constant)
            except psycopg.Error as exc:
                raise StoreError(
                    f"Can't use the on-site database: {type(exc).__name__}. The collector's "
                    f"database user needs to create and write the {TABLE} table."
                ) from exc

    def write(self, job_id: str, engagement: int | None, result: CollectResult) -> Receipt:
        import psycopg
        from psycopg.types.json import Jsonb

        doc = document(job_id, engagement, result)
        digest = _digest(doc)
        try:
            with self._connect() as conn:
                self._ensure_table(conn)
                conn.execute(
                    f"INSERT INTO {TABLE} (job, engagement, collected_at, key_version, result, "  # noqa: S608
                    "sha256) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (job) DO NOTHING",
                    [
                        doc["job"],
                        engagement,
                        doc["collected_at"],
                        result.key_version,
                        Jsonb(doc),
                        digest,
                    ],
                )
                row = conn.execute(
                    f"SELECT result, sha256 FROM {TABLE} WHERE job = %s",  # noqa: S608
                    [doc["job"]],
                ).fetchone()
        except psycopg.Error as exc:
            raise StoreError(f"Can't write to the on-site database: {type(exc).__name__}.") from exc
        stored, stored_digest = row
        if _digest(stored) != stored_digest:
            raise StoreError(f"The stored result of job {doc['job']} doesn't match its checksum.")
        if _digest({**doc, "collected_at": stored["collected_at"]}) != stored_digest:
            raise StoreError(f"The store already holds other content for job {doc['job']}.")
        return Receipt(doc["job"], stored_digest, len(result.devices))

    def write_export(self, job_id: str, doc: dict[str, Any]) -> str:
        import psycopg
        from psycopg.types.json import Jsonb

        job = str(uuid.UUID(job_id))
        digest = _digest(doc)
        try:
            with self._connect() as conn:
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {EXPORTS_TABLE} (
                        job uuid PRIMARY KEY,
                        exported_at timestamptz NOT NULL DEFAULT now(),
                        document jsonb NOT NULL,
                        sha256 char(64) NOT NULL
                    )
                    """
                )
                conn.execute(
                    f"INSERT INTO {EXPORTS_TABLE} (job, document, sha256) "  # noqa: S608
                    "VALUES (%s, %s, %s) ON CONFLICT (job) DO NOTHING",
                    [job, Jsonb(doc), digest],
                )
                row = conn.execute(
                    f"SELECT document, sha256 FROM {EXPORTS_TABLE} WHERE job = %s",  # noqa: S608
                    [job],
                ).fetchone()
        except psycopg.Error as exc:
            raise StoreError(f"Can't write to the on-site database: {type(exc).__name__}.") from exc
        stored, stored_digest = row
        if stored_digest != digest:
            raise StoreError(f"The store already holds another export for job {job}.")
        if _digest(stored) != digest:
            raise StoreError(f"The stored export of job {job} doesn't match its checksum.")
        return digest


def open_store(settings: dict[str, str]) -> Store:
    """The store a collector's saved settings name: {"kind": ..., "path" or "dsn": ...}."""
    kind = settings.get("kind")
    if kind == "folder" and settings.get("path"):
        return FolderStore(settings["path"])
    if kind == "postgres" and settings.get("dsn"):
        return PostgresStore(settings["dsn"])
    raise StoreError("The collector's on-site store isn't set up. Run: cartio-collector store set")
