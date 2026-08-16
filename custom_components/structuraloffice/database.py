"""Dedicated SQLite persistence and backup support for StructuralOffice."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .const import DATABASE_SCHEMA_VERSION
from .models import StructuralOfficeValidationError

_BACKUP_NAME = re.compile(r"^structuraloffice-\d{8}T\d{12}Z\.db$")
VALID_COLLECTIONS = {
    "contacts",
    "invoices",
    "occurrences",
    "routines",
    "topics",
}


class StructuralOfficeConflictError(StructuralOfficeValidationError):
    """Raised when a client writes an obsolete record revision."""

    def __init__(self, current: dict[str, Any]) -> None:
        super().__init__("The record was changed by another client")
        self.current = current


class StructuralOfficeDatabase:
    """Store StructuralOffice records in a private, versioned SQLite database."""

    def __init__(self, path: Path, backup_directory: Path) -> None:
        self.path = path
        self.backup_directory = backup_directory

    def initialize(self) -> None:
        """Create the database and apply schema migrations."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    collection TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (collection, record_id)
                );
                CREATE INDEX IF NOT EXISTS records_collection_idx
                    ON records (collection);
                CREATE TABLE IF NOT EXISTS import_batches (
                    import_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    checksum TEXT NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    created_count INTEGER NOT NULL,
                    updated_count INTEGER NOT NULL,
                    cancelled_count INTEGER NOT NULL,
                    raw_payload BLOB
                );
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(records)")
            }
            if "revision" not in columns:
                connection.execute(
                    "ALTER TABLE records ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
                )
            if "created_at" not in columns:
                connection.execute("ALTER TABLE records ADD COLUMN created_at TEXT")
            if "archived_at" not in columns:
                connection.execute("ALTER TABLE records ADD COLUMN archived_at TEXT")
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE records SET created_at = COALESCE(created_at, updated_at, ?)",
                (now,),
            )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS change_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    changed_fields TEXT NOT NULL,
                    payload TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS change_events_created_idx
                    ON change_events (created_at);
                CREATE TABLE IF NOT EXISTS edit_sessions (
                    session_id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS edit_sessions_record_idx
                    ON edit_sessions (collection, record_id);
                CREATE TABLE IF NOT EXISTS audit_log (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(DATABASE_SCHEMA_VERSION),),
            )

    def _connect(self, path: Path | None = None) -> sqlite3.Connection:
        connection = sqlite3.connect(path or self.path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def load(self, collections: set[str]) -> dict[str, dict[str, Any]]:
        """Load all requested record collections."""
        result = {collection: {} for collection in collections}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT collection, record_id, payload FROM records"
            ).fetchall()
        for collection, record_id, payload in rows:
            if collection in result:
                result[collection][record_id] = json.loads(payload)
        return result

    def save(self, data: Mapping[str, Mapping[str, Any]]) -> None:
        """Synchronize a legacy in-memory snapshot while preserving revisions."""
        timestamp = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for collection, records in data.items():
                stored = {
                    row[0]: (row[1], row[2])
                    for row in connection.execute(
                        "SELECT record_id, payload, revision FROM records "
                        "WHERE collection = ? AND archived_at IS NULL",
                        (collection,),
                    )
                }
                current_ids = set(records)
                for removed_id in set(stored) - current_ids:
                    connection.execute(
                        "DELETE FROM records WHERE collection = ? AND record_id = ?",
                        (collection, removed_id),
                    )
                for record_id, payload in records.items():
                    encoded = json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                    )
                    previous = stored.get(record_id)
                    if previous is None:
                        connection.execute(
                            "INSERT INTO records(collection, record_id, payload, updated_at, "
                            "revision, created_at, archived_at) VALUES(?, ?, ?, ?, 1, ?, NULL)",
                            (collection, record_id, encoded, timestamp, timestamp),
                        )
                    elif json.loads(previous[0]) != payload:
                        connection.execute(
                            "UPDATE records SET payload = ?, updated_at = ?, revision = ?, "
                            "archived_at = NULL WHERE collection = ? AND record_id = ?",
                            (encoded, timestamp, previous[1] + 1, collection, record_id),
                        )

    @staticmethod
    def _validate_collection(collection: str) -> None:
        if collection not in VALID_COLLECTIONS:
            raise StructuralOfficeValidationError("Unknown record collection")

    @staticmethod
    def _envelope(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        return {
            "archived_at": row[6],
            "collection": row[0],
            "created_at": row[5],
            "data": json.loads(row[2]),
            "id": row[1],
            "revision": row[3],
            "updated_at": row[4],
        }

    def list_live_records(
        self,
        collection: str,
        *,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a page of revisioned records."""
        self._validate_collection(collection)
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        where = "collection = ?" + ("" if include_archived else " AND archived_at IS NULL")
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM records WHERE {where}", (collection,)
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT collection, record_id, payload, revision, updated_at, "
                f"created_at, archived_at FROM records WHERE {where} "
                "ORDER BY updated_at DESC, record_id LIMIT ? OFFSET ?",
                (collection, limit, offset),
            ).fetchall()
        return {
            "items": [self._envelope(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    def get_live_record(self, collection: str, record_id: str) -> dict[str, Any] | None:
        """Return one revisioned record, including archived records."""
        self._validate_collection(collection)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT collection, record_id, payload, revision, updated_at, "
                "created_at, archived_at FROM records WHERE collection = ? AND record_id = ?",
                (collection, record_id),
            ).fetchone()
        return self._envelope(row) if row else None

    def write_live_record(
        self,
        collection: str,
        record_id: str,
        payload: dict[str, Any],
        expected_revision: int | None,
        user_id: str,
        user_name: str,
        requested_fields: set[str] | None = None,
    ) -> dict[str, Any]:
        """Create or update a record using optimistic concurrency control."""
        self._validate_collection(collection)
        timestamp = datetime.now(UTC).isoformat()
        payload_updated_at = payload.get("updated_at")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT collection, record_id, payload, revision, updated_at, "
                "created_at, archived_at FROM records WHERE collection = ? AND record_id = ?",
                (collection, record_id),
            ).fetchone()
            previous = json.loads(row[2]) if row else {}
            if row and expected_revision != row[3]:
                changes = connection.execute(
                    "SELECT revision, operation, changed_fields FROM change_events "
                    "WHERE collection = ? AND record_id = ? AND revision > ? "
                    "ORDER BY revision",
                    (collection, record_id, expected_revision or 0),
                ).fetchall()
                covered_revisions = {item[0] for item in changes}
                required_revisions = set(range((expected_revision or 0) + 1, row[3] + 1))
                changed_since = {
                    field for item in changes for field in json.loads(item[2])
                }
                can_merge = (
                    expected_revision is not None
                    and requested_fields is not None
                    and covered_revisions == required_revisions
                    and all(item[1] in {"updated", "merged"} for item in changes)
                    and requested_fields.isdisjoint(changed_since)
                )
                if not can_merge:
                    raise StructuralOfficeConflictError(self._envelope(row))
                payload = {
                    **previous,
                    **{field: payload.get(field) for field in requested_fields},
                }
                if payload_updated_at is not None:
                    payload["updated_at"] = payload_updated_at
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            if not row and expected_revision not in (None, 0):
                raise StructuralOfficeConflictError({})
            revision = row[3] + 1 if row else 1
            created_at = row[5] if row else timestamp
            if row:
                connection.execute(
                    "UPDATE records SET payload = ?, revision = ?, updated_at = ?, "
                    "archived_at = NULL WHERE collection = ? AND record_id = ?",
                    (encoded, revision, timestamp, collection, record_id),
                )
                operation = "merged" if expected_revision != row[3] else "updated"
            else:
                connection.execute(
                    "INSERT INTO records(collection, record_id, payload, updated_at, "
                    "revision, created_at, archived_at) VALUES(?, ?, ?, ?, ?, ?, NULL)",
                    (collection, record_id, encoded, timestamp, revision, created_at),
                )
                operation = "created"
            changed_fields = sorted(
                key for key in set(previous) | set(payload) if previous.get(key) != payload.get(key)
            )
            sequence = self._record_change(
                connection,
                collection,
                record_id,
                operation,
                revision,
                user_id,
                user_name,
                changed_fields,
                payload,
                timestamp,
            )
        result = self.get_live_record(collection, record_id)
        result["event_sequence"] = sequence
        result["operation"] = operation
        result["changed_fields"] = changed_fields
        return result

    def archive_live_record(
        self,
        collection: str,
        record_id: str,
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Archive a record using optimistic concurrency control."""
        self._validate_collection(collection)
        timestamp = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT collection, record_id, payload, revision, updated_at, "
                "created_at, archived_at FROM records WHERE collection = ? AND record_id = ?",
                (collection, record_id),
            ).fetchone()
            if row is None:
                raise StructuralOfficeValidationError("Record was not found")
            if expected_revision != row[3]:
                raise StructuralOfficeConflictError(self._envelope(row))
            revision = row[3] + 1
            connection.execute(
                "UPDATE records SET revision = ?, updated_at = ?, archived_at = ? "
                "WHERE collection = ? AND record_id = ?",
                (revision, timestamp, timestamp, collection, record_id),
            )
            sequence = self._record_change(
                connection,
                collection,
                record_id,
                "archived",
                revision,
                user_id,
                user_name,
                [],
                json.loads(row[2]),
                timestamp,
            )
        result = self.get_live_record(collection, record_id)
        result["event_sequence"] = sequence
        result["operation"] = "archived"
        result["changed_fields"] = []
        return result

    @staticmethod
    def _record_change(
        connection: sqlite3.Connection,
        collection: str,
        record_id: str,
        operation: str,
        revision: int,
        user_id: str,
        user_name: str,
        changed_fields: list[str],
        payload: dict[str, Any],
        timestamp: str,
    ) -> int:
        fields = json.dumps(changed_fields, separators=(",", ":"))
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        cursor = connection.execute(
            "INSERT INTO change_events(collection, record_id, operation, revision, "
            "user_id, user_name, changed_fields, payload, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                collection,
                record_id,
                operation,
                revision,
                user_id,
                user_name,
                fields,
                encoded,
                timestamp,
            ),
        )
        connection.execute(
            "INSERT INTO audit_log(action, collection, record_id, revision, user_id, "
            "user_name, details, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                operation,
                collection,
                record_id,
                revision,
                user_id,
                user_name,
                fields,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)

    def events_since(self, after: int, limit: int = 200) -> dict[str, Any]:
        """Return persisted changes so reconnecting clients can catch up."""
        limit = max(1, min(1000, limit))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, collection, record_id, operation, revision, user_id, "
                "user_name, changed_fields, created_at FROM change_events "
                "WHERE sequence > ? ORDER BY sequence LIMIT ?",
                (max(0, after), limit),
            ).fetchall()
        events = [
            {
                "changed_fields": json.loads(row[7]),
                "collection": row[1],
                "created_at": row[8],
                "operation": row[3],
                "record_id": row[2],
                "revision": row[4],
                "sequence": row[0],
                "user_id": row[5],
                "user_name": row[6],
            }
            for row in rows
        ]
        return {"events": events, "last_sequence": events[-1]["sequence"] if events else after}

    def audit_entries(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Return a page of audit entries without record payloads."""
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            rows = connection.execute(
                "SELECT sequence, action, collection, record_id, revision, user_id, "
                "user_name, details, created_at FROM audit_log "
                "ORDER BY sequence DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return {
            "items": [
                {
                    "action": row[1],
                    "changed_fields": json.loads(row[7]),
                    "collection": row[2],
                    "created_at": row[8],
                    "record_id": row[3],
                    "revision": row[4],
                    "sequence": row[0],
                    "user_id": row[5],
                    "user_name": row[6],
                }
                for row in rows
            ],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    def start_edit_session(
        self,
        collection: str,
        record_id: str,
        client_id: str,
        user_id: str,
        user_name: str,
        ttl_seconds: int = 60,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or refresh a soft edit-presence session."""
        self._validate_collection(collection)
        ttl_seconds = max(15, min(300, ttl_seconds))
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM edit_sessions WHERE expires_at <= ?", (now.isoformat(),)
            )
            if session_id:
                existing = connection.execute(
                    "SELECT user_id, collection, record_id FROM edit_sessions "
                    "WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if existing is None or existing != (user_id, collection, record_id):
                    raise StructuralOfficeValidationError("Edit session was not found")
                connection.execute(
                    "UPDATE edit_sessions SET expires_at = ?, client_id = ? "
                    "WHERE session_id = ?",
                    (expires_at.isoformat(), client_id, session_id),
                )
            else:
                session_id = uuid4().hex
                connection.execute(
                    "INSERT INTO edit_sessions(session_id, collection, record_id, "
                    "client_id, user_id, user_name, acquired_at, expires_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        collection,
                        record_id,
                        client_id[:200],
                        user_id,
                        user_name[:200],
                        now.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
        return {
            "editors": self.active_edit_sessions(collection, record_id),
            "expires_at": expires_at.isoformat(),
            "session_id": session_id,
        }

    def active_edit_sessions(self, collection: str, record_id: str) -> list[dict[str, Any]]:
        """Return current editors for one record."""
        self._validate_collection(collection)
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM edit_sessions WHERE expires_at <= ?", (now,))
            rows = connection.execute(
                "SELECT session_id, client_id, user_id, user_name, acquired_at, expires_at "
                "FROM edit_sessions WHERE collection = ? AND record_id = ? "
                "ORDER BY acquired_at",
                (collection, record_id),
            ).fetchall()
        return [
            {
                "acquired_at": row[4],
                "client_id": row[1],
                "expires_at": row[5],
                "session_id": row[0],
                "user_id": row[2],
                "user_name": row[3],
            }
            for row in rows
        ]

    def end_edit_session(self, session_id: str, user_id: str) -> bool:
        """End one edit session owned by the requesting user."""
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM edit_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            )
        return cursor.rowcount > 0

    def add_import_batch(self, batch: dict[str, Any], raw_payload: bytes) -> None:
        """Record one applied source import and retain its original bytes."""
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO import_batches(
                    import_id, source_name, checksum, imported_at, record_count,
                    created_count, updated_count, cancelled_count, raw_payload
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch["import_id"],
                    batch["source_name"],
                    batch["checksum"],
                    batch["imported_at"],
                    batch["record_count"],
                    batch["created"],
                    batch["updated"],
                    batch["cancelled"],
                    raw_payload,
                ),
            )

    def has_import_checksum(self, checksum: str) -> bool:
        """Return whether the exact source file was already applied."""
        with self._connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM import_batches WHERE checksum = ?", (checksum,)
                ).fetchone()
                is not None
            )

    def statistics(self) -> dict[str, Any]:
        """Return non-sensitive database health and size statistics."""
        with self._connect() as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            schema_version = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()[0]
            record_count = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            import_count = connection.execute(
                "SELECT COUNT(*) FROM import_batches"
            ).fetchone()[0]
        return {
            "backup_count": len(self.list_backups()),
            "database_bytes": sum(
                path.stat().st_size
                for path in (
                    self.path,
                    Path(f"{self.path}-wal"),
                    Path(f"{self.path}-shm"),
                )
                if path.exists()
            ),
            "import_count": import_count,
            "integrity": integrity,
            "record_count": record_count,
            "schema_version": int(schema_version),
        }

    def create_backup(self) -> dict[str, Any]:
        """Create a consistent online SQLite backup."""
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.backup_directory / f"structuraloffice-{timestamp}.db"
        with self._connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        return self._backup_info(target)

    def list_backups(self) -> list[dict[str, Any]]:
        """List managed database backups, newest first."""
        if not self.backup_directory.exists():
            return []
        return sorted(
            (
                self._backup_info(path)
                for path in self.backup_directory.glob("*.db")
                if _BACKUP_NAME.fullmatch(path.name)
            ),
            key=lambda item: item["created_at"],
            reverse=True,
        )

    def restore_backup(self, filename: str) -> None:
        """Replace the live database from a validated managed backup."""
        source = self._backup_path(filename)
        with sqlite3.connect(source) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StructuralOfficeValidationError("Backup integrity check failed")
        safety_copy = self.create_backup()
        try:
            with sqlite3.connect(source) as backup, self._connect() as live:
                backup.backup(live)
        except Exception:
            safety_path = self._backup_path(safety_copy["filename"])
            with sqlite3.connect(safety_path) as backup, self._connect() as live:
                backup.backup(live)
            raise

    def delete_backup(self, filename: str) -> None:
        """Delete a managed backup."""
        self._backup_path(filename).unlink()

    def read_backup(self, filename: str) -> bytes:
        """Read a managed backup for an authenticated download."""
        return self._backup_path(filename).read_bytes()

    def _backup_path(self, filename: str) -> Path:
        if not _BACKUP_NAME.fullmatch(filename):
            raise StructuralOfficeValidationError("Invalid backup filename")
        path = self.backup_directory / filename
        if not path.is_file():
            raise StructuralOfficeValidationError("Backup was not found")
        return path

    @staticmethod
    def _backup_info(path: Path) -> dict[str, Any]:
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        return {
            "created_at": modified.isoformat(),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
        }
