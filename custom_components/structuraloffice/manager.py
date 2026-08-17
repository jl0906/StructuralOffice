"""Data management and reminder scheduling for StructuralOffice."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .accounting import (
    accounting_summary,
    invoice_for_frontend,
    validate_invoice,
)
from .analytics import build_accounting_analytics, build_workflow_analytics
from .const import (
    BACKUP_DIRECTORY,
    CONF_CATCH_UP_HOURS,
    CONF_COMPANY_ADDRESS,
    CONF_COMPANY_EMAIL,
    CONF_COMPANY_NAME,
    CONF_DEFAULT_PAYMENT_TERM_DAYS,
    CONF_DEFAULT_REMINDER_TIME,
    CONF_NOTIFY_TARGETS,
    CONF_PAYMENT_REMINDER_ESTIMATED_MINUTES,
    CONF_SEPA_DATE_AS_DUE_DATE,
    DATABASE_DIRECTORY,
    DATABASE_FILENAME,
    DEFAULT_CATCH_UP_HOURS,
    DEFAULT_COMPANY_ADDRESS,
    DEFAULT_COMPANY_EMAIL,
    DEFAULT_COMPANY_NAME,
    DEFAULT_PAYMENT_REMINDER_ESTIMATED_MINUTES,
    DEFAULT_PAYMENT_TERM_DAYS,
    DEFAULT_REMINDER_TIME,
    DEFAULT_SEPA_DATE_AS_DUE_DATE,
    DOMAIN,
    INTEGRATION_VERSION,
    LIVE_UPDATE_EVENT,
    SCHEDULER_INTERVAL,
    STATUS_OPEN,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    UPDATE_EVENT,
    VALID_STATUSES,
)
from .database import StructuralOfficeDatabase
from .invoice_csv import parse_invoice_list_csv
from .models import (
    StructuralOfficeValidationError,
    iter_due_dates,
    occurrence_id,
    validate_contact,
    validate_routine,
    validate_topic,
)

_LOGGER = logging.getLogger(__name__)


class StructuralOfficeManager:
    """Own StructuralOffice data and scheduled reminders."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        database_path: Path | None = None,
        backup_directory: Path | None = None,
        legacy_storage: bool = True,
        tenant_user_id: str | None = None,
        shared_notifications: bool = True,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )
        database_directory = Path(hass.config.path(DATABASE_DIRECTORY))
        self.database = StructuralOfficeDatabase(
            database_path or database_directory / DATABASE_FILENAME,
            backup_directory or database_directory / BACKUP_DIRECTORY,
        )
        self.legacy_storage = legacy_storage
        self.tenant_user_id = tenant_user_id
        self.shared_notifications = shared_notifications
        self.data: dict[str, Any] = self._empty_data()
        self.database_stats: dict[str, Any] = {}
        self.backups: list[dict[str, Any]] = []
        self._listeners: set[Callable[[], None]] = set()
        self._remove_timer: Callable[[], None] | None = None
        self._last_materialized_date: date | None = None

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        return {
            "contacts": {},
            "topics": {},
            "routines": {},
            "occurrences": {},
            "notifications": {},
            "invoices": {},
            "user_roles": {},
        }

    async def async_initialize(self) -> None:
        """Load the dedicated database, migrating legacy JSON storage once."""
        await self.hass.async_add_executor_job(self.database.initialize)
        loaded = await self.hass.async_add_executor_job(
            self.database.load, set(self.data)
        )
        if any(loaded.values()):
            self.data.update(loaded)
        else:
            legacy = await self.store.async_load() if self.legacy_storage else None
            if isinstance(legacy, dict):
                for key in self.data:
                    if isinstance(legacy.get(key), dict):
                        self.data[key] = legacy[key]
                if any(self.data.values()):
                    await self.hass.async_add_executor_job(
                        self.database.save, self.data
                    )
                    _LOGGER.info("Migrated legacy StructuralOffice storage to SQLite")
        await self._async_refresh_database_stats()
        await self.async_materialize_workflow_tasks(force=True)
        await self.async_evaluate_accounting_tasks(force=True)
        self._remove_timer = async_track_time_interval(
            self.hass, self._async_timer, SCHEDULER_INTERVAL
        )
        self.hass.async_create_task(self.async_process_reminders())

    async def async_shutdown(self) -> None:
        """Stop timers and persist the current state."""
        if self._remove_timer is not None:
            self._remove_timer()
            self._remove_timer = None
        await self.hass.async_add_executor_job(self.database.save, self.data)

    async def _async_timer(self, _now: datetime) -> None:
        await self.async_materialize_workflow_tasks()
        await self.async_evaluate_accounting_tasks()
        await self.async_process_reminders()
        for listener in list(self._listeners):
            listener()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Listen for data changes."""
        self._listeners.add(listener)

        def remove() -> None:
            self._listeners.discard(listener)

        return remove

    @callback
    def _notify_changed(self) -> None:
        for listener in list(self._listeners):
            listener()
        self.hass.bus.async_fire(UPDATE_EVENT, self._tenant_payload({}))

    def _tenant_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach the private tenant identifier to an internal event."""
        return {**payload, "tenant_user_id": self.tenant_user_id}

    async def _async_save(self) -> None:
        await self.hass.async_add_executor_job(self.database.save, self.data)
        await self._async_refresh_database_stats()
        self._notify_changed()
        self.hass.bus.async_fire(
            LIVE_UPDATE_EVENT,
            self._tenant_payload({"operation": "refresh", "reason": "legacy_write"}),
        )

    async def _async_refresh_database_stats(self) -> None:
        self.database_stats = await self.hass.async_add_executor_job(
            self.database.statistics
        )
        self.backups = await self.hass.async_add_executor_job(self.database.list_backups)

    @property
    def options(self) -> dict[str, Any]:
        """Return normalized config options."""
        return {
            CONF_NOTIFY_TARGETS: (
                list(self.entry.options.get(CONF_NOTIFY_TARGETS, []))
                if self.shared_notifications
                else []
            ),
            CONF_DEFAULT_REMINDER_TIME: self.entry.options.get(
                CONF_DEFAULT_REMINDER_TIME, DEFAULT_REMINDER_TIME
            ),
            CONF_CATCH_UP_HOURS: int(
                self.entry.options.get(CONF_CATCH_UP_HOURS, DEFAULT_CATCH_UP_HOURS)
            ),
            CONF_DEFAULT_PAYMENT_TERM_DAYS: int(
                self.entry.options.get(
                    CONF_DEFAULT_PAYMENT_TERM_DAYS, DEFAULT_PAYMENT_TERM_DAYS
                )
            ),
            CONF_SEPA_DATE_AS_DUE_DATE: bool(
                self.entry.options.get(
                    CONF_SEPA_DATE_AS_DUE_DATE, DEFAULT_SEPA_DATE_AS_DUE_DATE
                )
            ),
            CONF_COMPANY_NAME: str(
                self.entry.options.get(CONF_COMPANY_NAME, DEFAULT_COMPANY_NAME)
            ),
            CONF_COMPANY_ADDRESS: str(
                self.entry.options.get(CONF_COMPANY_ADDRESS, DEFAULT_COMPANY_ADDRESS)
            ),
            CONF_COMPANY_EMAIL: str(
                self.entry.options.get(CONF_COMPANY_EMAIL, DEFAULT_COMPANY_EMAIL)
            ),
            CONF_PAYMENT_REMINDER_ESTIMATED_MINUTES: int(
                self.entry.options.get(
                    CONF_PAYMENT_REMINDER_ESTIMATED_MINUTES,
                    DEFAULT_PAYMENT_REMINDER_ESTIMATED_MINUTES,
                )
            ),
        }

    def _validate_live_record(
        self, collection: str, raw: dict[str, Any], record_id: str | None
    ) -> dict[str, Any]:
        """Validate a record written by a live Windows client."""
        value = dict(raw)
        if record_id:
            value["id"] = record_id
        if collection == "contacts":
            return validate_contact(value, record_id)
        if collection == "topics":
            return validate_topic(value, record_id)
        if collection == "routines":
            return validate_routine(value, set(self.data["topics"]), record_id)
        if collection == "invoices":
            return validate_invoice(value, record_id)
        if collection == "occurrences":
            if not record_id or not self._occurrence_exists(record_id):
                raise StructuralOfficeValidationError("Task was not found")
            status = str(value.get("status", ""))
            if status not in VALID_STATUSES:
                raise StructuralOfficeValidationError("Invalid status")
            return {
                "status": status,
                "updated_at": dt_util.utcnow().isoformat(),
            }
        raise StructuralOfficeValidationError("Unknown record collection")

    async def async_live_list(
        self,
        collection: str,
        *,
        include_archived: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return a page of revisioned live records."""
        if collection == "occurrences":
            today = dt_util.now().date()
            generated = self.build_occurrences(
                today - timedelta(days=365), today + timedelta(days=365)
            )
            states = await self.hass.async_add_executor_job(
                partial(
                    self.database.list_live_records,
                    collection,
                    include_archived=include_archived,
                    limit=500,
                    offset=0,
                )
            )
            revisions = {item["id"]: item for item in states["items"]}
            items = []
            for occurrence in generated:
                state = revisions.get(occurrence["id"])
                items.append(
                    {
                        "archived_at": None,
                        "collection": collection,
                        "created_at": state["created_at"] if state else None,
                        "data": occurrence,
                        "id": occurrence["id"],
                        "revision": state["revision"] if state else 0,
                        "updated_at": state["updated_at"] if state else None,
                    }
                )
            return {
                "items": items[offset : offset + limit],
                "limit": limit,
                "offset": offset,
                "total": len(items),
            }
        return await self.hass.async_add_executor_job(
            partial(
                self.database.list_live_records,
                collection,
                include_archived=include_archived,
                limit=limit,
                offset=offset,
            )
        )

    async def async_live_get(self, collection: str, record_id: str) -> dict[str, Any]:
        """Return one revisioned live record."""
        result = await self.hass.async_add_executor_job(
            self.database.get_live_record, collection, record_id
        )
        if collection == "occurrences":
            try:
                raw_date = record_id.rsplit(":", 1)[1]
                due = date.fromisoformat(raw_date)
            except (IndexError, ValueError) as err:
                raise StructuralOfficeValidationError("Task was not found") from err
            occurrence = next(
                (item for item in self.build_occurrences(due, due) if item["id"] == record_id),
                None,
            )
            if occurrence:
                if result:
                    occurrence["status"] = result["data"].get(
                        "status", occurrence["status"]
                    )
                return {
                    "archived_at": result["archived_at"] if result else None,
                    "collection": collection,
                    "created_at": result["created_at"] if result else None,
                    "data": occurrence,
                    "id": record_id,
                    "revision": result["revision"] if result else 0,
                    "updated_at": result["updated_at"] if result else None,
                }
        if result is None:
            raise StructuralOfficeValidationError("Record was not found")
        return result

    async def async_live_write(
        self,
        collection: str,
        raw: dict[str, Any],
        record_id: str | None,
        expected_revision: int | None,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Validate and commit one live record change."""
        merged = dict(self.data.get(collection, {}).get(record_id, {}))
        merged.update(raw)
        normalized = self._validate_live_record(collection, merged, record_id)
        record_id = normalized.get("id") or record_id
        if not record_id:
            raise StructuralOfficeValidationError("Record ID is required")
        result = await self.hass.async_add_executor_job(
            self.database.write_live_record,
            collection,
            record_id,
            normalized,
            expected_revision,
            user_id,
            user_name,
            set(raw),
        )
        self.data[collection][record_id] = result["data"]
        if collection in {"topics", "routines"}:
            await self.async_materialize_workflow_tasks(force=True)
        elif collection == "invoices":
            await self.async_evaluate_accounting_tasks(force=True)
        await self._async_refresh_database_stats()
        self._fire_live_event(result)
        return result

    async def async_live_archive(
        self,
        collection: str,
        record_id: str,
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Archive a live record after applying business constraints."""
        if collection == "topics":
            used_by = [
                item["name"]
                for item in self.data["routines"].values()
                if record_id in item["topic_ids"]
            ]
            if used_by:
                raise StructuralOfficeValidationError(
                    f"Topic is still in use: {', '.join(used_by)}"
                )
        result = await self.hass.async_add_executor_job(
            self.database.archive_live_record,
            collection,
            record_id,
            expected_revision,
            user_id,
            user_name,
        )
        self.data[collection].pop(record_id, None)
        await self._async_refresh_database_stats()
        self._fire_live_event(result)
        return result

    def _fire_live_event(self, result: dict[str, Any]) -> None:
        event = {
            "changed_fields": result.get("changed_fields", []),
            "collection": result["collection"],
            "operation": result["operation"],
            "record_id": result["id"],
            "revision": result["revision"],
            "sequence": result["event_sequence"],
        }
        self.hass.bus.async_fire(LIVE_UPDATE_EVENT, self._tenant_payload(event))
        self._notify_changed()

    async def async_start_edit_session(
        self,
        collection: str,
        record_id: str,
        client_id: str,
        user_id: str,
        user_name: str,
        ttl_seconds: int,
        session_id: str | None,
    ) -> dict[str, Any]:
        """Start or refresh a soft edit-presence session."""
        result = await self.hass.async_add_executor_job(
            self.database.start_edit_session,
            collection,
            record_id,
            client_id,
            user_id,
            user_name,
            ttl_seconds,
            session_id,
        )
        self.hass.bus.async_fire(
            LIVE_UPDATE_EVENT,
            self._tenant_payload({
                "collection": collection,
                "editors": result["editors"],
                "operation": "presence",
                "record_id": record_id,
            }),
        )
        return result

    async def async_end_edit_session(
        self, collection: str, record_id: str, session_id: str, user_id: str
    ) -> bool:
        """End an edit-presence session."""
        ended = await self.hass.async_add_executor_job(
            self.database.end_edit_session, session_id, user_id
        )
        editors = await self.hass.async_add_executor_job(
            self.database.active_edit_sessions, collection, record_id
        )
        self.hass.bus.async_fire(
            LIVE_UPDATE_EVENT,
            self._tenant_payload({
                "collection": collection,
                "editors": editors,
                "operation": "presence",
                "record_id": record_id,
            }),
        )
        return ended

    async def async_events_since(self, after: int, limit: int) -> dict[str, Any]:
        """Return persisted changes for reconnecting clients."""
        return await self.hass.async_add_executor_job(
            self.database.events_since, after, limit
        )

    async def async_audit_entries(self, limit: int, offset: int) -> dict[str, Any]:
        """Return revision audit metadata."""
        return await self.hass.async_add_executor_job(
            self.database.audit_entries, limit, offset
        )

    async def async_list_materialized_tasks(
        self,
        *,
        status: str | None,
        source_type: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        """Return persisted routine and accounting tasks."""
        return await self.hass.async_add_executor_job(
            partial(
                self.database.list_materialized_tasks,
                status=status,
                source_type=source_type,
                limit=limit,
                offset=offset,
            )
        )

    async def async_get_materialized_task(self, task_id: str) -> dict[str, Any]:
        """Return one task with its checklist."""
        return await self.hass.async_add_executor_job(
            self.database.get_materialized_task, task_id
        )

    async def async_today_dashboard(self) -> dict[str, Any]:
        """Return the local day's task workload summary."""
        today = dt_util.now().date().isoformat()
        return await self.hass.async_add_executor_job(
            self.database.today_dashboard, today
        )

    async def async_schedule_dunning_task(
        self,
        task_id: str,
        expected_revision: int,
        due_date: str,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Complete a payment reminder and schedule its dunning follow-up."""
        try:
            selected_due_date = date.fromisoformat(due_date)
        except ValueError as err:
            raise StructuralOfficeValidationError(
                "Dunning due date must use YYYY-MM-DD"
            ) from err
        if selected_due_date < dt_util.now().date():
            raise StructuralOfficeValidationError(
                "Dunning due date must not be in the past"
            )
        result = await self.hass.async_add_executor_job(
            self.database.schedule_dunning_task,
            task_id,
            expected_revision,
            due_date,
            self.options[CONF_PAYMENT_REMINDER_ESTIMATED_MINUTES],
            dt_util.utcnow().isoformat(),
            user_id,
            user_name,
        )
        self._fire_task_event(
            task_id, "completed", result["completed_task"]["revision"]
        )
        self._fire_task_event(
            result["dunning_task"]["id"],
            "created",
            result["dunning_task"]["revision"],
        )
        return result

    async def async_confirm_accounting_task_settled(
        self,
        task_id: str,
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Complete an invoice task after explicit settlement confirmation."""
        result = await self.hass.async_add_executor_job(
            self.database.confirm_accounting_task_settled,
            task_id,
            expected_revision,
            dt_util.utcnow().isoformat(),
            user_id,
            user_name,
        )
        self._fire_task_event(task_id, "settlement_confirmed", result["revision"])
        return result

    async def async_create_manual_task(
        self, raw: dict[str, Any], user_id: str, user_name: str
    ) -> dict[str, Any]:
        """Create a standalone task for the Windows client."""
        result = await self.hass.async_add_executor_job(
            self.database.create_manual_task,
            raw,
            dt_util.utcnow().isoformat(),
            user_id,
            user_name,
        )
        self._fire_task_event(result["id"], "created", result["revision"])
        return result

    async def async_update_materialized_task(
        self,
        task_id: str,
        changes: dict[str, Any],
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Update one task and publish a live refresh event."""
        result = await self.hass.async_add_executor_job(
            self.database.update_materialized_task,
            task_id,
            changes,
            expected_revision,
            user_id,
            user_name,
        )
        self._fire_task_event(task_id, "updated", result["revision"])
        return result

    async def async_update_task_checklist_item(
        self,
        task_id: str,
        item_id: str,
        changes: dict[str, Any],
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Update one checklist item and publish a live refresh event."""
        result = await self.hass.async_add_executor_job(
            self.database.update_task_checklist_item,
            task_id,
            item_id,
            changes,
            expected_revision,
            user_id,
            user_name,
        )
        self._fire_task_event(task_id, "checklist_updated", result["revision"])
        return result

    @callback
    def _fire_task_event(self, task_id: str, operation: str, revision: int) -> None:
        self.hass.bus.async_fire(
            LIVE_UPDATE_EVENT,
            self._tenant_payload({
                "collection": "tasks",
                "operation": operation,
                "record_id": task_id,
                "revision": revision,
            }),
        )
        self._notify_changed()

    async def async_list_import_batches(self, limit: int, offset: int) -> dict[str, Any]:
        """Return retained invoice import history."""
        return await self.hass.async_add_executor_job(
            self.database.list_import_batches, limit, offset
        )

    async def async_get_import_batch(self, import_id: str) -> dict[str, Any]:
        """Return one invoice import with row-level history."""
        return await self.hass.async_add_executor_job(
            self.database.get_import_batch, import_id
        )

    async def async_list_accounting_batches(
        self, *, status: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        """Return grouped accounting task summaries."""
        return await self.hass.async_add_executor_job(
            partial(
                self.database.list_accounting_task_batches,
                status=status,
                limit=limit,
                offset=offset,
            )
        )

    async def async_accounting_batch_invoices(
        self, batch_id: str
    ) -> list[dict[str, Any]]:
        """Return exact invoice membership for an accounting task."""
        return await self.hass.async_add_executor_job(
            self.database.accounting_task_invoice_ids, batch_id
        )

    async def async_accounting_rules(self) -> list[dict[str, Any]]:
        """Return grouped accounting task-generation rules."""
        return await self.hass.async_add_executor_job(self.database.list_accounting_rules)

    async def async_update_accounting_rule(
        self,
        rule_id: str,
        changes: dict[str, Any],
        expected_revision: int,
        user_id: str,
        user_name: str,
    ) -> dict[str, Any]:
        """Update one task-generation rule and immediately re-evaluate invoices."""
        result = await self.hass.async_add_executor_job(
            self.database.update_accounting_rule,
            rule_id,
            changes,
            expected_revision,
            user_id,
            user_name,
        )
        await self.async_evaluate_accounting_tasks(force=True)
        return result

    async def async_upsert_topic(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Create or update a topic."""
        requested_id = str(raw.get("id", ""))
        existing_id = requested_id if requested_id in self.data["topics"] else None
        topic = validate_topic(raw, existing_id)
        self.data["topics"][topic["id"]] = topic
        await self._async_save()
        await self.async_materialize_workflow_tasks(force=True)
        return topic

    async def async_delete_topic(self, topic_id: str) -> None:
        """Delete an unused topic."""
        if topic_id not in self.data["topics"]:
            raise StructuralOfficeValidationError("Topic was not found")
        used_by = [
            routine["name"]
            for routine in self.data["routines"].values()
            if topic_id in routine["topic_ids"]
        ]
        if used_by:
            raise StructuralOfficeValidationError(
                f"Topic is still in use: {', '.join(used_by)}"
            )
        del self.data["topics"][topic_id]
        await self._async_save()

    async def async_upsert_routine(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Create or update a routine."""
        requested_id = str(raw.get("id", ""))
        existing_id = requested_id if requested_id in self.data["routines"] else None
        routine = validate_routine(raw, set(self.data["topics"]), existing_id)
        self.data["routines"][routine["id"]] = routine
        await self._async_save()
        await self.async_materialize_workflow_tasks(force=True)
        return routine

    async def async_delete_routine(self, routine_id: str) -> None:
        """Delete a routine and its generated state."""
        if self.data["routines"].pop(routine_id, None) is None:
            raise StructuralOfficeValidationError("Routine was not found")
        prefix = f"{routine_id}:"
        self.data["occurrences"] = {
            key: value
            for key, value in self.data["occurrences"].items()
            if not key.startswith(prefix)
        }
        self.data["notifications"] = {
            key: value
            for key, value in self.data["notifications"].items()
            if not key.startswith(prefix)
        }
        await self._async_save()

    async def async_upsert_invoice(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Create or update an accounting record."""
        requested_id = str(raw.get("id", ""))
        existing_id = requested_id if requested_id in self.data["invoices"] else None
        invoice = validate_invoice(raw, existing_id)
        self.data["invoices"][invoice["id"]] = invoice
        await self._async_save()
        await self.async_evaluate_accounting_tasks(force=True)
        return invoice_for_frontend(invoice, dt_util.now().date())

    async def async_delete_invoice(self, invoice_id: str) -> None:
        """Delete an accounting record and its notification history."""
        if self.data["invoices"].pop(invoice_id, None) is None:
            raise StructuralOfficeValidationError("Accounting record was not found")
        prefix = f"invoice:{invoice_id}:"
        self.data["notifications"] = {
            key: value
            for key, value in self.data["notifications"].items()
            if not key.startswith(prefix)
        }
        await self._async_save()

    async def async_import_invoices(self, records: list[dict[str, Any]]) -> dict[str, int]:
        """Validate and apply a confirmed Excel import."""
        if len(records) > 5000:
            raise StructuralOfficeValidationError("A maximum of 5,000 records is allowed")
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        created = 0
        updated = 0
        unchanged = 0
        for raw in records:
            requested_id = str(raw.get("id", ""))
            existing_id = requested_id if requested_id in self.data["invoices"] else None
            invoice = validate_invoice(raw, existing_id)
            if invoice["id"] in seen_ids:
                raise StructuralOfficeValidationError("Import contains duplicate IDs")
            seen_ids.add(invoice["id"])
            if existing_id:
                existing = self.data["invoices"][existing_id]
                comparable = {key: value for key, value in invoice.items() if key != "updated_at"}
                existing_comparable = {
                    key: value for key, value in existing.items() if key != "updated_at"
                }
                if comparable == existing_comparable:
                    invoice = existing
                    unchanged += 1
                else:
                    updated += 1
            else:
                created += 1
            normalized.append(invoice)
        for invoice in normalized:
            self.data["invoices"][invoice["id"]] = invoice
        await self._async_save()
        await self.async_evaluate_accounting_tasks(force=True)
        return {"created": created, "unchanged": unchanged, "updated": updated}

    async def async_import_invoice_csv(
        self,
        content: bytes,
        source_name: str,
        *,
        apply: bool,
    ) -> dict[str, Any]:
        """Preview or apply an invoice-list CSV export."""
        parsed = await self.hass.async_add_executor_job(
            partial(
                parse_invoice_list_csv,
                content,
                self.options[CONF_DEFAULT_PAYMENT_TERM_DAYS],
                use_sepa_date=self.options[CONF_SEPA_DATE_AS_DUE_DATE],
            )
        )
        existing = self.data["invoices"]
        parsed["created"] = sum(item["id"] not in existing for item in parsed["records"])
        parsed["unchanged"] = sum(
            item["id"] in existing
            and {key: value for key, value in item.items() if key != "updated_at"}
            == {
                key: value
                for key, value in existing[item["id"]].items()
                if key != "updated_at"
            }
            for item in parsed["records"]
        )
        parsed["updated"] = len(parsed["records"]) - parsed["created"] - parsed["unchanged"]
        parsed["already_imported"] = await self.hass.async_add_executor_job(
            self.database.has_import_checksum, parsed["checksum"]
        )
        fingerprints = [item["fingerprint"] for item in parsed["row_fingerprints"]]
        known_fingerprints = await self.hass.async_add_executor_job(
            self.database.known_import_row_fingerprints, fingerprints
        )
        parsed["known_rows"] = len(known_fingerprints)
        parsed["new_rows"] = len(fingerprints) - len(known_fingerprints)
        if not apply:
            return parsed
        if parsed["errors"]:
            raise StructuralOfficeValidationError("CSV import contains validation errors")
        if parsed["already_imported"]:
            return {
                "already_imported": True,
                "cancelled": parsed["cancelled"],
                "checksum": parsed["checksum"],
                "created": 0,
                "known_rows": parsed["known_rows"],
                "new_rows": 0,
                "record_count": len(parsed["records"]),
                "source_name": Path(source_name).name[:255] or "invoice-list.csv",
                "unchanged": len(parsed["records"]),
                "updated": 0,
            }

        result = await self.async_import_invoices(parsed["records"])
        imported_at = dt_util.utcnow().isoformat()
        batch = {
            "cancelled": parsed["cancelled"],
            "checksum": parsed["checksum"],
            "created": result["created"],
            "import_id": f"csv-{parsed['checksum'][:24]}",
            "imported_at": imported_at,
            "record_count": len(parsed["records"]),
            "known_rows": parsed["known_rows"],
            "new_rows": parsed["new_rows"],
            "source_name": Path(source_name).name[:255] or "invoice-list.csv",
            "unchanged": result["unchanged"],
            "updated": result["updated"],
        }
        await self.hass.async_add_executor_job(
            self.database.add_import_batch, batch, content, parsed["row_fingerprints"]
        )
        await self._async_refresh_database_stats()
        return batch

    async def async_materialize_workflow_tasks(self, *, force: bool = False) -> dict[str, int]:
        """Persist concrete routine tasks for history and future Windows clients."""
        today = dt_util.now().date()
        if not force and self._last_materialized_date == today:
            return {"created": 0, "updated": 0}
        occurrences = self.build_occurrences(
            today - timedelta(days=365), today + timedelta(days=730)
        )
        result = await self.hass.async_add_executor_job(
            self.database.materialize_task_occurrences,
            occurrences,
            dt_util.utcnow().isoformat(),
        )
        self._last_materialized_date = today
        await self._async_refresh_database_stats()
        return result

    async def async_evaluate_accounting_tasks(
        self, now: datetime | None = None, *, force: bool = False
    ) -> dict[str, Any]:
        """Create or refresh grouped tasks for due, unpaid invoices."""
        local_now = dt_util.as_local(now or dt_util.utcnow())
        result = await self.hass.async_add_executor_job(
            self.database.evaluate_accounting_tasks,
            local_now.date().isoformat(),
            dt_util.utcnow().isoformat(),
            local_now.strftime("%H:%M"),
            force,
            self.options[CONF_PAYMENT_REMINDER_ESTIMATED_MINUTES],
        )
        if (
            result["created"]
            or result["updated"]
            or result["completed"]
            or result["confirmation_required"]
        ):
            self.hass.bus.async_fire(
                LIVE_UPDATE_EVENT,
                self._tenant_payload({
                    "operation": "accounting_tasks_refreshed",
                    "summary": result,
                }),
            )
            self._notify_changed()
        if result["notifiable_created"] and self.options[CONF_NOTIFY_TARGETS]:
            await self.hass.services.async_call(
                "notify",
                "send_message",
                {
                    "title": "StructuralOffice: Accounting tasks",
                    "message": (
                        f"{result['notifiable_created']} grouped task(s) were created for unpaid "
                        "invoices. Review them in the StructuralOffice Windows client."
                    ),
                    "data": {
                        "tag": f"structuraloffice-accounting-{result['evaluation_date']}"
                    },
                },
                target={"entity_id": self.options[CONF_NOTIFY_TARGETS]},
                blocking=True,
            )
        await self._async_refresh_database_stats()
        return result

    async def async_create_backup(self) -> dict[str, Any]:
        """Create a consistent database backup."""
        result = await self.hass.async_add_executor_job(self.database.create_backup)
        await self._async_refresh_database_stats()
        self._notify_changed()
        return result

    async def async_restore_backup(self, filename: str) -> None:
        """Restore a managed backup and reload all in-memory records."""
        await self.hass.async_add_executor_job(self.database.restore_backup, filename)
        await self.hass.async_add_executor_job(self.database.initialize)
        self.data = await self.hass.async_add_executor_job(
            self.database.load, set(self.data)
        )
        await self._async_refresh_database_stats()
        self._notify_changed()

    async def async_delete_backup(self, filename: str) -> None:
        """Delete a managed backup."""
        await self.hass.async_add_executor_job(self.database.delete_backup, filename)
        await self._async_refresh_database_stats()
        self._notify_changed()

    def system_data(self) -> dict[str, Any]:
        """Return the deliberately small Home Assistant administration view."""
        return {
            "backups": list(self.backups),
            "database": dict(self.database_stats),
            "storage_scope": "home_assistant_user",
            "version": INTEGRATION_VERSION,
        }

    async def async_set_occurrence_status(self, item_id: str, status: str) -> dict[str, Any]:
        """Set the status of one generated topic occurrence."""
        if status not in VALID_STATUSES:
            raise StructuralOfficeValidationError("Invalid status")
        if not self._occurrence_exists(item_id):
            raise StructuralOfficeValidationError("Task was not found")
        state = {
            "status": status,
            "updated_at": dt_util.utcnow().isoformat(),
        }
        self.data["occurrences"][item_id] = state
        await self._async_save()
        return state

    def _occurrence_exists(self, item_id: str) -> bool:
        try:
            routine_id, topic_id, raw_date = item_id.split(":", 2)
            due = date.fromisoformat(raw_date)
        except (ValueError, TypeError):
            return False
        routine = self.data["routines"].get(routine_id)
        if routine is None or (
            topic_id != "direct" and topic_id not in routine["topic_ids"]
        ):
            return False
        return due in set(iter_due_dates(routine, due, due))

    def build_occurrences(self, start: date, end: date) -> list[dict[str, Any]]:
        """Build topic occurrences in a date range."""
        topics = self.data["topics"]
        result: list[dict[str, Any]] = []
        for routine in self.data["routines"].values():
            if not routine["enabled"]:
                continue
            for due in iter_due_dates(routine, start, end):
                topic_ids = routine["topic_ids"] or ["direct"]
                for topic_id in topic_ids:
                    if topic_id == "direct":
                        topic = {
                            "name": routine["name"],
                            "description": routine["description"],
                            "category": "Routine",
                            "checklist": [],
                            "steps": [],
                            "priority": routine.get("priority", "normal"),
                            "estimated_minutes": routine.get("estimated_minutes", 10),
                        }
                    else:
                        topic = topics.get(topic_id)
                        if topic is None:
                            continue
                    item_id = occurrence_id(routine["id"], topic_id, due)
                    stored = self.data["occurrences"].get(item_id, {})
                    result.append(
                        {
                            "id": item_id,
                            "routine_id": routine["id"],
                            "routine_name": routine["name"],
                            "topic_id": None if topic_id == "direct" else topic_id,
                            "topic_name": topic["name"],
                            "description": topic["description"],
                            "category": topic["category"],
                            "checklist": topic["checklist"],
                            "steps": topic.get("steps", []),
                            "priority": topic.get("priority", "normal"),
                            "estimated_minutes": int(
                                topic.get(
                                    "estimated_minutes",
                                    routine.get("estimated_minutes", 10),
                                )
                            ),
                            "due_date": due.isoformat(),
                            "due_time": routine["due_time"],
                            "status": stored.get("status", STATUS_OPEN),
                        }
                    )
        return sorted(
            result,
            key=lambda item: (
                item["due_date"],
                item["due_time"],
                item["topic_name"],
            ),
        )

    def frontend_data(self) -> dict[str, Any]:
        """Return data needed by the panel."""
        today = dt_util.now().date()
        occurrences = self.build_occurrences(
            today - timedelta(days=365), today + timedelta(days=90)
        )
        open_items = [item for item in occurrences if item["status"] == STATUS_OPEN]
        past_occurrences = [
            item for item in occurrences if item["due_date"] <= today.isoformat()
        ]
        return {
            "topics": sorted(
                self.data["topics"].values(),
                key=lambda item: item["name"].casefold(),
            ),
            "routines": sorted(
                self.data["routines"].values(),
                key=lambda item: item["name"].casefold(),
            ),
            "occurrences": occurrences,
            "summary": {
                "open": len(open_items),
                "today": sum(item["due_date"] == today.isoformat() for item in open_items),
                "overdue": sum(item["due_date"] < today.isoformat() for item in open_items),
                "upcoming": sum(item["due_date"] > today.isoformat() for item in open_items),
            },
            "options": self.options,
            "today": today.isoformat(),
            "invoices": [
                invoice_for_frontend(invoice, today)
                for invoice in sorted(
                    self.data["invoices"].values(),
                    key=lambda item: (item["due_date"], item["invoice_number"]),
                )
            ],
            "accounting_summary": accounting_summary(list(self.data["invoices"].values()), today),
            "analytics": {
                "accounting": build_accounting_analytics(
                    list(self.data["invoices"].values()), today
                ),
                "workflow": build_workflow_analytics(past_occurrences),
            },
        }

    async def async_process_reminders(self, now: datetime | None = None) -> None:
        """Send due, not-yet-sent reminders."""
        now = dt_util.as_local(now or dt_util.utcnow())
        targets = self.options[CONF_NOTIFY_TARGETS]
        if not targets:
            return
        catch_up = timedelta(hours=self.options[CONF_CATCH_UP_HOURS])
        changed = False
        for routine in self.data["routines"].values():
            if not routine["enabled"]:
                continue
            offsets = routine["reminder_offsets"]
            if not offsets:
                continue
            routine_now = now.astimezone(ZoneInfo(routine.get("timezone", "Europe/Berlin")))
            start = routine_now.date() - timedelta(days=max(offsets) + 1)
            end = routine_now.date() - timedelta(days=min(offsets) - 1)
            due_clock = time.fromisoformat(routine["due_time"])
            for due in iter_due_dates(routine, start, end):
                for topic_id in routine["topic_ids"] or ["direct"]:
                    item_id = occurrence_id(routine["id"], topic_id, due)
                    item_status = (
                        self.data["occurrences"].get(item_id, {}).get("status", STATUS_OPEN)
                    )
                    if item_status != STATUS_OPEN:
                        continue
                    topic = (
                        {"name": routine["name"]}
                        if topic_id == "direct"
                        else self.data["topics"].get(topic_id)
                    )
                    if topic is None:
                        continue
                    eligible: list[tuple[int, datetime, str]] = []
                    for offset in offsets:
                        remind_date = due + timedelta(days=offset)
                        remind_at = datetime.combine(
                            remind_date,
                            due_clock,
                            tzinfo=ZoneInfo(routine.get("timezone", "Europe/Berlin")),
                        )
                        notification_id = f"{item_id}:{offset}"
                        delivered = notification_id in self.data["notifications"] or await (
                            self.hass.async_add_executor_job(
                                self.database.reminder_was_delivered, notification_id
                            )
                        )
                        if delivered:
                            continue
                        age = routine_now - remind_at
                        policy = routine.get("catch_up_policy", "configured_window")
                        allowed_age = (
                            SCHEDULER_INTERVAL
                            if policy == "skip_missed" or catch_up <= timedelta(0)
                            else catch_up
                        )
                        if remind_at <= routine_now and age <= allowed_age:
                            eligible.append((offset, remind_at, notification_id))
                    if routine.get("catch_up_policy") == "latest_only" and eligible:
                        eligible = [max(eligible, key=lambda item: item[1])]
                    for offset, remind_at, notification_id in eligible:
                        await self._async_send_notification(
                            topic["name"], routine["name"], due, item_id
                        )
                        sent_at = now.isoformat()
                        await self.hass.async_add_executor_job(
                            self.database.record_reminder_delivery,
                            notification_id,
                            item_id,
                            routine["id"],
                            offset,
                            remind_at.isoformat(),
                            sent_at,
                        )
                        self.data["notifications"][notification_id] = sent_at
                        changed = True
        if changed:
            await self._async_save()

    async def _async_send_notification(
        self, topic_name: str, routine_name: str, due: date, item_id: str
    ) -> None:
        """Send a Home Assistant notification."""
        await self.hass.services.async_call(
            "notify",
            "send_message",
            {
                "title": f"StructuralOffice: {topic_name}",
                "message": f"{routine_name} is due on {due.strftime('%Y-%m-%d')}.",
                "data": {
                    "url": f"/{DOMAIN}",
                    "clickAction": f"/{DOMAIN}",
                    "tag": f"structuraloffice-{item_id}",
                },
            },
            target={"entity_id": self.options[CONF_NOTIFY_TARGETS]},
            blocking=True,
        )

    async def async_send_test_notification(self) -> None:
        """Send a test notification to the configured targets."""
        if not self.options[CONF_NOTIFY_TARGETS]:
            raise StructuralOfficeValidationError(
                "Configure a notification device first"
            )
        await self.hass.services.async_call(
            "notify",
            "send_message",
            {
                "title": "StructuralOffice",
                "message": "Push notifications are configured successfully.",
            },
            target={"entity_id": self.options[CONF_NOTIFY_TARGETS]},
            blocking=True,
        )
