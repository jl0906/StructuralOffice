"""Calendar entity exposing StructuralOffice due dates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import StructuralOfficeConfigEntry
from .const import STATUS_OPEN
from .manager import StructuralOfficeManager


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: StructuralOfficeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the StructuralOffice calendar."""
    async_add_entities([StructuralOfficeCalendar(entry.runtime_data.manager)])


class StructuralOfficeCalendar(CalendarEntity):
    """Calendar containing open StructuralOffice tasks."""

    _attr_name = "StructuralOffice Due Dates"
    _attr_unique_id = "structuraloffice_deadlines"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, manager: StructuralOfficeManager) -> None:
        self.manager = manager
        self._remove_listener: Callable[[], None] | None = None

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next task."""
        now = dt_util.now()
        items = self.manager.build_occurrences(
            now.date() - timedelta(days=1), now.date() + timedelta(days=365)
        )
        for item in items:
            if item["status"] != STATUS_OPEN:
                continue
            event = self._to_event(item)
            if event.end > now:
                return event
        return None

    async def async_get_events(
        self,
        _hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return open tasks in the requested range."""
        return [
            self._to_event(item)
            for item in self.manager.build_occurrences(start_date.date(), end_date.date())
            if item["status"] == STATUS_OPEN
        ]

    @staticmethod
    def _to_event(item: dict) -> CalendarEvent:
        due = datetime.fromisoformat(f"{item['due_date']}T{item['due_time']}").replace(
            tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
        return CalendarEvent(
            start=due,
            end=due + timedelta(minutes=30),
            summary=item["topic_name"],
            description=f"Routine: {item['routine_name']}\n{item['description']}",
            uid=item["id"],
        )

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.manager.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
        self.async_update_event_listeners()
