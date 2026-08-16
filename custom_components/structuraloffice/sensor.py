"""StructuralOffice summary sensors."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StructuralOfficeConfigEntry
from .manager import StructuralOfficeManager

SENSORS = (
    ("open_tasks", "Offene Aufgaben", "mdi:clipboard-text-clock", "summary", "open"),
    (
        "tasks_due_today",
        "Heute fällige Aufgaben",
        "mdi:calendar-today",
        "summary",
        "today",
    ),
    (
        "overdue_tasks",
        "Überfällige Aufgaben",
        "mdi:calendar-alert",
        "summary",
        "overdue",
    ),
    (
        "open_payables",
        "Offene Eingangsrechnungen",
        "mdi:invoice-arrow-left-outline",
        "accounting_summary",
        "open_payables",
    ),
    (
        "due_payments",
        "Fällige Zahlungen",
        "mdi:cash-clock",
        "accounting_summary",
        "due_payments",
    ),
    (
        "open_receivables",
        "Offene Forderungen",
        "mdi:invoice-arrow-right-outline",
        "accounting_summary",
        "open_receivables",
    ),
    (
        "overdue_receivables",
        "Überfällige Forderungen",
        "mdi:cash-remove",
        "accounting_summary",
        "overdue_receivables",
    ),
)


async def async_setup_entry(
    _hass,
    entry: StructuralOfficeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up StructuralOffice sensors."""
    manager = entry.runtime_data.manager
    async_add_entities(
        StructuralOfficeSensor(manager, key, name, icon, section, summary_key)
        for key, name, icon, section, summary_key in SENSORS
    )


class StructuralOfficeSensor(SensorEntity):
    """A calculated StructuralOffice task count."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "Aufgaben"

    def __init__(
        self,
        manager: StructuralOfficeManager,
        key: str,
        name: str,
        icon: str,
        section: str,
        summary_key: str,
    ) -> None:
        self.manager = manager
        self._section = section
        self._summary_key = summary_key
        self._attr_unique_id = f"structuraloffice_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._remove_listener: Callable[[], None] | None = None

    @property
    def native_value(self) -> int:
        """Return the current task count."""
        return int(self.manager.frontend_data()[self._section][self._summary_key])

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager changes."""
        self._remove_listener = self.manager.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from manager changes."""
        if self._remove_listener:
            self._remove_listener()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
