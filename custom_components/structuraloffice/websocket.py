"""WebSocket API for the StructuralOffice panel."""

from __future__ import annotations

import base64
import binascii

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    CONF_COMPANY_ADDRESS,
    CONF_COMPANY_EMAIL,
    CONF_COMPANY_NAME,
    DOMAIN,
    LIVE_UPDATE_EVENT,
)
from .csv_export import csv_filename, export_invoices_csv
from .excel import export_invoices_xlsx, load_template_xlsx, parse_invoices_xlsx
from .manager import StructuralOfficeManager
from .models import StructuralOfficeValidationError
from .pdf_document import build_invoice_pdf
from .tenancy import StructuralOfficeTenantRegistry

READ_ROLES = {"admin", "editor", "viewer"}
WRITE_ROLES = {"admin", "editor"}


def _tenants(hass: HomeAssistant) -> StructuralOfficeTenantRegistry:
    tenants = hass.data.get(DOMAIN, {}).get("tenants")
    if tenants is None:
        raise StructuralOfficeValidationError("StructuralOffice is not configured")
    return tenants


def _manager(hass: HomeAssistant, user_id: str) -> StructuralOfficeManager:
    return _tenants(hass).manager_for(user_id)


def _require_role(hass: HomeAssistant, connection, roles: set[str]) -> str:
    role = _tenants(hass).user_role(connection.user.id, connection.user.is_admin)
    if role not in roles:
        raise StructuralOfficeValidationError(
            "Access to StructuralOffice is denied. Contact an administrator."
        )
    return role


def _error(connection: websocket_api.ActiveConnection, msg: dict, err: Exception) -> None:
    connection.send_error(msg["id"], "invalid_request", str(err))


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_data"})
async def ws_get_data(hass, connection, msg) -> None:
    """Return the reduced Home Assistant administration dashboard."""
    try:
        manager = _manager(hass, connection.user.id)
        role = _require_role(hass, connection, {"admin"})
        result = manager.system_data()
        result["access"] = role
        connection.send_result(msg["id"], result)
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe_live"})
async def ws_subscribe_live(hass, connection, msg) -> None:
    """Subscribe an authorized Windows client to live record events."""
    try:
        _manager(hass, connection.user.id)
        _require_role(hass, connection, READ_ROLES)

        @callback
        def forward_event(event: Event) -> None:
            if event.data.get("tenant_user_id") == connection.user.id:
                connection.send_event(msg["id"], event.data)

        connection.subscriptions[msg["id"]] = hass.bus.async_listen(
            LIVE_UPDATE_EVENT, forward_event
        )
        connection.send_result(msg["id"])
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/set_user_role", vol.Required("user_id"): str,
     vol.Required("role"): vol.In(["none", "viewer", "editor"])}
)
async def ws_set_user_role(hass, connection, msg) -> None:
    """Assign a StructuralOffice role. Home Assistant admins remain admins."""
    try:
        _require_role(hass, connection, {"admin"})
        await _tenants(hass).async_set_user_role(
            msg["user_id"], None if msg["role"] == "none" else msg["role"]
        )
        connection.send_result(msg["id"])
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


def _write_command(command_type: str, key: str, handler):
    """Build a role-protected mutation command."""
    value_type = dict if key in {"topic", "routine", "invoice"} else str
    schema = {
        vol.Required("type"): f"{DOMAIN}/{command_type}",
        vol.Required(key): value_type,
    }

    @websocket_api.async_response
    @websocket_api.websocket_command(schema)
    async def command(hass, connection, msg) -> None:
        try:
            manager = _manager(hass, connection.user.id)
            _require_role(hass, connection, WRITE_ROLES)
            result = await handler(manager, msg[key])
            connection.send_result(msg["id"], result)
        except (StructuralOfficeValidationError, TypeError, ValueError) as err:
            _error(connection, msg, err)

    command.__name__ = f"ws_{command_type}"
    return command


ws_upsert_topic = _write_command(
    "upsert_topic", "topic", lambda manager, value: manager.async_upsert_topic(value)
)
ws_delete_topic = _write_command(
    "delete_topic", "topic_id", lambda manager, value: manager.async_delete_topic(value)
)
ws_upsert_routine = _write_command(
    "upsert_routine", "routine", lambda manager, value: manager.async_upsert_routine(value)
)
ws_delete_routine = _write_command(
    "delete_routine", "routine_id", lambda manager, value: manager.async_delete_routine(value)
)
ws_upsert_invoice = _write_command(
    "upsert_invoice", "invoice", lambda manager, value: manager.async_upsert_invoice(value)
)
ws_delete_invoice = _write_command(
    "delete_invoice", "invoice_id", lambda manager, value: manager.async_delete_invoice(value)
)


