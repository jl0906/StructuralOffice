"""Dedicated SQLite persistence and backup support for StructuralOffice."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .const import DATABASE_SCHEMA_VERSION, VALID_STATUSES
from .models import StructuralOfficeValidationError

_BACKUP_NAME = re.compile(r"^structuraloffice-\d{8}T\d{12}Z\.db$")
VALID_COLLECTIONS = {
    "contacts",
    "invoices",
    "occurrences",
    "routines",
    "topics",
}
VALID_EDIT_COLLECTIONS = VALID_COLLECTIONS | {"accounting_rules", "task_checklist", "tasks"}


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
        previous_version = self._existing_schema_version()
        if previous_version is not None and previous_version < DATABASE_SCHEMA_VERSION:
            self.create_backup()
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
                    unchanged_count INTEGER NOT NULL DEFAULT 0,
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
            import_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(import_batches)")
            }
            if "known_row_count" not in import_columns:
                connection.execute(
                    "ALTER TABLE import_batches ADD COLUMN known_row_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "new_row_count" not in import_columns:
                connection.execute(
                    "ALTER TABLE import_batches ADD COLUMN new_row_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "unchanged_count" not in import_columns:
                connection.execute(
                    "ALTER TABLE import_batches ADD COLUMN unchanged_count "
                    "INTEGER NOT NULL DEFAULT 0"
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_topics (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    estimated_minutes INTEGER NOT NULL,
                    instructions TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );
                CREATE TABLE IF NOT EXISTS workflow_topic_steps (
                    id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL REFERENCES workflow_topics(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1,
                    estimated_minutes INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(topic_id, position)
                );
                CREATE TABLE IF NOT EXISTS workflow_routines (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    due_time TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT,
                    enabled INTEGER NOT NULL,
                    catch_up_policy TEXT NOT NULL,
                    estimated_minutes INTEGER NOT NULL DEFAULT 10,
                    priority TEXT NOT NULL DEFAULT 'normal',
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );
                CREATE TABLE IF NOT EXISTS workflow_recurrence_rules (
                    routine_id TEXT PRIMARY KEY REFERENCES workflow_routines(id) ON DELETE CASCADE,
                    frequency TEXT NOT NULL,
                    interval_value INTEGER NOT NULL,
                    weekdays TEXT NOT NULL,
                    month_days TEXT NOT NULL,
                    months TEXT NOT NULL,
                    explicit_dates TEXT NOT NULL,
                    business_day_rule TEXT NOT NULL,
                    invalid_day_rule TEXT NOT NULL,
                    non_working_dates TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS workflow_routine_topics (
                    routine_id TEXT NOT NULL REFERENCES workflow_routines(id) ON DELETE CASCADE,
                    topic_id TEXT NOT NULL REFERENCES workflow_topics(id),
                    position INTEGER NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1,
                    due_offset_days INTEGER NOT NULL DEFAULT 0,
                    due_time_override TEXT,
                    PRIMARY KEY(routine_id, topic_id)
                );
                CREATE TABLE IF NOT EXISTS workflow_reminders (
                    id TEXT PRIMARY KEY,
                    routine_id TEXT NOT NULL REFERENCES workflow_routines(id) ON DELETE CASCADE,
                    offset_days INTEGER NOT NULL,
                    reminder_time TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(routine_id, offset_days, reminder_time)
                );
                CREATE TABLE IF NOT EXISTS task_occurrences (
                    id TEXT PRIMARY KEY,
                    routine_id TEXT REFERENCES workflow_routines(id),
                    topic_id TEXT REFERENCES workflow_topics(id),
                    source_type TEXT NOT NULL,
                    source_id TEXT,
                    scheduled_date TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    estimated_minutes INTEGER NOT NULL DEFAULT 10,
                    topic_snapshot TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    completed_by TEXT,
                    completion_note TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );
                CREATE INDEX IF NOT EXISTS task_occurrences_due_idx
                    ON task_occurrences(status, due_at);
                CREATE TABLE IF NOT EXISTS task_checklist_items (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task_occurrences(id) ON DELETE CASCADE,
                    source_step_id TEXT,
                    position INTEGER NOT NULL,
                    title_snapshot TEXT NOT NULL,
                    required INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    completed_at TEXT,
                    completed_by TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS reminder_deliveries (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task_occurrences(id) ON DELETE CASCADE,
                    routine_id TEXT NOT NULL,
                    offset_days INTEGER NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sent_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_id, offset_days)
                );
                CREATE TABLE IF NOT EXISTS accounting_invoices (
                    id TEXT PRIMARY KEY,
                    invoice_number TEXT NOT NULL,
                    invoice_date TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    gross_cents INTEGER NOT NULL,
                    outstanding_cents INTEGER NOT NULL,
                    contact TEXT NOT NULL,
                    source TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );
                CREATE INDEX IF NOT EXISTS accounting_invoices_due_idx
                    ON accounting_invoices(due_date, status, currency);
                CREATE TABLE IF NOT EXISTS invoice_import_rows (
                    row_fingerprint TEXT PRIMARY KEY,
                    import_id TEXT NOT NULL REFERENCES import_batches(import_id) ON DELETE CASCADE,
                    invoice_number TEXT NOT NULL,
                    row_number INTEGER NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounting_escalation_rules (
                    id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    escalation_level INTEGER NOT NULL,
                    days_after_due INTEGER NOT NULL,
                    evaluation_time TEXT NOT NULL,
                    group_by TEXT NOT NULL DEFAULT 'invoice_range',
                    minimum_open_invoices INTEGER NOT NULL DEFAULT 1,
                    maximum_invoices_per_batch INTEGER NOT NULL DEFAULT 1000,
                    auto_complete_empty_batches INTEGER NOT NULL DEFAULT 1,
                    notify_enabled INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_type, escalation_level)
                );
                CREATE TABLE IF NOT EXISTS accounting_task_batches (
                    id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    escalation_level INTEGER NOT NULL,
                    source_due_date TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    evaluation_date TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    invoice_count_initial INTEGER NOT NULL,
                    invoice_count_open INTEGER NOT NULL,
                    outstanding_cents INTEGER NOT NULL,
                    estimated_minutes INTEGER NOT NULL DEFAULT 10,
                    created_automatically INTEGER NOT NULL,
                    rule_id TEXT REFERENCES accounting_escalation_rules(id),
                    deduplication_key TEXT NOT NULL UNIQUE,
                    membership_fingerprint TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    archived_at TEXT
                );
                CREATE INDEX IF NOT EXISTS accounting_task_batches_due_idx
                    ON accounting_task_batches(status, due_at);
                CREATE TABLE IF NOT EXISTS accounting_task_invoices (
                    task_id TEXT NOT NULL REFERENCES accounting_task_batches(id) ON DELETE CASCADE,
                    invoice_id TEXT NOT NULL REFERENCES accounting_invoices(id),
                    outstanding_cents_at_creation INTEGER NOT NULL,
                    outstanding_cents_current INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    included_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolution_reason TEXT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(task_id, invoice_id)
                );
                """
            )
            recurrence_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(workflow_recurrence_rules)"
                )
            }
            if "non_working_dates" not in recurrence_columns:
                connection.execute(
                    "ALTER TABLE workflow_recurrence_rules ADD COLUMN "
                    "non_working_dates TEXT NOT NULL DEFAULT '[]'"
                )
            routine_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(workflow_routines)")
            }
            if "estimated_minutes" not in routine_columns:
                connection.execute(
                    "ALTER TABLE workflow_routines ADD COLUMN "
                    "estimated_minutes INTEGER NOT NULL DEFAULT 10"
                )
            if "priority" not in routine_columns:
                connection.execute(
                    "ALTER TABLE workflow_routines ADD COLUMN "
                    "priority TEXT NOT NULL DEFAULT 'normal'"
                )
            task_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(task_occurrences)")
            }
            if "estimated_minutes" not in task_columns:
                connection.execute(
                    "ALTER TABLE task_occurrences ADD COLUMN "
                    "estimated_minutes INTEGER NOT NULL DEFAULT 10"
                )
            batch_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(accounting_task_batches)"
                )
            }
            if "estimated_minutes" not in batch_columns:
                connection.execute(
                    "ALTER TABLE accounting_task_batches ADD COLUMN "
                    "estimated_minutes INTEGER NOT NULL DEFAULT 10"
                )
            if "membership_fingerprint" not in batch_columns:
                connection.execute(
                    "ALTER TABLE accounting_task_batches ADD COLUMN "
                    "membership_fingerprint TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS accounting_task_membership_idx ON "
                "accounting_task_batches(rule_id, currency, membership_fingerprint)"
            )
            self._insert_default_escalation_rules(connection, now)
            self._sync_all_projections(connection)
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(DATABASE_SCHEMA_VERSION),),
            )

    def _existing_schema_version(self) -> int | None:
        """Return the schema version before initialization, if a database exists."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        try:
            with sqlite3.connect(self.path) as connection:
                has_metadata = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'metadata'"
                ).fetchone()
                if not has_metadata:
                    return 0
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()
                return int(row[0]) if row else 0
        except (sqlite3.DatabaseError, TypeError, ValueError):
            return 0

    def _connect(self, path: Path | None = None) -> sqlite3.Connection:
        connection = sqlite3.connect(path or self.path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def _insert_default_escalation_rules(
        connection: sqlite3.Connection, timestamp: str
    ) -> None:
        defaults = (
            ("payment-reminder-default", "payment_reminder", 0, 1),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO accounting_escalation_rules("
            "id, task_type, escalation_level, days_after_due, evaluation_time, "
            "group_by, minimum_open_invoices, maximum_invoices_per_batch, "
            "auto_complete_empty_batches, notify_enabled, enabled, revision, "
            "created_at, updated_at) VALUES(?, ?, ?, ?, '09:00', 'invoice_range', "
            "1, 1000, 1, 1, 1, 1, ?, ?)",
            ((*item, timestamp, timestamp) for item in defaults),
        )
        connection.execute(
            "UPDATE accounting_escalation_rules SET group_by = 'invoice_range' "
            "WHERE group_by = 'due_date'"
        )
        connection.execute(
            "UPDATE accounting_escalation_rules SET enabled = 0 "
            "WHERE task_type = 'dunning'"
        )

    def _sync_all_projections(self, connection: sqlite3.Connection) -> None:
        """Populate normalized workflow tables from existing alpha records."""
        rows = connection.execute(
            "SELECT collection, record_id, payload, revision, updated_at, "
            "created_at, archived_at FROM records "
            "WHERE collection IN ('topics', 'routines', 'invoices') "
            "ORDER BY CASE collection WHEN 'topics' THEN 1 WHEN 'routines' THEN 2 ELSE 3 END"
        ).fetchall()
        for row in rows:
            self._project_record(connection, *row)

    def _project_record(
        self,
        connection: sqlite3.Connection,
        collection: str,
        record_id: str,
        encoded_payload: str,
        revision: int,
        updated_at: str,
        created_at: str,
        archived_at: str | None,
    ) -> None:
        """Synchronize one generic alpha record into normalized backend tables."""
        payload = json.loads(encoded_payload)
        if collection == "topics":
            connection.execute(
                """INSERT INTO workflow_topics(
                    id, name, description, category, priority, estimated_minutes,
                    instructions, enabled, revision, created_at, updated_at, archived_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, description=excluded.description,
                    category=excluded.category, priority=excluded.priority,
                    estimated_minutes=excluded.estimated_minutes,
                    instructions=excluded.instructions, enabled=excluded.enabled,
                    revision=excluded.revision, updated_at=excluded.updated_at,
                    archived_at=excluded.archived_at""",
                (
                    record_id,
                    payload.get("name", ""),
                    payload.get("description", ""),
                    payload.get("category", ""),
                    payload.get("priority", "normal"),
                    int(payload.get("estimated_minutes", 0)),
                    payload.get("instructions", ""),
                    int(payload.get("enabled", True)),
                    revision,
                    created_at,
                    updated_at,
                    archived_at,
                ),
            )
            connection.execute(
                "DELETE FROM workflow_topic_steps WHERE topic_id = ?", (record_id,)
            )
            for position, raw_step in enumerate(
                payload.get("steps", payload.get("checklist", []))
            ):
                step = raw_step if isinstance(raw_step, dict) else {"title": str(raw_step)}
                connection.execute(
                    "INSERT INTO workflow_topic_steps(id, topic_id, position, title, "
                    "required, estimated_minutes, enabled) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"{record_id}:{step.get('id') or position}",
                        record_id,
                        position,
                        str(step.get("title", "")),
                        int(step.get("required", True)),
                        int(step.get("estimated_minutes", 0)),
                        int(step.get("enabled", True)),
                    ),
                )
            return
        if collection == "routines":
            schedule = payload.get("schedule", {})
            connection.execute(
                """INSERT INTO workflow_routines(
                    id, name, description, timezone, due_time, start_date, end_date,
                    enabled, catch_up_policy, estimated_minutes, priority, revision,
                    created_at, updated_at, archived_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, description=excluded.description,
                    timezone=excluded.timezone, due_time=excluded.due_time,
                    start_date=excluded.start_date, end_date=excluded.end_date,
                    enabled=excluded.enabled, catch_up_policy=excluded.catch_up_policy,
                    estimated_minutes=excluded.estimated_minutes,
                    priority=excluded.priority,
                    revision=excluded.revision, updated_at=excluded.updated_at,
                    archived_at=excluded.archived_at""",
                (
                    record_id,
                    payload.get("name", ""),
                    payload.get("description", ""),
                    payload.get("timezone", "Europe/Berlin"),
                    payload.get("due_time", "09:00"),
                    schedule.get("start_date", created_at[:10]),
                    payload.get("end_date"),
                    int(payload.get("enabled", True)),
                    payload.get("catch_up_policy", "configured_window"),
                    int(payload.get("estimated_minutes", 10)),
                    payload.get("priority", "normal"),
                    revision,
                    created_at,
                    updated_at,
                    archived_at,
                ),
            )
            connection.execute(
                "INSERT OR REPLACE INTO workflow_recurrence_rules("
                "routine_id, frequency, interval_value, weekdays, month_days, months, "
                "explicit_dates, business_day_rule, invalid_day_rule, non_working_dates) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    schedule.get("frequency", "monthly"),
                    int(schedule.get("interval", 1)),
                    json.dumps(schedule.get("weekdays", [])),
                    json.dumps(schedule.get("month_days", [])),
                    json.dumps(schedule.get("months", [])),
                    json.dumps(schedule.get("dates", [])),
                    schedule.get("business_day_rule", "none"),
                    schedule.get("invalid_day_rule", "skip"),
                    json.dumps(schedule.get("non_working_dates", [])),
                ),
            )
            connection.execute(
                "DELETE FROM workflow_routine_topics WHERE routine_id = ?", (record_id,)
            )
            for position, topic_id in enumerate(payload.get("topic_ids", [])):
                if connection.execute(
                    "SELECT 1 FROM workflow_topics WHERE id = ?", (topic_id,)
                ).fetchone():
                    connection.execute(
                        "INSERT INTO workflow_routine_topics(routine_id, topic_id, position) "
                        "VALUES(?, ?, ?)",
                        (record_id, topic_id, position),
                    )
            connection.execute(
                "DELETE FROM workflow_reminders WHERE routine_id = ?", (record_id,)
            )
            for offset in payload.get("reminder_offsets", []):
                connection.execute(
                    "INSERT INTO workflow_reminders(id, routine_id, offset_days, "
                    "reminder_time) VALUES(?, ?, ?, ?)",
                    (
                        f"{record_id}:{offset}",
                        record_id,
                        int(offset),
                        payload.get("due_time", "09:00"),
                    ),
                )
            return
        if collection == "occurrences":
            connection.execute(
                "UPDATE task_occurrences SET status = ?, revision = ?, updated_at = ? "
                "WHERE id = ?",
                (payload.get("status", "open"), revision, updated_at, record_id),
            )
            return
        if collection == "invoices":
            connection.execute(
                """INSERT INTO accounting_invoices(
                    id, invoice_number, invoice_date, due_date, currency, status,
                    gross_cents, outstanding_cents, contact, source, revision,
                    updated_at, archived_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    invoice_number=excluded.invoice_number,
                    invoice_date=excluded.invoice_date, due_date=excluded.due_date,
                    currency=excluded.currency, status=excluded.status,
                    gross_cents=excluded.gross_cents,
                    outstanding_cents=excluded.outstanding_cents,
                    contact=excluded.contact, source=excluded.source,
                    revision=excluded.revision, updated_at=excluded.updated_at,
                    archived_at=excluded.archived_at""",
                (
                    record_id,
                    payload.get("invoice_number", ""),
                    payload.get("invoice_date", ""),
                    payload.get("due_date", ""),
                    payload.get("currency", "EUR"),
                    payload.get("status", "open"),
                    int(payload.get("gross_cents", 0)),
                    int(payload.get("outstanding_cents", payload.get("gross_cents", 0))),
                    payload.get("contact", ""),
                    payload.get("source", "manual"),
                    revision,
                    updated_at,
                    archived_at,
                ),
            )

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
                    if collection == "topics":
                        connection.execute(
                            "UPDATE workflow_topics SET enabled = 0, archived_at = ?, "
                            "updated_at = ? WHERE id = ?",
                            (timestamp, timestamp, removed_id),
                        )
                    elif collection == "routines":
                        connection.execute(
                            "UPDATE workflow_routines SET enabled = 0, archived_at = ?, "
                            "updated_at = ? WHERE id = ?",
                            (timestamp, timestamp, removed_id),
                        )
                    elif collection == "invoices":
                        connection.execute(
                            "UPDATE accounting_invoices SET archived_at = ?, updated_at = ? "
                            "WHERE id = ?",
                            (timestamp, timestamp, removed_id),
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
            self._sync_all_projections(connection)

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
            if collection in {"topics", "routines", "occurrences", "invoices"}:
                self._project_record(
                    connection,
                    collection,
                    record_id,
                    encoded,
                    revision,
                    timestamp,
                    created_at,
                    None,
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
            if collection in {"topics", "routines", "occurrences", "invoices"}:
                self._project_record(
                    connection,
                    collection,
                    record_id,
                    row[2],
                    revision,
                    timestamp,
                    row[5],
                    timestamp,
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
        if collection not in VALID_EDIT_COLLECTIONS:
            raise StructuralOfficeValidationError("Unknown edit collection")
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

    def add_import_batch(
        self,
        batch: dict[str, Any],
        raw_payload: bytes,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record one applied source import and retain its original bytes."""
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO import_batches(
                    import_id, source_name, checksum, imported_at, record_count,
                    created_count, updated_count, unchanged_count, cancelled_count, raw_payload,
                    known_row_count, new_row_count
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch["import_id"],
                    batch["source_name"],
                    batch["checksum"],
                    batch["imported_at"],
                    batch["record_count"],
                    batch["created"],
                    batch["updated"],
                    batch.get("unchanged", 0),
                    batch["cancelled"],
                    raw_payload,
                    batch.get("known_rows", 0),
                    batch.get("new_rows", 0),
                ),
            )
            if rows:
                connection.executemany(
                    "INSERT OR IGNORE INTO invoice_import_rows("
                    "row_fingerprint, import_id, invoice_number, row_number, imported_at) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (
                        (
                            item["fingerprint"],
                            batch["import_id"],
                            item["invoice_number"],
                            int(item["row_number"]),
                            batch["imported_at"],
                        )
                        for item in rows
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

    def known_import_row_fingerprints(self, fingerprints: list[str]) -> set[str]:
        """Return source-row fingerprints already retained by previous imports."""
        if not fingerprints:
            return set()
        known: set[str] = set()
        with self._connect() as connection:
            for start in range(0, len(fingerprints), 500):
                chunk = fingerprints[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"SELECT row_fingerprint FROM invoice_import_rows "
                    f"WHERE row_fingerprint IN ({placeholders})",
                    chunk,
                ).fetchall()
                known.update(row[0] for row in rows)
        return known

    def add_import_rows(
        self, import_id: str, rows: list[dict[str, Any]], imported_at: str
    ) -> int:
        """Persist previously unseen exported booking rows."""
        with self._connect() as connection:
            before = connection.total_changes
            connection.executemany(
                "INSERT OR IGNORE INTO invoice_import_rows("
                "row_fingerprint, import_id, invoice_number, row_number, imported_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (
                    (
                        item["fingerprint"],
                        import_id,
                        item["invoice_number"],
                        int(item["row_number"]),
                        imported_at,
                    )
                    for item in rows
                ),
            )
            return connection.total_changes - before

    def list_import_batches(self, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """Return paginated invoice import history without source payloads."""
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]
            rows = connection.execute(
                "SELECT import_id, source_name, checksum, imported_at, record_count, "
                "created_count, updated_count, unchanged_count, cancelled_count, "
                "known_row_count, new_row_count, length(raw_payload) "
                "FROM import_batches ORDER BY imported_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        keys = (
            "import_id", "source_name", "checksum", "imported_at", "record_count",
            "created", "updated", "unchanged", "cancelled", "known_rows", "new_rows",
            "source_bytes",
        )
        return {
            "items": [dict(zip(keys, row, strict=True)) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    def get_import_batch(self, import_id: str) -> dict[str, Any]:
        """Return one import and its retained row fingerprints."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT import_id, source_name, checksum, imported_at, record_count, "
                "created_count, updated_count, unchanged_count, cancelled_count, "
                "known_row_count, new_row_count, length(raw_payload) "
                "FROM import_batches WHERE import_id = ?",
                (import_id,),
            ).fetchone()
            if row is None:
                raise StructuralOfficeValidationError("Import batch was not found")
            keys = (
                "import_id", "source_name", "checksum", "imported_at", "record_count",
                "created", "updated", "unchanged", "cancelled", "known_rows",
                "new_rows", "source_bytes",
            )
            batch = dict(zip(keys, row, strict=True))
            rows = connection.execute(
                "SELECT row_fingerprint, invoice_number, row_number, imported_at "
                "FROM invoice_import_rows WHERE import_id = ? ORDER BY row_number",
                (import_id,),
            ).fetchall()
        batch["rows"] = [
            {
                "fingerprint": row[0],
                "invoice_number": row[1],
                "row_number": row[2],
                "imported_at": row[3],
            }
            for row in rows
        ]
        return batch

    def read_import_source(self, import_id: str) -> tuple[str, bytes]:
        """Return the retained original source for an administrator."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source_name, raw_payload FROM import_batches WHERE import_id = ?",
                (import_id,),
            ).fetchone()
        if row is None or row[1] is None:
            raise StructuralOfficeValidationError("Import source was not found")
        return row[0], bytes(row[1])

    def materialize_task_occurrences(
        self, occurrences: list[dict[str, Any]], timestamp: str
    ) -> dict[str, int]:
        """Persist generated routine tasks and their checklist snapshots."""
        created = 0
        updated = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for occurrence in occurrences:
                exists = connection.execute(
                    "SELECT status FROM task_occurrences WHERE id = ?",
                    (occurrence["id"],),
                ).fetchone()
                snapshot = json.dumps(
                    {
                        "category": occurrence.get("category", ""),
                        "description": occurrence.get("description", ""),
                        "estimated_minutes": int(occurrence.get("estimated_minutes", 10)),
                        "routine_name": occurrence.get("routine_name", ""),
                        "topic_name": occurrence.get("topic_name", ""),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                due_at = f"{occurrence['due_date']}T{occurrence['due_time']}:00"
                if exists is None:
                    connection.execute(
                        """INSERT INTO task_occurrences(
                            id, routine_id, topic_id, source_type, source_id,
                            scheduled_date, due_at, status, priority, topic_snapshot,
                            estimated_minutes, created_at, updated_at
                        ) VALUES(?, ?, ?, 'routine', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            occurrence["id"],
                            occurrence["routine_id"],
                            occurrence["topic_id"],
                            occurrence["routine_id"],
                            occurrence["due_date"],
                            due_at,
                            occurrence["status"],
                            occurrence.get("priority", "normal"),
                            snapshot,
                            int(occurrence.get("estimated_minutes", 10)),
                            timestamp,
                            timestamp,
                        ),
                    )
                    created += 1
                elif exists[0] == "open":
                    connection.execute(
                        "UPDATE task_occurrences SET due_at = ?, priority = ?, "
                        "topic_snapshot = ?, estimated_minutes = ?, updated_at = ? "
                        "WHERE id = ?",
                        (
                            due_at,
                            occurrence.get("priority", "normal"),
                            snapshot,
                            int(occurrence.get("estimated_minutes", 10)),
                            timestamp,
                            occurrence["id"],
                        ),
                    )
                    updated += 1
                raw_steps = occurrence.get("steps") or [
                    {"title": title} for title in occurrence.get("checklist", [])
                ]
                for position, step in enumerate(raw_steps):
                    item_id = f"{occurrence['id']}:{position}"
                    connection.execute(
                        "INSERT OR IGNORE INTO task_checklist_items("
                        "id, task_id, source_step_id, position, title_snapshot, required) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (
                            item_id,
                            occurrence["id"],
                            f"{occurrence['topic_id']}:{step.get('id') or position}",
                            position,
                            str(step.get("title", "")),
                            int(step.get("required", True)),
                        ),
                    )
        return {"created": created, "updated": updated}

    def evaluate_accounting_tasks(
        self,
        evaluation_date: str,
        timestamp: str,
        evaluation_time: str,
        force: bool = False,
        estimated_minutes: int = 10,
    ) -> dict[str, Any]:
        """Create or refresh one invoice-range task per currency and escalation rule."""
        evaluated = date.fromisoformat(evaluation_date)
        created = 0
        updated = 0
        completed = 0
        notifiable_created = 0
        if not 1 <= estimated_minutes <= 1440:
            raise StructuralOfficeValidationError(
                "Estimated minutes must be between 1 and 1440"
            )
        touched: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rules = connection.execute(
                "SELECT id, task_type, escalation_level, days_after_due, evaluation_time, "
                "minimum_open_invoices, auto_complete_empty_batches, notify_enabled "
                "FROM accounting_escalation_rules WHERE enabled = 1"
            ).fetchall()
            processed_rule_ids: set[str] = set()
            for rule in rules:
                last_evaluation = connection.execute(
                    "SELECT value FROM metadata WHERE key = ?",
                    (f"accounting_last_evaluation:{rule[0]}",),
                ).fetchone()
                if not force and (
                    rule[4] > evaluation_time
                    or (last_evaluation and last_evaluation[0] == evaluation_date)
                ):
                    continue
                processed_rule_ids.add(rule[0])
                cutoff = (evaluated - timedelta(days=rule[3])).isoformat()
                invoices = connection.execute(
                    "SELECT id, due_date, currency, outstanding_cents, invoice_number "
                    "FROM accounting_invoices "
                    "WHERE status = 'open' AND outstanding_cents > 0 AND archived_at IS NULL "
                    "AND due_date <= ? ORDER BY due_date, currency, invoice_number",
                    (cutoff,),
                ).fetchall()
                groups: dict[str, list[tuple[Any, ...]]] = {}
                for invoice in invoices:
                    groups.setdefault(invoice[2], []).append(invoice)
                for currency, members in groups.items():
                    if len(members) < rule[5]:
                        continue
                    source_due_date = min(item[1] for item in members)
                    membership_fingerprint = hashlib.sha256(
                        "\n".join(sorted(item[0] for item in members)).encode()
                    ).hexdigest()
                    batches = connection.execute(
                        "SELECT id, invoice_count_initial, revision, status FROM "
                        "accounting_task_batches WHERE rule_id = ? AND currency = ? "
                        "AND status IN ('open', 'in_progress') "
                        "ORDER BY created_at, id",
                        (rule[0], currency),
                    ).fetchall()
                    batch = batches[0] if batches else None
                    if batch is None:
                        already_handled = connection.execute(
                            "SELECT 1 FROM accounting_task_batches WHERE rule_id = ? "
                            "AND currency = ? AND membership_fingerprint = ? AND status "
                            "NOT IN ('open', 'in_progress') LIMIT 1",
                            (rule[0], currency, membership_fingerprint),
                        ).fetchone()
                        if already_handled:
                            continue
                        batch_id = uuid4().hex
                        dedupe = f"{rule[0]}:{currency}:{batch_id}"
                        connection.execute(
                            """INSERT INTO accounting_task_batches(
                                id, task_type, escalation_level, source_due_date, currency,
                                evaluation_date, due_at, status, invoice_count_initial,
                                invoice_count_open, outstanding_cents, estimated_minutes,
                                created_automatically,
                                rule_id, deduplication_key, membership_fingerprint,
                                created_at, updated_at
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                            (
                                batch_id,
                                rule[1],
                                rule[2],
                                source_due_date,
                                currency,
                                evaluation_date,
                                f"{evaluation_date}T{rule[4]}:00",
                                len(members),
                                len(members),
                                sum(item[3] for item in members),
                                estimated_minutes,
                                rule[0],
                                dedupe,
                                membership_fingerprint,
                                timestamp,
                                timestamp,
                            ),
                        )
                        created += 1
                        notifiable_created += int(bool(rule[7]))
                    else:
                        batch_id = batch[0]
                        connection.execute(
                            "UPDATE accounting_task_batches SET source_due_date = ?, "
                            "evaluation_date = ?, invoice_count_initial = "
                            "MAX(invoice_count_initial, ?), "
                            "membership_fingerprint = ?, updated_at = ?, revision = revision + 1 "
                            "WHERE id = ?",
                            (
                                source_due_date,
                                evaluation_date,
                                len(members),
                                membership_fingerprint,
                                timestamp,
                                batch_id,
                            ),
                        )
                        updated += 1
                        for duplicate in batches[1:]:
                            connection.execute(
                                "DELETE FROM accounting_task_invoices WHERE task_id = ?",
                                (duplicate[0],),
                            )
                            self._refresh_accounting_batch(
                                connection, duplicate[0], timestamp, True
                            )
                    touched.append(batch_id)
                    current_ids = {item[0] for item in members}
                    for invoice_id, _due, _currency, outstanding, _number in members:
                        connection.execute(
                            """INSERT INTO accounting_task_invoices(
                                task_id, invoice_id, outstanding_cents_at_creation,
                                outstanding_cents_current, status, included_at
                            ) VALUES(?, ?, ?, ?, 'open', ?)
                            ON CONFLICT(task_id, invoice_id) DO UPDATE SET
                                outstanding_cents_current=excluded.outstanding_cents_current,
                                status='open', resolved_at=NULL, resolution_reason=NULL,
                                revision=accounting_task_invoices.revision + 1""",
                            (batch_id, invoice_id, outstanding, outstanding, timestamp),
                        )
                    old_members = connection.execute(
                        "SELECT invoice_id FROM accounting_task_invoices WHERE task_id = ?",
                        (batch_id,),
                    ).fetchall()
                    for (invoice_id,) in old_members:
                        if invoice_id in current_ids:
                            continue
                        state = connection.execute(
                            "SELECT status, outstanding_cents FROM accounting_invoices "
                            "WHERE id = ?",
                            (invoice_id,),
                        ).fetchone()
                        reason = state[0] if state else "missing"
                        outstanding = state[1] if state else 0
                        connection.execute(
                            "UPDATE accounting_task_invoices SET status = ?, "
                            "outstanding_cents_current = ?, resolved_at = ?, "
                            "resolution_reason = ?, revision = revision + 1 "
                            "WHERE task_id = ? AND invoice_id = ?",
                            (reason, outstanding, timestamp, reason, batch_id, invoice_id),
                        )
                    self._refresh_accounting_batch(connection, batch_id, timestamp, bool(rule[6]))

                connection.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
                    (f"accounting_last_evaluation:{rule[0]}", evaluation_date),
                )

            open_batches = connection.execute(
                "SELECT id, rule_id FROM accounting_task_batches "
                "WHERE status IN ('open', 'in_progress')"
            ).fetchall()
            for batch_id, rule_id in open_batches:
                if batch_id in touched or rule_id not in processed_rule_ids:
                    continue
                auto_complete = connection.execute(
                    "SELECT auto_complete_empty_batches FROM accounting_escalation_rules "
                    "WHERE id = ?",
                    (rule_id,),
                ).fetchone()
                was_completed = self._refresh_accounting_batch(
                    connection,
                    batch_id,
                    timestamp,
                    bool(auto_complete[0]) if auto_complete else True,
                )
                completed += int(was_completed)
        return {
            "completed": completed,
            "created": created,
            "evaluation_date": evaluation_date,
            "notifiable_created": notifiable_created,
            "updated": updated,
        }

    @staticmethod
    def _refresh_accounting_batch(
        connection: sqlite3.Connection,
        batch_id: str,
        timestamp: str,
        auto_complete: bool,
    ) -> bool:
        members = connection.execute(
            """SELECT link.invoice_id, invoice.status, invoice.outstanding_cents
               FROM accounting_task_invoices AS link
               LEFT JOIN accounting_invoices AS invoice ON invoice.id = link.invoice_id
               WHERE link.task_id = ?""",
            (batch_id,),
        ).fetchall()
        for invoice_id, invoice_status, invoice_outstanding in members:
            status = invoice_status or "missing"
            outstanding = int(invoice_outstanding or 0)
            is_open = status == "open" and outstanding > 0
            connection.execute(
                "UPDATE accounting_task_invoices SET status = ?, "
                "outstanding_cents_current = ?, resolved_at = ?, "
                "resolution_reason = ?, revision = revision + 1 "
                "WHERE task_id = ? AND invoice_id = ?",
                (
                    "open" if is_open else status,
                    outstanding,
                    None if is_open else timestamp,
                    None if is_open else status,
                    batch_id,
                    invoice_id,
                ),
            )
        open_members = [item for item in members if item[1] == "open" and item[2] > 0]
        count = len(open_members)
        outstanding = sum(item[2] for item in open_members)
        previous_status = connection.execute(
            "SELECT status FROM accounting_task_batches WHERE id = ?", (batch_id,)
        ).fetchone()[0]
        completed = auto_complete and count == 0 and previous_status != "auto_completed"
        if auto_complete and count == 0:
            status = "auto_completed"
        elif previous_status == "auto_completed":
            status = "open"
        else:
            status = previous_status
        connection.execute(
            "UPDATE accounting_task_batches SET invoice_count_open = ?, "
            "outstanding_cents = ?, status = ?, completed_at = ?, updated_at = ? "
            "WHERE id = ?",
            (
                count,
                outstanding,
                status,
                timestamp if status in {"auto_completed", "completed"} else None,
                timestamp,
                batch_id,
            ),
        )
        batch = connection.execute(
            "SELECT task_type, escalation_level, source_due_date, due_at, status, "
            "invoice_count_initial, invoice_count_open, outstanding_cents, currency, "
            "estimated_minutes "
            "FROM accounting_task_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        invoice_numbers = [
            row[0]
            for row in connection.execute(
                "SELECT invoice.invoice_number FROM accounting_task_invoices AS link "
                "JOIN accounting_invoices AS invoice ON invoice.id = link.invoice_id "
                "WHERE link.task_id = ? AND invoice.status = 'open' "
                "AND invoice.outstanding_cents > 0 ORDER BY invoice.invoice_number",
                (batch_id,),
            ).fetchall()
        ]
        first_invoice_number = invoice_numbers[0] if invoice_numbers else None
        last_invoice_number = invoice_numbers[-1] if invoice_numbers else None
        invoice_range = (
            first_invoice_number
            if first_invoice_number == last_invoice_number
            else f"{first_invoice_number}-{last_invoice_number}"
            if first_invoice_number and last_invoice_number
            else ""
        )
        task_id = f"accounting:{batch_id}"
        snapshot = json.dumps(
            {
                "category": "Accounting",
                "currency": batch[8],
                "description": (
                    f"{batch[6]} overdue open invoice(s) in range {invoice_range}."
                ),
                "estimated_minutes": batch[9],
                "escalation_level": batch[1],
                "first_invoice_number": first_invoice_number,
                "invoice_count_initial": batch[5],
                "invoice_count_open": batch[6],
                "invoice_range": invoice_range,
                "last_invoice_number": last_invoice_number,
                "outstanding_cents": batch[7],
                "source_due_date": batch[2],
                "task_type": batch[0],
                "topic_name": f"Write payment reminders {invoice_range}",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """INSERT INTO task_occurrences(
                id, source_type, source_id, scheduled_date, due_at, status, priority,
                topic_snapshot, estimated_minutes, created_at, updated_at
            ) VALUES(?, 'accounting_due_batch', ?, ?, ?, ?, 'high', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET due_at=excluded.due_at,
                status=excluded.status, topic_snapshot=excluded.topic_snapshot,
                updated_at=excluded.updated_at, revision=task_occurrences.revision + 1""",
            (
                task_id,
                batch_id,
                batch[2],
                batch[3],
                batch[4],
                snapshot,
                batch[9],
                timestamp,
                timestamp,
            ),
        )
        return completed

    def list_accounting_task_batches(
        self, *, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """Return grouped accounting tasks without expanding invoice payloads."""
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        where = "WHERE status = ?" if status else ""
        params: tuple[Any, ...] = (status,) if status else ()
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM accounting_task_batches {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT id, task_type, escalation_level, source_due_date, currency, "
                "evaluation_date, due_at, status, invoice_count_initial, "
                "invoice_count_open, outstanding_cents, estimated_minutes, "
                "created_automatically, "
                "rule_id, created_at, updated_at, completed_at, revision "
                f"FROM accounting_task_batches {where} ORDER BY due_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        keys = (
            "id", "task_type", "escalation_level", "source_due_date", "currency",
            "evaluation_date", "due_at", "status", "invoice_count_initial",
            "invoice_count_open", "outstanding_cents", "estimated_minutes",
            "created_automatically",
            "rule_id", "created_at", "updated_at", "completed_at", "revision",
        )
        return {
            "items": [dict(zip(keys, row, strict=True)) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    def accounting_task_invoice_ids(self, batch_id: str) -> list[dict[str, Any]]:
        """Return exact invoice membership for one grouped accounting task."""
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM accounting_task_batches WHERE id = ?", (batch_id,)
            ).fetchone() is None:
                raise StructuralOfficeValidationError("Accounting task was not found")
            rows = connection.execute(
                "SELECT invoice_id, outstanding_cents_at_creation, "
                "outstanding_cents_current, status, included_at, resolved_at, "
                "resolution_reason, revision FROM accounting_task_invoices "
                "WHERE task_id = ? ORDER BY invoice_id",
                (batch_id,),
            ).fetchall()
        keys = (
            "invoice_id", "outstanding_cents_at_creation", "outstanding_cents_current",
            "status", "included_at", "resolved_at", "resolution_reason", "revision",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def list_materialized_tasks(
        self,
        *,
        status: str | None = None,
        source_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return persisted routine and accounting tasks."""
        if status and status not in VALID_STATUSES:
            raise StructuralOfficeValidationError("Invalid task status")
        if source_type and source_type not in {"accounting_due_batch", "manual", "routine"}:
            raise StructuralOfficeValidationError("Invalid task source type")
        limit = max(1, min(500, limit))
        offset = max(0, offset)
        clauses = ["archived_at IS NULL"]
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        where = " AND ".join(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM task_occurrences WHERE {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT id, routine_id, topic_id, source_type, source_id, scheduled_date, "
                "due_at, status, priority, topic_snapshot, started_at, completed_at, "
                "completed_by, completion_note, estimated_minutes, revision, created_at, "
                "updated_at "
                f"FROM task_occurrences WHERE {where} "
                "ORDER BY due_at, id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        keys = (
            "id", "routine_id", "topic_id", "source_type", "source_id",
            "scheduled_date", "due_at", "status", "priority", "snapshot",
            "started_at", "completed_at", "completed_by", "completion_note",
            "estimated_minutes", "revision", "created_at", "updated_at",
        )
        items = [dict(zip(keys, row, strict=True)) for row in rows]
        for item in items:
            item["snapshot"] = json.loads(item["snapshot"])
        return {"items": items, "limit": limit, "offset": offset, "total": total}

    def get_materialized_task(self, task_id: str) -> dict[str, Any]:
        """Return one materialized task and its checklist."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, routine_id, topic_id, source_type, source_id, scheduled_date, "
                "due_at, status, priority, topic_snapshot, started_at, completed_at, "
                "completed_by, completion_note, estimated_minutes, revision, created_at, "
                "updated_at, archived_at "
                "FROM task_occurrences WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise StructuralOfficeValidationError("Task was not found")
            keys = (
                "id", "routine_id", "topic_id", "source_type", "source_id",
                "scheduled_date", "due_at", "status", "priority", "snapshot",
                "started_at", "completed_at", "completed_by", "completion_note",
                "estimated_minutes", "revision", "created_at", "updated_at", "archived_at",
            )
            task = dict(zip(keys, row, strict=True))
            task["snapshot"] = json.loads(task["snapshot"])
            checklist_rows = connection.execute(
                "SELECT id, source_step_id, position, title_snapshot, required, completed, "
                "completed_at, completed_by, note, revision FROM task_checklist_items "
                "WHERE task_id = ? ORDER BY position, id",
                (task_id,),
            ).fetchall()
        checklist_keys = (
            "id", "source_step_id", "position", "title", "required", "completed",
            "completed_at", "completed_by", "note", "revision",
        )
        task["checklist"] = [
            dict(zip(checklist_keys, item, strict=True)) for item in checklist_rows
        ]
        for item in task["checklist"]:
            item["completed"] = bool(item["completed"])
            item["required"] = bool(item["required"])
        return task

    def today_dashboard(self, today: str) -> dict[str, Any]:
        """Return today's open workload and the longest due task."""
        try:
            date.fromisoformat(today)
        except ValueError as err:
            raise StructuralOfficeValidationError("Dashboard date must use YYYY-MM-DD") from err
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, source_type, due_at, status, priority, topic_snapshot, "
                "estimated_minutes FROM task_occurrences WHERE archived_at IS NULL "
                "AND status IN ('open', 'in_progress') AND scheduled_date <= ? "
                "ORDER BY estimated_minutes DESC, due_at, "
                "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'normal' THEN 2 ELSE 3 END, id",
                (today,),
            ).fetchall()
        tasks = []
        for row in rows:
            snapshot = json.loads(row[5])
            tasks.append(
                {
                    "due_at": row[2],
                    "estimated_minutes": row[6],
                    "id": row[0],
                    "priority": row[4],
                    "source_type": row[1],
                    "status": row[3],
                    "title": snapshot.get("topic_name", ""),
                }
            )
        return {
            "date": today,
            "estimated_minutes_total": sum(
                item["estimated_minutes"] for item in tasks
            ),
            "longest_task": tasks[0] if tasks else None,
            "open_task_count": len(tasks),
        }

    def create_manual_task(
        self, raw: dict[str, Any], timestamp: str, user_id: str, user_name: str
    ) -> dict[str, Any]:
        """Create one standalone task for an authorized client."""
        title = str(raw.get("title") or "").strip()
        if not title:
            raise StructuralOfficeValidationError("Task title is required")
        due_at = str(raw.get("due_at") or "").strip()
        try:
            scheduled_date = datetime.fromisoformat(due_at).date().isoformat()
        except ValueError as err:
            raise StructuralOfficeValidationError("Task due_at must be ISO 8601") from err
        priority = str(raw.get("priority", "normal"))
        if priority not in {"low", "normal", "high", "critical"}:
            raise StructuralOfficeValidationError("Invalid task priority")
        if "estimated_minutes" not in raw:
            raise StructuralOfficeValidationError("Task estimated_minutes is required")
        estimated_minutes = int(raw["estimated_minutes"])
        if not 1 <= estimated_minutes <= 1440:
            raise StructuralOfficeValidationError(
                "Estimated minutes must be between 1 and 1440"
            )
        checklist = raw.get("checklist", [])
        if not isinstance(checklist, list) or len(checklist) > 100:
            raise StructuralOfficeValidationError("Task checklist is invalid")
        task_id = str(raw.get("id") or uuid4().hex)
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id) is None:
            raise StructuralOfficeValidationError("Task ID contains unsupported characters")
        snapshot = json.dumps(
            {
                "category": str(raw.get("category") or ""),
                "description": str(raw.get("description") or ""),
                "estimated_minutes": estimated_minutes,
                "topic_name": title,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO task_occurrences(id, source_type, scheduled_date, due_at, "
                    "status, priority, topic_snapshot, estimated_minutes, created_at, "
                    "updated_at) VALUES(?, 'manual', ?, ?, 'open', ?, ?, ?, ?, ?)",
                    (
                        task_id, scheduled_date, due_at, priority, snapshot,
                        estimated_minutes, timestamp, timestamp,
                    ),
                )
            except sqlite3.IntegrityError as err:
                raise StructuralOfficeValidationError("Task ID already exists") from err
            for position, raw_item in enumerate(checklist):
                item = raw_item if isinstance(raw_item, dict) else {"title": raw_item}
                item_title = str(item.get("title") or "").strip()
                if not item_title:
                    raise StructuralOfficeValidationError("Checklist title is required")
                connection.execute(
                    "INSERT INTO task_checklist_items(id, task_id, position, title_snapshot, "
                    "required) VALUES(?, ?, ?, ?, ?)",
                    (
                        f"{task_id}:{position}", task_id, position, item_title,
                        int(bool(item.get("required", True))),
                    ),
                )
            self._record_change(
                connection, "tasks", task_id, "created", 1, user_id, user_name,
                ["checklist", "due_at", "estimated_minutes", "priority", "title"],
                raw,
                timestamp,
            )
        return self.get_materialized_task(task_id)

    def update_materialized_task(
        self,
        task_id: str,
        changes: dict[str, Any],
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Update task state and scheduling with optimistic concurrency control."""
        allowed = {"completion_note", "due_at", "estimated_minutes", "priority", "status"}
        if not changes or (unknown := set(changes) - allowed):
            detail = f": {', '.join(sorted(unknown))}" if changes and unknown else ""
            raise StructuralOfficeValidationError(f"Unsupported or empty task update{detail}")
        normalized = dict(changes)
        if "status" in normalized:
            normalized["status"] = str(normalized["status"])
            if normalized["status"] not in VALID_STATUSES - {"auto_completed"}:
                raise StructuralOfficeValidationError("Invalid task status")
        if "priority" in normalized:
            normalized["priority"] = str(normalized["priority"])
            if normalized["priority"] not in {"low", "normal", "high", "critical"}:
                raise StructuralOfficeValidationError("Invalid task priority")
        if "completion_note" in normalized:
            normalized["completion_note"] = str(normalized["completion_note"])[:5000]
        if "estimated_minutes" in normalized:
            normalized["estimated_minutes"] = int(normalized["estimated_minutes"])
            if not 1 <= normalized["estimated_minutes"] <= 1440:
                raise StructuralOfficeValidationError(
                    "Estimated minutes must be between 1 and 1440"
                )
        if "due_at" in normalized:
            try:
                datetime.fromisoformat(str(normalized["due_at"]))
            except ValueError as err:
                raise StructuralOfficeValidationError("Task due_at must be ISO 8601") from err
            normalized["due_at"] = str(normalized["due_at"])
        timestamp = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision, source_type, source_id, status, started_at, "
                "completed_at, completed_by "
                "FROM task_occurrences WHERE id = ? AND archived_at IS NULL",
                (task_id,),
            ).fetchone()
            if row is None:
                raise StructuralOfficeValidationError("Task was not found")
            if row[0] != expected_revision:
                raise StructuralOfficeConflictError(self.get_materialized_task(task_id))
            status = normalized.get("status", row[3])
            started_at = row[4] or (timestamp if status == "in_progress" else None)
            terminal = status in {"completed", "skipped", "cancelled", "auto_completed"}
            completed_at = (row[5] or timestamp) if terminal else None
            completed_by = (row[6] or user_id) if terminal else None
            assignments = [f"{key} = ?" for key in normalized]
            values = list(normalized.values())
            if "due_at" in normalized:
                assignments.append("scheduled_date = ?")
                values.append(datetime.fromisoformat(normalized["due_at"]).date().isoformat())
            assignments.extend(
                [
                    "started_at = ?",
                    "completed_at = ?",
                    "completed_by = ?",
                    "updated_at = ?",
                    "revision = revision + 1",
                ]
            )
            values.extend(
                [
                    started_at,
                    completed_at,
                    completed_by,
                    timestamp,
                    task_id,
                ]
            )
            connection.execute(
                f"UPDATE task_occurrences SET {', '.join(assignments)} WHERE id = ?", values
            )
            if row[1] == "accounting_due_batch" and row[2]:
                batch_updates: dict[str, Any] = {}
                if "status" in normalized:
                    batch_updates["status"] = status
                    batch_updates["completed_at"] = completed_at
                if "due_at" in normalized:
                    batch_updates["due_at"] = normalized["due_at"]
                if batch_updates:
                    batch_assignments = ", ".join(f"{key} = ?" for key in batch_updates)
                    connection.execute(
                        f"UPDATE accounting_task_batches SET {batch_assignments}, "
                        "updated_at = ?, revision = revision + 1 WHERE id = ?",
                        (*batch_updates.values(), timestamp, row[2]),
                    )
            current = connection.execute(
                "SELECT revision FROM task_occurrences WHERE id = ?", (task_id,)
            ).fetchone()[0]
            self._record_change(
                connection, "tasks", task_id, "updated", current, user_id, user_name,
                sorted(normalized), normalized, timestamp,
            )
        return self.get_materialized_task(task_id)

    def update_task_checklist_item(
        self,
        task_id: str,
        item_id: str,
        changes: dict[str, Any],
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Update one task checklist item with optimistic concurrency control."""
        allowed = {"completed", "note"}
        if not changes or set(changes) - allowed:
            raise StructuralOfficeValidationError("Unsupported or empty checklist update")
        timestamp = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM task_checklist_items WHERE id = ? AND task_id = ?",
                (item_id, task_id),
            ).fetchone()
            if row is None:
                raise StructuralOfficeValidationError("Checklist item was not found")
            if row[0] != expected_revision:
                current = next(
                    item
                    for item in self.get_materialized_task(task_id)["checklist"]
                    if item["id"] == item_id
                )
                raise StructuralOfficeConflictError(current)
            completed = int(bool(changes.get("completed", False)))
            note = str(changes.get("note", ""))[:5000]
            connection.execute(
                "UPDATE task_checklist_items SET completed = ?, completed_at = ?, "
                "completed_by = ?, note = ?, revision = revision + 1 WHERE id = ?",
                (
                    completed, timestamp if completed else None,
                    user_id if completed else None, note, item_id,
                ),
            )
            revision = row[0] + 1
            self._record_change(
                connection, "task_checklist", item_id, "updated", revision,
                user_id, user_name, sorted(changes), changes, timestamp,
            )
        return next(
            item
            for item in self.get_materialized_task(task_id)["checklist"]
            if item["id"] == item_id
        )

    def reminder_was_delivered(self, delivery_id: str) -> bool:
        """Return whether a reminder delivery was committed successfully."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM reminder_deliveries WHERE id = ?", (delivery_id,)
            ).fetchone()
        return row is not None and row[0] == "sent"

    def record_reminder_delivery(
        self,
        delivery_id: str,
        task_id: str,
        routine_id: str,
        offset_days: int,
        scheduled_at: str,
        sent_at: str,
    ) -> None:
        """Persist a successful notification delivery for restart-safe deduplication."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO reminder_deliveries(id, task_id, routine_id, offset_days, "
                "scheduled_at, status, sent_at, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, 'sent', ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status='sent', sent_at=excluded.sent_at, "
                "error=NULL, updated_at=excluded.updated_at",
                (
                    delivery_id, task_id, routine_id, offset_days, scheduled_at,
                    sent_at, sent_at, sent_at,
                ),
            )

    def list_accounting_rules(self) -> list[dict[str, Any]]:
        """Return accounting task-generation rules."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, task_type, escalation_level, days_after_due, evaluation_time, "
                "group_by, minimum_open_invoices, maximum_invoices_per_batch, "
                "auto_complete_empty_batches, notify_enabled, enabled, revision, "
                "created_at, updated_at FROM accounting_escalation_rules "
                "ORDER BY days_after_due, escalation_level"
            ).fetchall()
        keys = (
            "id", "task_type", "escalation_level", "days_after_due",
            "evaluation_time", "group_by", "minimum_open_invoices",
            "maximum_invoices_per_batch", "auto_complete_empty_batches",
            "notify_enabled", "enabled", "revision", "created_at", "updated_at",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def update_accounting_rule(
        self,
        rule_id: str,
        changes: dict[str, Any],
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Update a task-generation rule with revision protection."""
        allowed = {
            "auto_complete_empty_batches",
            "days_after_due",
            "enabled",
            "evaluation_time",
            "minimum_open_invoices",
            "notify_enabled",
        }
        if not changes:
            raise StructuralOfficeValidationError("Accounting rule update must not be empty")
        if unknown := set(changes) - allowed:
            raise StructuralOfficeValidationError(
                f"Unsupported accounting rule fields: {', '.join(sorted(unknown))}"
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM accounting_escalation_rules WHERE id = ?",
                (rule_id,),
            ).fetchone()
            if row is None:
                raise StructuralOfficeValidationError("Accounting rule was not found")
            if row[0] != expected_revision:
                current = next(
                    item for item in self.list_accounting_rules() if item["id"] == rule_id
                )
                raise StructuralOfficeConflictError(current)
            normalized: dict[str, Any] = {}
            for key, value in changes.items():
                if key in {"days_after_due", "minimum_open_invoices"}:
                    normalized[key] = int(value)
                elif key in {"enabled", "notify_enabled", "auto_complete_empty_batches"}:
                    if not isinstance(value, bool):
                        raise StructuralOfficeValidationError(f"{key} must be a boolean")
                    normalized[key] = int(value)
                else:
                    try:
                        datetime.strptime(str(value), "%H:%M")
                    except ValueError as err:
                        raise StructuralOfficeValidationError(
                            "Evaluation time must use HH:MM"
                        ) from err
                    normalized[key] = str(value)
            if normalized.get("days_after_due", 0) < 0:
                raise StructuralOfficeValidationError("Days after due must not be negative")
            if normalized.get("minimum_open_invoices", 1) < 1:
                raise StructuralOfficeValidationError("Minimum open invoices must be positive")
            timestamp = datetime.now(UTC).isoformat()
            assignments = ", ".join(f"{key} = ?" for key in normalized)
            if assignments:
                connection.execute(
                    f"UPDATE accounting_escalation_rules SET {assignments}, "
                    "revision = revision + 1, updated_at = ? WHERE id = ?",
                    (*normalized.values(), timestamp, rule_id),
                )
            updated = connection.execute(
                "SELECT revision FROM accounting_escalation_rules WHERE id = ?",
                (rule_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO audit_log(action, collection, record_id, revision, user_id, "
                "user_name, details, created_at) VALUES('updated', 'accounting_rules', "
                "?, ?, ?, ?, ?, ?)",
                (
                    rule_id,
                    updated,
                    user_id,
                    user_name,
                    json.dumps(sorted(normalized)),
                    timestamp,
                ),
            )
        return next(item for item in self.list_accounting_rules() if item["id"] == rule_id)

    def statistics(self) -> dict[str, Any]:
        """Return non-sensitive database health and size statistics."""
        with self._connect() as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            schema_version = connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()[0]
            record_count = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
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
