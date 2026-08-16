"""Dedicated SQLite persistence and backup support for StructuralOffice."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .const import DATABASE_SCHEMA_VERSION
from .models import StructuralOfficeValidationError

_BACKUP_NAME = re.compile(r"^structuraloffice-\d{8}T\d{12}Z\.db$")


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
        """Atomically replace the stored application snapshot."""
        timestamp = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for collection, records in data.items():
                connection.execute(
                    "DELETE FROM records WHERE collection = ?", (collection,)
                )
                connection.executemany(
                    "INSERT INTO records(collection, record_id, payload, updated_at) "
                    "VALUES(?, ?, ?, ?)",
                    (
                        (
                            collection,
                            record_id,
                            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                            timestamp,
                        )
                        for record_id, payload in records.items()
                    ),
                )

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
