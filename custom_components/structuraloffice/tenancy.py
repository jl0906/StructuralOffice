"""Per-user StructuralOffice database lifecycle and access roles."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    BACKUP_DIRECTORY,
    DATABASE_DIRECTORY,
    DATABASE_FILENAME,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .manager import StructuralOfficeManager
from .models import StructuralOfficeValidationError
from .tenant_storage import migrate_legacy_database, tenant_database_paths

_LOGGER = logging.getLogger(__name__)

TENANCY_STORAGE_VERSION = 1


class StructuralOfficeTenantRegistry:
    """Resolve authenticated users to isolated StructuralOffice managers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.store = Store[dict[str, Any]](
            hass,
            TENANCY_STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.tenancy.{entry.entry_id}",
        )
        self.owner_user_id: str | None = None
        self.roles: dict[str, str] = {}
        self._managers: dict[str, StructuralOfficeManager] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._legacy_json_available = False
        self._metadata_initialized = False

    async def async_initialize(self) -> None:
        """Load role metadata and initialize the owner's tenant."""
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self._metadata_initialized = stored.get("initialized") is True
            owner = stored.get("owner_user_id")
            if isinstance(owner, str) and owner:
                self.owner_user_id = owner
            roles = stored.get("roles")
            if isinstance(roles, dict):
                self.roles = {
                    str(user_id): role
                    for user_id, role in roles.items()
                    if role in {"viewer", "editor"}
                }

        users = [
            user
            for user in await self.hass.auth.async_get_users()
            if not getattr(user, "system_generated", False)
        ]
        known_ids = {user.id for user in users}
        if self.owner_user_id not in known_ids:
            owner = next((user for user in users if getattr(user, "is_owner", False)), None)
            owner = owner or next((user for user in users if user.is_admin), None)
            owner = owner or (users[0] if users else None)
            if owner is None:
                raise StructuralOfficeValidationError(
                    "StructuralOffice requires a Home Assistant user"
                )
            self.owner_user_id = owner.id

        root = Path(self.hass.config.path(DATABASE_DIRECTORY))
        owner_database, owner_backups = tenant_database_paths(root, self.owner_user_id)
        migrated = False
        if not self._metadata_initialized:
            migrated = await self.hass.async_add_executor_job(
                migrate_legacy_database,
                root / DATABASE_FILENAME,
                root / BACKUP_DIRECTORY,
                owner_database,
                owner_backups,
            )
        legacy_store = Store[dict[str, Any]](
            self.hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{self.entry.entry_id}",
        )
        self._legacy_json_available = isinstance(await legacy_store.async_load(), dict)

        owner_manager = await self.async_manager_for(self.owner_user_id)
        if (
            not self._metadata_initialized
            and isinstance(owner_manager.data.get("user_roles"), dict)
        ):
            self.roles = {
                str(user_id): role
                for user_id, role in owner_manager.data["user_roles"].items()
                if role in {"viewer", "editor"}
            }
        await self._async_save_metadata()
        if migrated:
            _LOGGER.info(
                "Migrated the shared StructuralOffice database to the owner's private tenant"
            )

        provisioned_ids = set(self.roles) & known_ids
        provisioned_ids.update(user.id for user in users if user.is_admin)
        for user_id in provisioned_ids:
            await self.async_manager_for(user_id)

    async def async_manager_for(self, user_id: str) -> StructuralOfficeManager:
        """Return an initialized manager backed by this user's database only."""
        if user_id in self._managers:
            return self._managers[user_id]
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if user_id in self._managers:
                return self._managers[user_id]
            root = Path(self.hass.config.path(DATABASE_DIRECTORY))
            database_path, backup_directory = tenant_database_paths(root, user_id)
            manager = StructuralOfficeManager(
                self.hass,
                self.entry,
                database_path=database_path,
                backup_directory=backup_directory,
                legacy_storage=(
                    user_id == self.owner_user_id
                    and not self._metadata_initialized
                    and not database_path.exists()
                    and self._legacy_json_available
                ),
                tenant_user_id=user_id,
                shared_notifications=user_id == self.owner_user_id,
            )
            await manager.async_initialize()
            self._managers[user_id] = manager
            return manager

    def manager_for(self, user_id: str) -> StructuralOfficeManager:
        """Return an already provisioned tenant for synchronous request routing."""
        manager = self._managers.get(user_id)
        if manager is None:
            raise StructuralOfficeValidationError(
                "No private database is provisioned for this Home Assistant user"
            )
        return manager

    @property
    def primary_manager(self) -> StructuralOfficeManager:
        """Return the owner's initialized manager for Home Assistant sensors."""
        if self.owner_user_id is None or self.owner_user_id not in self._managers:
            raise StructuralOfficeValidationError("StructuralOffice is not initialized")
        return self._managers[self.owner_user_id]

    def user_role(self, user_id: str, is_admin: bool = False) -> str | None:
        """Return the global access role without exposing another user's database."""
        if is_admin:
            return "admin"
        role = self.roles.get(user_id)
        return role if role in {"viewer", "editor"} else None

    async def async_set_user_role(self, user_id: str, role: str | None) -> None:
        """Assign access and provision a private database for the user."""
        users = await self.hass.auth.async_get_users()
        if user_id not in {user.id for user in users}:
            raise StructuralOfficeValidationError("Home Assistant user was not found")
        if role is not None and role not in {"viewer", "editor"}:
            raise StructuralOfficeValidationError("Invalid role")
        if role is None:
            self.roles.pop(user_id, None)
        else:
            self.roles[user_id] = role
            await self.async_manager_for(user_id)
        await self._async_save_metadata()

    async def _async_save_metadata(self) -> None:
        await self.store.async_save(
            {
                "owner_user_id": self.owner_user_id,
                "roles": dict(self.roles),
                "initialized": True,
            }
        )
        self._metadata_initialized = True

    async def async_shutdown(self) -> None:
        """Stop all tenant schedulers and persist their databases."""
        for manager in list(self._managers.values()):
            await manager.async_shutdown()
        self._managers.clear()

    def notify_options_changed(self) -> None:
        """Refresh listeners for every initialized tenant."""
        for manager in self._managers.values():
            manager._notify_changed()
