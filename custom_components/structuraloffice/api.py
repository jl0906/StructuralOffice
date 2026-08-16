"""Authenticated REST API used by the StructuralOffice Windows client."""

from __future__ import annotations

import base64
import binascii
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized

from .accounting import DIRECTION_RECEIVABLE, INVOICE_STATUS_OPEN
from .const import CONF_COMPANY_ADDRESS, CONF_COMPANY_EMAIL, CONF_COMPANY_NAME, DOMAIN
from .manager import StructuralOfficeManager
from .models import StructuralOfficeValidationError
from .pdf_document import build_invoice_pdf

API_PREFIX = f"/api/{DOMAIN}/v1"


def _manager(request: web.Request) -> StructuralOfficeManager:
    hass: HomeAssistant = request.app["hass"]
    manager = hass.data.get(DOMAIN, {}).get("manager")
    if manager is None:
        raise web.HTTPServiceUnavailable(text="StructuralOffice is not configured")
    return manager


def _role(request: web.Request, *, write: bool = False, admin: bool = False) -> str:
    user = request["hass_user"]
    manager = _manager(request)
    role = manager.user_role(user.id, user.is_admin)
    allowed = {"admin"} if admin else {"admin", "editor"} if write else {
        "admin",
        "editor",
        "viewer",
    }
    if role not in allowed:
        raise Unauthorized()
    return role


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in value)


class StructuralOfficeStatusView(HomeAssistantView):
    """Expose non-sensitive service and database status."""

    url = f"{API_PREFIX}/status"
    name = f"api:{DOMAIN}:v1:status"

    async def get(self, request: web.Request) -> web.Response:
        _role(request)
        return self.json(_manager(request).system_data())


class StructuralOfficeInvoicesView(HomeAssistantView):
    """Return normalized invoices and derived due states."""

    url = f"{API_PREFIX}/invoices"
    name = f"api:{DOMAIN}:v1:invoices"

    async def get(self, request: web.Request) -> web.Response:
        _role(request)
        manager = _manager(request)
        invoices = manager.frontend_data()["invoices"]
        due_state = request.query.get("due_state")
        status = request.query.get("status")
        if due_state:
            invoices = [item for item in invoices if item["due_state"] == due_state]
        if status:
            invoices = [item for item in invoices if item["status"] == status]
        return self.json({"invoices": invoices})


class StructuralOfficeInvoiceImportView(HomeAssistantView):
    """Preview or apply an invoice-list CSV upload."""

    url = f"{API_PREFIX}/imports/invoice-list"
    name = f"api:{DOMAIN}:v1:imports:invoice-list"

    async def post(self, request: web.Request) -> web.Response:
        _role(request, write=True)
        try:
            payload = await request.json()
            content = base64.b64decode(payload.get("content", ""), validate=True)
            result = await _manager(request).async_import_invoice_csv(
                content,
                str(payload.get("filename") or "invoice-list.csv"),
                apply=bool(payload.get("apply", False)),
            )
            return self.json(result)
        except (binascii.Error, StructuralOfficeValidationError, ValueError) as err:
            return self.json({"error": str(err)}, status_code=400)


