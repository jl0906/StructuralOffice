"""StructuralOffice backend health sensors."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import StructuralOfficeConfigEntry
from .manager import StructuralOfficeManager

SENSORS = (
    ("database_size", "Database Size", "mdi:database", "database_bytes", "B"),
    ("database_records", "Database Records", "mdi:table-row", "record_count", "records"),
    ("database_backups", "Database Backups", "mdi:database-check", "backup_count", "backups"),
    ("database_schema", "Database Schema", "mdi:database-cog", "schema_version", "version"),
)


async def async_setup_entry(
    _hass,
    entry: StructuralOfficeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up StructuralOffice backend sensors."""
    manager = entry.runtime_data.tenants.primary_manager
    async_add_entities(
        StructuralOfficeDatabaseSensor(manager, key, name, icon, statistic, unit)
        for key, name, icon, statistic, unit in SENSORS
    )


class StructuralOfficeDatabaseSensor(SensorEntity):
    """Represent one cached database statistic."""

    _attr_has_entity_name = True

    def __init__(
        self,
        manager: StructuralOfficeManager,
        key: str,
        name: str,
        icon: str,
        statistic: str,
        unit: str,
    ) -> None:
        self.manager = manager
        self._statistic = statistic
        self._attr_unique_id = f"structuraloffice_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._remove_listener: Callable[[], None] | None = None

    @property
    def native_value(self) -> int:
        """Return the current cached database statistic."""
        return int(self.manager.database_stats.get(self._statistic, 0))

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