@websocket_api.async_response
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/set_occurrence_status",
     vol.Required("occurrence_id"): str,
     vol.Required("status"): vol.In(
         ["open", "in_progress", "completed", "skipped", "cancelled", "auto_completed"]
     )}
)
async def ws_set_occurrence_status(hass, connection, msg) -> None:
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, WRITE_ROLES)
        result = await manager.async_set_occurrence_status(msg["occurrence_id"], msg["status"])
        connection.send_result(msg["id"], result)
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/test_notification"})
async def ws_test_notification(hass, connection, msg) -> None:
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, {"admin"})
        await manager.async_send_test_notification()
        connection.send_result(msg["id"])
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/create_backup"})
async def ws_create_backup(hass, connection, msg) -> None:
    """Create a managed SQLite backup from the Home Assistant panel."""
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, {"admin"})
        connection.send_result(msg["id"], await manager.async_create_backup())
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/restore_backup",
        vol.Required("filename"): str,
    }
)
async def ws_restore_backup(hass, connection, msg) -> None:
    """Restore a managed SQLite backup from the Home Assistant panel."""
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, {"admin"})
        await manager.async_restore_backup(msg["filename"])
        connection.send_result(msg["id"])
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_backup",
        vol.Required("filename"): str,
    }
)
async def ws_delete_backup(hass, connection, msg) -> None:
    """Delete a managed SQLite backup from the Home Assistant panel."""
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, {"admin"})
        await manager.async_delete_backup(msg["filename"])
        connection.send_result(msg["id"])
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/preview_invoice_import", vol.Required("content"): str}
)
async def ws_preview_invoice_import(hass, connection, msg) -> None:
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, WRITE_ROLES)
        if len(msg["content"]) > 7_500_000:
            raise StructuralOfficeValidationError("Excel file is larger than 5 MB")
        content = base64.b64decode(msg["content"], validate=True)
        result = await hass.async_add_executor_job(parse_invoices_xlsx, content)
        existing = manager.data["invoices"]
        result["created"] = sum(item["id"] not in existing for item in result["records"])
        result["updated"] = sum(item["id"] in existing for item in result["records"])
        connection.send_result(msg["id"], result)
    except (StructuralOfficeValidationError, binascii.Error) as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/apply_invoice_import", vol.Required("records"): [dict]}
)
async def ws_apply_invoice_import(hass, connection, msg) -> None:
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, WRITE_ROLES)
        result = await manager.async_import_invoices(msg["records"])
        connection.send_result(msg["id"], result)
    except (StructuralOfficeValidationError, TypeError, ValueError) as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/export_invoices", vol.Optional("empty", default=False): bool}
)
async def ws_export_invoices(hass, connection, msg) -> None:
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, READ_ROLES)
        invoices = [] if msg["empty"] else list(manager.data["invoices"].values())
        content = await hass.async_add_executor_job(
            load_template_xlsx if msg["empty"] else export_invoices_xlsx,
            *([] if msg["empty"] else [invoices]),
        )
        filename = (
            "StructuralOffice-Accounting-Template.xlsx"
            if msg["empty"]
            else "StructuralOffice-Accounting.xlsx"
        )
        connection.send_result(msg["id"], _download(content, filename))
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/export_invoices_csv"})
async def ws_export_invoices_csv(hass, connection, msg) -> None:
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, READ_ROLES)
        content = export_invoices_csv(list(manager.data["invoices"].values()))
        connection.send_result(msg["id"], _download(content, csv_filename()))
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/generate_invoice_pdf",
     vol.Required("invoice_id"): str,
     vol.Required("document_type"): vol.In(
         ["payment_reminder", "dunning_1", "dunning_2", "dunning_3"]
     )}
)
async def ws_generate_invoice_pdf(hass, connection, msg) -> None:
    try:
        manager = _manager(hass, connection.user.id)
        _require_role(hass, connection, WRITE_ROLES)
        invoice = manager.data["invoices"].get(msg["invoice_id"])
        if invoice is None:
            raise StructuralOfficeValidationError("Accounting record was not found")
        options = manager.options
        company = {
            "name": options[CONF_COMPANY_NAME],
            "address": options[CONF_COMPANY_ADDRESS],
            "email": options[CONF_COMPANY_EMAIL],
        }
        content = await hass.async_add_executor_job(
            build_invoice_pdf, invoice, company, msg["document_type"]
        )
        safe_number = "".join(
            char if char.isalnum() or char in "-_" else "-"
            for char in invoice["invoice_number"]
        )
        connection.send_result(msg["id"], _download(content, f"Dunning-{safe_number}.pdf"))
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


def _download(content: bytes, filename: str) -> dict[str, str]:
    return {"content": base64.b64encode(content).decode("ascii"), "filename": filename}


def async_register(hass: HomeAssistant) -> None:
    """Register StructuralOffice WebSocket commands."""
    for command in (
        ws_get_data, ws_subscribe_live, ws_set_user_role, ws_upsert_topic, ws_delete_topic,
        ws_upsert_routine, ws_delete_routine, ws_set_occurrence_status,
        ws_test_notification, ws_upsert_invoice, ws_delete_invoice,
        ws_preview_invoice_import, ws_apply_invoice_import, ws_export_invoices,
        ws_export_invoices_csv, ws_generate_invoice_pdf,
        ws_create_backup, ws_restore_backup, ws_delete_backup,
    ):
        websocket_api.async_register_command(hass, command)
