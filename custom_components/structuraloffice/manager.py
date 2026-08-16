"""Data management and reminder scheduling for StructuralOffice."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Any

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
    CONF_SEPA_DATE_AS_DUE_DATE,
    DATABASE_DIRECTORY,
    DATABASE_FILENAME,
    DEFAULT_CATCH_UP_HOURS,
    DEFAULT_COMPANY_ADDRESS,
    DEFAULT_COMPANY_EMAIL,
    DEFAULT_COMPANY_NAME,
    DEFAULT_PAYMENT_TERM_DAYS,
    DEFAULT_REMINDER_TIME,
    DEFAULT_SEPA_DATE_AS_DUE_DATE,
    DOMAIN,
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
    validate_routine,
    validate_topic,
)

_LOGGER = logging.getLogger(__name__)


class StructuralOfficeManager:
    """Own StructuralOffice data and scheduled reminders."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry.entry_id}",
        )
        database_directory = Path(hass.config.path(DATABASE_DIRECTORY))
        self.database = StructuralOfficeDatabase(
            database_directory / DATABASE_FILENAME,
            database_directory / BACKUP_DIRECTORY,
        )
        self.data: dict[str, Any] = self._empty_data()
        self.database_stats: dict[str, Any] = {}
        self.backups: list[dict[str, Any]] = []
        self._listeners: set[Callable[[], None]] = set()
        self._remove_timer: Callable[[], None] | None = None

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        return {
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
            legacy = await self.store.async_load()
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
        self.hass.bus.async_fire(UPDATE_EVENT)

    async def _async_save(self) -> None:
        await self.hass.async_add_executor_job(self.database.save, self.data)
        await self._async_refresh_database_stats()
        self._notify_changed()

    async def _async_refresh_database_stats(self) -> None:
        self.database_stats = await self.hass.async_add_executor_job(
            self.database.statistics
        )
        self.backups = await self.hass.async_add_executor_job(self.database.list_backups)

    @property
    def options(self) -> dict[str, Any]:
        """Return normalized config options."""
        return {
            CONF_NOTIFY_TARGETS: list(self.entry.options.get(CONF_NOTIFY_TARGETS, [])),
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
        }

    def user_role(self, user_id: str, is_admin: bool = False) -> str | None:
        """Return an effective StructuralOffice role."""
        if is_admin:
            return "admin"
        role = self.data["user_roles"].get(user_id)
        return role if role in {"viewer", "editor"} else None

    async def async_set_user_role(self, user_id: str, role: str | None) -> None:
        """Assign or remove a StructuralOffice role."""
        if role is not None and role not in {"viewer", "editor"}:
            raise StructuralOfficeValidationError("Invalid role")
        if role is None:
            self.data["user_roles"].pop(user_id, None)
        else:
            self.data["user_roles"][user_id] = role
        await self._async_save()

    async def async_upsert_topic(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Create or update a topic."""
        requested_id = str(raw.get("id", ""))
        existing_id = requested_id if requested_id in self.data["topics"] else None
        topic = validate_topic(raw, existing_id)
        self.data["topics"][topic["id"]] = topic
        await self._async_save()
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
        for raw in records:
            requested_id = str(raw.get("id", ""))
            existing_id = requested_id if requested_id in self.data["invoices"] else None
            invoice = validate_invoice(raw, existing_id)
            if invoice["id"] in seen_ids:
                raise StructuralOfficeValidationError("Import contains duplicate IDs")
            seen_ids.add(invoice["id"])
            if existing_id:
                updated += 1
            else:
                created += 1
            normalized.append(invoice)
        for invoice in normalized:
            self.data["invoices"][invoice["id"]] = invoice
        await self._async_save()
        return {"created": created, "updated": updated}

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
        parsed["updated"] = sum(item["id"] in existing for item in parsed["records"])
        parsed["already_imported"] = await self.hass.async_add_executor_job(
            self.database.has_import_checksum, parsed["checksum"]
        )
        if not apply:
            return parsed
        if parsed["errors"]:
            raise StructuralOfficeValidationError("CSV import contains validation errors")
        if parsed["already_imported"]:
            raise StructuralOfficeValidationError("This exact CSV file was already imported")

        result = await self.async_import_invoices(parsed["records"])
        imported_at = dt_util.utcnow().isoformat()
        batch = {
            "cancelled": parsed["cancelled"],
            "checksum": parsed["checksum"],
            "created": result["created"],
            "import_id": f"csv-{parsed['checksum'][:24]}",
            "imported_at": imported_at,
            "record_count": len(parsed["records"]),
            "source_name": Path(source_name).name[:255] or "invoice-list.csv",
            "updated": result["updated"],
        }
        await self.hass.async_add_executor_job(
            self.database.add_import_batch, batch, content
        )
        await self._async_refresh_database_stats()
        return batch

    async def async_create_backup(self) -> dict[str, Any]:
        """Create a consistent database backup."""
        result = await self.hass.async_add_executor_job(self.database.create_backup)
        await self._async_refresh_database_stats()
        self._notify_changed()
        return result

    async def async_restore_backup(self, filename: str) -> None:
        """Restore a managed backup and reload all in-memory records."""
        await self.hass.async_add_executor_job(self.database.restore_backup, filename)
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
        today = dt_util.now().date()
        invoices = [invoice_for_frontend(item, today) for item in self.data["invoices"].values()]
        return {
            "backups": list(self.backups),
            "database": dict(self.database_stats),
            "invoice_status": {
                "cancelled": sum(item["status"] == "cancelled" for item in invoices),
                "due_today": sum(item["due_state"] == "due_today" for item in invoices),
                "open": sum(item["status"] == "open" for item in invoices),
                "overdue": sum(item["due_state"] == "overdue" for item in invoices),
                "paid": sum(item["status"] == "paid" for item in invoices),
            },
            "version": "0.4.0-alpha",
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
        if routine is None or topic_id not in routine["topic_ids"]:
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
                for topic_id in routine["topic_ids"]:
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
                            "topic_id": topic_id,
                            "topic_name": topic["name"],
                            "description": topic["description"],
                            "category": topic["category"],
                            "checklist": topic["checklist"],
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
            start = now.date() - timedelta(days=max(offsets) + 1)
            end = now.date() - timedelta(days=min(offsets) - 1)
            due_clock = time.fromisoformat(routine["due_time"])
            for due in iter_due_dates(routine, start, end):
                for topic_id in routine["topic_ids"]:
                    item_id = occurrence_id(routine["id"], topic_id, due)
                    item_status = (
                        self.data["occurrences"].get(item_id, {}).get("status", STATUS_OPEN)
                    )
                    if item_status != STATUS_OPEN:
                        continue
                    topic = self.data["topics"].get(topic_id)
                    if topic is None:
                        continue
                    for offset in offsets:
                        remind_date = due + timedelta(days=offset)
                        remind_at = datetime.combine(
                            remind_date,
                            due_clock,
                            tzinfo=dt_util.DEFAULT_TIME_ZONE,
                        )
                        notification_id = f"{item_id}:{offset}"
                        if notification_id in self.data["notifications"]:
                            continue
                        age = now - remind_at
                        allowed_age = catch_up if catch_up > timedelta(0) else SCHEDULER_INTERVAL
                        if remind_at <= now and age <= allowed_age:
                            await self._async_send_notification(
                                topic["name"], routine["name"], due, item_id
                            )
                            self.data["notifications"][notification_id] = now.isoformat()
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