class StructuralOfficeDocumentsView(HomeAssistantView):
    """Generate requested reminder documents without automatic escalation."""

    url = f"{API_PREFIX}/documents"
    name = f"api:{DOMAIN}:v1:documents"

    async def post(self, request: web.Request) -> web.Response:
        _role(request, write=True)
        try:
            payload = await request.json()
            document_type = str(payload.get("document_type", ""))
            invoices = self._select(_manager(request), payload)
            if not invoices:
                raise StructuralOfficeValidationError("No matching invoices were found")
            invalid = [
                item["invoice_number"]
                for item in invoices
                if item["direction"] != DIRECTION_RECEIVABLE
                or item["status"] != INVOICE_STATUS_OPEN
            ]
            if invalid:
                raise StructuralOfficeValidationError(
                    "Documents require open receivables: " + ", ".join(invalid[:10])
                )
            manager = _manager(request)
            company = {
                "address": manager.options[CONF_COMPANY_ADDRESS],
                "email": manager.options[CONF_COMPANY_EMAIL],
                "name": manager.options[CONF_COMPANY_NAME],
            }
            generated = [
                (
                    f"{document_type}-{_safe_filename(item['invoice_number'])}.pdf",
                    await manager.hass.async_add_executor_job(
                        build_invoice_pdf, item, company, document_type
                    ),
                )
                for item in invoices
            ]
            if len(generated) == 1:
                filename, content = generated[0]
            else:
                archive = BytesIO()
                with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
                    for generated_name, generated_content in generated:
                        zip_file.writestr(generated_name, generated_content)
                filename, content = "StructuralOffice-documents.zip", archive.getvalue()
            return self.json(
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "count": len(generated),
                    "filename": filename,
                }
            )
        except (StructuralOfficeValidationError, ValueError) as err:
            return self.json({"error": str(err)}, status_code=400)

    @staticmethod
    def _select(manager: StructuralOfficeManager, payload: dict) -> list[dict]:
        invoices = sorted(
            manager.data["invoices"].values(), key=lambda item: item["invoice_number"]
        )
        numbers = {str(item) for item in payload.get("invoice_numbers", [])}
        if numbers:
            return [item for item in invoices if item["invoice_number"] in numbers]
        start = str(payload.get("invoice_number_from") or "")
        end = str(payload.get("invoice_number_to") or start)
        if not start:
            raise StructuralOfficeValidationError("Select invoice numbers or a range")
        return [item for item in invoices if start <= item["invoice_number"] <= end]


class StructuralOfficeBackupsView(HomeAssistantView):
    """Create managed database backups."""

    url = f"{API_PREFIX}/backups"
    name = f"api:{DOMAIN}:v1:backups"

    async def get(self, request: web.Request) -> web.Response:
        _role(request, admin=True)
        return self.json({"backups": _manager(request).backups})

    async def post(self, request: web.Request) -> web.Response:
        _role(request, admin=True)
        return self.json(await _manager(request).async_create_backup())


class StructuralOfficeBackupView(HomeAssistantView):
    """Download, restore, or delete one managed backup."""

    url = f"{API_PREFIX}/backups/{{filename}}"
    name = f"api:{DOMAIN}:v1:backup"

    async def get(self, request: web.Request, filename: str) -> web.Response:
        _role(request, admin=True)
        try:
            content = await _manager(request).hass.async_add_executor_job(
                _manager(request).database.read_backup, filename
            )
            return web.Response(
                body=content,
                content_type="application/vnd.sqlite3",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except StructuralOfficeValidationError as err:
            raise web.HTTPNotFound(text=str(err)) from err

    async def post(self, request: web.Request, filename: str) -> web.Response:
        _role(request, admin=True)
        try:
            await _manager(request).async_restore_backup(filename)
            return self.json_message("Backup restored")
        except StructuralOfficeValidationError as err:
            return self.json({"error": str(err)}, status_code=400)

    async def delete(self, request: web.Request, filename: str) -> web.Response:
        _role(request, admin=True)
        try:
            await _manager(request).async_delete_backup(filename)
            return self.json_message("Backup deleted")
        except StructuralOfficeValidationError as err:
            return self.json({"error": str(err)}, status_code=404)


def async_register(hass: HomeAssistant) -> None:
    """Register the versioned StructuralOffice REST API."""
    for view in (
        StructuralOfficeStatusView,
        StructuralOfficeInvoicesView,
        StructuralOfficeInvoiceImportView,
        StructuralOfficeDocumentsView,
        StructuralOfficeBackupsView,
        StructuralOfficeBackupView,
    ):
        hass.http.register_view(view)
