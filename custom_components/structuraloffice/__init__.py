"""StructuralOffice Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .api import async_register as async_register_api
from .const import (
    DOMAIN,
    FRONTEND_URL,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL,
    PLATFORMS,
)
from .manager import StructuralOfficeManager
from .websocket import async_register as async_register_websocket


@dataclass
class StructuralOfficeRuntimeData:
    """Runtime objects for a StructuralOffice config entry."""

    manager: StructuralOfficeManager


type StructuralOfficeConfigEntry = ConfigEntry[StructuralOfficeRuntimeData]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Set up component-level StructuralOffice resources."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("websocket_registered"):
        async_register_websocket(hass)
        async_register_api(hass)
        domain_data["websocket_registered"] = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: StructuralOfficeConfigEntry) -> bool:
    """Set up StructuralOffice from a config entry."""
    manager = StructuralOfficeManager(hass, entry)
    await manager.async_initialize()
    entry.runtime_data = StructuralOfficeRuntimeData(manager)
    hass.data[DOMAIN]["manager"] = manager

    if not hass.data[DOMAIN].get("static_registered"):
        frontend_path = Path(__file__).parent / "frontend"
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/structuraloffice_static", str(frontend_path), False)]
        )
        hass.data[DOMAIN]["static_registered"] = True

    if not frontend.async_panel_exists(hass, PANEL_URL):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL,
            webcomponent_name=PANEL_COMPONENT,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=f"{FRONTEND_URL}?v=0.7.0-alpha",
            require_admin=True,
            config_panel_domain=DOMAIN,
            handle_safe_area=True,
        )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: StructuralOfficeConfigEntry) -> None:
    """Notify the runtime about updated config options."""
    if manager := hass.data.get(DOMAIN, {}).get("manager"):
        manager._notify_changed()


async def async_unload_entry(hass: HomeAssistant, entry: StructuralOfficeConfigEntry) -> bool:
    """Unload StructuralOffice."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.manager.async_shutdown()
    hass.data[DOMAIN].pop("manager", None)
    if frontend.async_panel_exists(hass, PANEL_URL):
        frontend.async_remove_panel(hass, PANEL_URL)
    return True
