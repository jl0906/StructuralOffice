"""WebSocket API for the StructuralOffice panel."""

from __future__ import annotations

import base64
import binascii

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import (
    CONF_COMPANY_ADDRESS,
    CONF_COMPANY_EMAIL,
    CONF_COMPANY_NAME,
    DOMAIN,
)
from .csv_export import csv_filename, export_invoices_csv
from .excel import export_invoices_xlsx, load_template_xlsx, parse_invoices_xlsx
from .manager import StructuralOfficeManager
from .models import StructuralOfficeValidationError
from .pdf_document import build_invoice_pdf

READ_ROLES = {"admin", "editor", "viewer"}
WRITE_ROLES = {"admin", "editor"}


def _manager(hass: HomeAssistant) -> StructuralOfficeManager:
    manager = hass.data.get(DOMAIN, {}).get("manager")
    if manager is None:
        raise StructuralOfficeValidationError("StructuralOffice ist nicht eingerichtet")
    return manager


def _role(manager: StructuralOfficeManager, connection) -> str | None:
    return manager.user_role(connection.user.id, connection.user.is_admin)


def _require_role(manager: StructuralOfficeManager, connection, roles: set[str]) -> str:
    role = _role(manager, connection)
    if role not in roles:
        raise StructuralOfficeValidationError(
            "Keine Berechtigung für StructuralOffice. Bitte einen Administrator kontaktieren."
        )
    return role


def _error(connection: websocket_api.ActiveConnection, msg: dict, err: Exception) -> None:
    connection.send_error(msg["id"], "invalid_request", str(err))


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_data"})
async def ws_get_data(hass, connection, msg) -> None:
    """Return panel data and effective access information."""
    try:
        manager = _manager(hass)
        role = _require_role(manager, connection, READ_ROLES)
        result = manager.frontend_data()
        result["access"] = role
        result["users"] = []
        if role == "admin":
            users = await hass.auth.async_get_users()
            result["users"] = [
                {
                    "id": user.id,
                    "name": user.name or user.id,
                    "is_admin": user.is_admin,
                    "is_active": user.is_active,
                    "role": manager.user_role(user.id, user.is_admin),
                }
                for user in users
                if not user.system_generated
            ]
        connection.send_result(msg["id"], result)
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
        manager = _manager(hass)
        _require_role(manager, connection, {"admin"})
        await manager.async_set_user_role(
            msg["user_id"], None if msg["role"] == "none" else msg["role"]
        )
        connection.send_result(msg["id"])
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


def _write_command(command_type: str, key: str, handler):
    """Build a role-protected mutation command."""
    schema = {vol.Required("type"): f"{DOMAIN}/{command_type}", vol.Required(key): dict if key in {"topic", "routine", "invoice"} else str}

    @websocket_api.async_response
    @websocket_api.websocket_command(schema)
    async def command(hass, connection, msg) -> None:
        try:
            manager = _manager(hass)
            _require_role(manager, connection, WRITE_ROLES)
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
     vol.Required("status"): vol.In(["open", "completed", "skipped"])}
)
async def ws_set_occurrence_status(hass, connection, msg) -> None:
    try:
        manager = _manager(hass)
        _require_role(manager, connection, WRITE_ROLES)
        result = await manager.async_set_occurrence_status(msg["occurrence_id"], msg["status"])
        connection.send_result(msg["id"], result)
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/test_notification"})
async def ws_test_notification(hass, connection, msg) -> None:
    try:
        manager = _manager(hass)
        _require_role(manager, connection, {"admin"})
        await manager.async_send_test_notification()
        connection.send_result(msg["id"])
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/preview_invoice_import", vol.Required("content"): str}
)
async def ws_preview_invoice_import(hass, connection, msg) -> None:
    try:
        manager = _manager(hass)
        _require_role(manager, connection, WRITE_ROLES)
        if len(msg["content"]) > 7_500_000:
            raise StructuralOfficeValidationError("Excel-Datei ist größer als 5 MB")
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
        manager = _manager(hass)
        _require_role(manager, connection, WRITE_ROLES)
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
        manager = _manager(hass)
        _require_role(manager, connection, READ_ROLES)
        invoices = [] if msg["empty"] else list(manager.data["invoices"].values())
        content = await hass.async_add_executor_job(
            load_template_xlsx if msg["empty"] else export_invoices_xlsx,
            *([] if msg["empty"] else [invoices]),
        )
        filename = "StructuralOffice-Buchhaltung-Vorlage.xlsx" if msg["empty"] else "StructuralOffice-Buchhaltung.xlsx"
        connection.send_result(msg["id"], _download(content, filename))
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/export_invoices_csv"})
async def ws_export_invoices_csv(hass, connection, msg) -> None:
    try:
        manager = _manager(hass)
        _require_role(manager, connection, READ_ROLES)
        content = export_invoices_csv(list(manager.data["invoices"].values()))
        connection.send_result(msg["id"], _download(content, csv_filename()))
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


@websocket_api.async_response
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/generate_invoice_pdf",
     vol.Required("invoice_id"): str,
     vol.Required("document_type"): vol.In(["payment_reminder", "dunning_1", "dunning_2", "dunning_3"])}
)
async def ws_generate_invoice_pdf(hass, connection, msg) -> None:
    try:
        manager = _manager(hass)
        _require_role(manager, connection, WRITE_ROLES)
        invoice = manager.data["invoices"].get(msg["invoice_id"])
        if invoice is None:
            raise StructuralOfficeValidationError("Buchung wurde nicht gefunden")
        options = manager.options
        company = {
            "name": options[CONF_COMPANY_NAME],
            "address": options[CONF_COMPANY_ADDRESS],
            "email": options[CONF_COMPANY_EMAIL],
        }
        content = await hass.async_add_executor_job(
            build_invoice_pdf, invoice, company, msg["document_type"]
        )
        safe_number = "".join(char if char.isalnum() or char in "-_" else "-" for char in invoice["invoice_number"])
        connection.send_result(msg["id"], _download(content, f"Mahndokument-{safe_number}.pdf"))
    except StructuralOfficeValidationError as err:
        _error(connection, msg, err)


def _download(content: bytes, filename: str) -> dict[str, str]:
    return {"content": base64.b64encode(content).decode("ascii"), "filename": filename}


def async_register(hass: HomeAssistant) -> None:
    """Register StructuralOffice WebSocket commands."""
    for command in (
        ws_get_data, ws_set_user_role, ws_upsert_topic, ws_delete_topic,
        ws_upsert_routine, ws_delete_routine, ws_set_occurrence_status,
        ws_test_notification, ws_upsert_invoice, ws_delete_invoice,
        ws_preview_invoice_import, ws_apply_invoice_import, ws_export_invoices,
        ws_export_invoices_csv, ws_generate_invoice_pdf,
    ):
        websocket_api.async_register_command(hass, command)
