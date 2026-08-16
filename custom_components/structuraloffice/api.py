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
from .database import StructuralOfficeConflictError
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


def _identity(request: web.Request) -> tuple[str, str]:
    user = request["hass_user"]
    return user.id, user.name or user.id


def _integer(value: str | None, default: int, field: str) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError as err:
        raise StructuralOfficeValidationError(f"{field} must be an integer") from err


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


class StructuralOfficeLiveCollectionView(HomeAssistantView):
    """List and create revisioned records for the Windows client."""

    url = f"{API_PREFIX}/live/{{collection}}"
    name = f"api:{DOMAIN}:v1:live-collection"

    async def get(self, request: web.Request, collection: str) -> web.Response:
        _role(request)
        try:
            result = await _manager(request).async_live_list(
                collection,
                include_archived=request.query.get("include_archived") == "true",
                limit=_integer(request.query.get("limit"), 100, "limit"),
                offset=_integer(request.query.get("offset"), 0, "offset"),
            )
            return self.json(result)
        except StructuralOfficeValidationError as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)

    async def post(self, request: web.Request, collection: str) -> web.Response:
        _role(request, write=True)
        try:
            payload = await request.json()
            user_id, user_name = _identity(request)
            result = await _manager(request).async_live_write(
                collection,
                payload.get("data", {}),
                None,
                payload.get("expected_revision"),
                user_id,
                user_name,
            )
            return self.json(result, status_code=201)
        except StructuralOfficeConflictError as err:
            return self.json(
                {"code": "revision_conflict", "current": err.current, "error": str(err)},
                status_code=409,
            )
        except (StructuralOfficeValidationError, TypeError, ValueError) as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)


class StructuralOfficeLiveRecordView(HomeAssistantView):
    """Read, update, or archive one revisioned record."""

    url = f"{API_PREFIX}/live/{{collection}}/{{record_id}}"
    name = f"api:{DOMAIN}:v1:live-record"

    async def get(
        self, request: web.Request, collection: str, record_id: str
    ) -> web.Response:
        _role(request)
        try:
            return self.json(await _manager(request).async_live_get(collection, record_id))
        except StructuralOfficeValidationError as err:
            return self.json({"code": "not_found", "error": str(err)}, status_code=404)

    async def patch(
        self, request: web.Request, collection: str, record_id: str
    ) -> web.Response:
        _role(request, write=True)
        try:
            payload = await request.json()
            if "expected_revision" not in payload:
                raise StructuralOfficeValidationError("expected_revision is required")
            user_id, user_name = _identity(request)
            result = await _manager(request).async_live_write(
                collection,
                payload.get("data", {}),
                record_id,
                int(payload["expected_revision"]),
                user_id,
                user_name,
            )
            return self.json(result)
        except StructuralOfficeConflictError as err:
            return self.json(
                {"code": "revision_conflict", "current": err.current, "error": str(err)},
                status_code=409,
            )
        except (StructuralOfficeValidationError, TypeError, ValueError) as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)

    async def delete(
        self, request: web.Request, collection: str, record_id: str
    ) -> web.Response:
        _role(request, write=True)
        try:
            expected_revision = _integer(
                request.query.get("expected_revision"), -1, "expected_revision"
            )
            if expected_revision < 0:
                raise StructuralOfficeValidationError("expected_revision is required")
            user_id, user_name = _identity(request)
            result = await _manager(request).async_live_archive(
                collection, record_id, expected_revision, user_id, user_name
            )
            return self.json(result)
        except StructuralOfficeConflictError as err:
            return self.json(
                {"code": "revision_conflict", "current": err.current, "error": str(err)},
                status_code=409,
            )
        except (StructuralOfficeValidationError, ValueError) as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)


class StructuralOfficeEditingView(HomeAssistantView):
    """Manage expiring edit-presence sessions."""

    url = f"{API_PREFIX}/editing/{{collection}}/{{record_id}}"
    name = f"api:{DOMAIN}:v1:editing"

    async def get(
        self, request: web.Request, collection: str, record_id: str
    ) -> web.Response:
        _role(request)
        try:
            editors = await _manager(request).hass.async_add_executor_job(
                _manager(request).database.active_edit_sessions, collection, record_id
            )
            return self.json({"editors": editors})
        except StructuralOfficeValidationError as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)

    async def post(
        self, request: web.Request, collection: str, record_id: str
    ) -> web.Response:
        _role(request, write=True)
        try:
            payload = await request.json()
            user_id, user_name = _identity(request)
            result = await _manager(request).async_start_edit_session(
                collection,
                record_id,
                str(payload.get("client_id") or "windows-client"),
                user_id,
                user_name,
                int(payload.get("ttl_seconds", 60)),
                payload.get("session_id"),
            )
            return self.json(result)
        except (StructuralOfficeValidationError, TypeError, ValueError) as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)

    async def delete(
        self, request: web.Request, collection: str, record_id: str
    ) -> web.Response:
        _role(request, write=True)
        session_id = str(request.query.get("session_id") or "")
        if not session_id:
            return self.json(
                {"code": "invalid_request", "error": "session_id is required"},
                status_code=400,
            )
        ended = await _manager(request).async_end_edit_session(
            collection, record_id, session_id, request["hass_user"].id
        )
        return self.json({"ended": ended})


class StructuralOfficeEventsView(HomeAssistantView):
    """Return missed live events after a reconnect cursor."""

    url = f"{API_PREFIX}/events"
    name = f"api:{DOMAIN}:v1:events"

    async def get(self, request: web.Request) -> web.Response:
        _role(request)
        try:
            return self.json(
                await _manager(request).async_events_since(
                    _integer(request.query.get("after"), 0, "after"),
                    _integer(request.query.get("limit"), 200, "limit"),
                )
            )
        except StructuralOfficeValidationError as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)


class StructuralOfficeAuditView(HomeAssistantView):
    """Return administrator-visible change metadata."""

    url = f"{API_PREFIX}/audit"
    name = f"api:{DOMAIN}:v1:audit"

    async def get(self, request: web.Request) -> web.Response:
        _role(request, admin=True)
        try:
            return self.json(
                await _manager(request).async_audit_entries(
                    _integer(request.query.get("limit"), 100, "limit"),
                    _integer(request.query.get("offset"), 0, "offset"),
                )
            )
        except StructuralOfficeValidationError as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)


class StructuralOfficeRolesView(HomeAssistantView):
    """List and assign StructuralOffice roles for Windows-client administration."""

    url = f"{API_PREFIX}/roles"
    name = f"api:{DOMAIN}:v1:roles"

    async def get(self, request: web.Request) -> web.Response:
        _role(request, admin=True)
        manager = _manager(request)
        users = await manager.hass.auth.async_get_users()
        return self.json(
            {
                "users": [
                    {
                        "id": user.id,
                        "is_active": user.is_active,
                        "is_admin": user.is_admin,
                        "name": user.name or user.id,
                        "role": manager.user_role(user.id, user.is_admin),
                    }
                    for user in users
                    if not user.system_generated
                ]
            }
        )

    async def put(self, request: web.Request) -> web.Response:
        _role(request, admin=True)
        try:
            payload = await request.json()
            role = payload.get("role")
            if role == "none":
                role = None
            await _manager(request).async_set_user_role(str(payload["user_id"]), role)
            return self.json_message("Role updated")
        except (KeyError, StructuralOfficeValidationError, TypeError) as err:
            return self.json({"code": "invalid_request", "error": str(err)}, status_code=400)


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
        StructuralOfficeLiveCollectionView,
        StructuralOfficeLiveRecordView,
        StructuralOfficeEditingView,
        StructuralOfficeEventsView,
        StructuralOfficeAuditView,
        StructuralOfficeRolesView,
        StructuralOfficeInvoiceImportView,
        StructuralOfficeDocumentsView,
        StructuralOfficeBackupsView,
        StructuralOfficeBackupView,
    ):
        hass.http.register_view(view)
