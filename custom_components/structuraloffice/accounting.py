"""Accounting data helpers for StructuralOffice."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .models import StructuralOfficeValidationError, new_id

DIRECTION_PAYABLE = "payable"
DIRECTION_RECEIVABLE = "receivable"
VALID_DIRECTIONS = {DIRECTION_PAYABLE, DIRECTION_RECEIVABLE}

INVOICE_STATUS_OPEN = "open"
INVOICE_STATUS_PAID = "paid"
INVOICE_STATUS_CANCELLED = "cancelled"
VALID_INVOICE_STATUSES = {
    INVOICE_STATUS_OPEN,
    INVOICE_STATUS_PAID,
    INVOICE_STATUS_CANCELLED,
}


def _text(value: Any, field: str, *, required: bool = False, limit: int = 5000) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise StructuralOfficeValidationError(f"{field} must not be empty")
    if len(result) > limit:
        raise StructuralOfficeValidationError(f"{field} is too long")
    return result


def _date(value: Any, field: str, *, required: bool = False) -> str | None:
    if value in (None, ""):
        if required:
            raise StructuralOfficeValidationError(f"{field} is required")
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError as err:
        raise StructuralOfficeValidationError(f"{field} is not a valid date") from err


def amount_to_cents(value: Any, field: str) -> int:
    """Convert a user-entered amount to integer cents."""
    if value in (None, ""):
        return 0
    normalized = str(value).strip().replace(" ", "")
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    try:
        decimal = Decimal(normalized)
    except InvalidOperation as err:
        raise StructuralOfficeValidationError(f"{field} is not a valid amount") from err
    if decimal < 0 or decimal > Decimal("999999999999.99"):
        raise StructuralOfficeValidationError(f"{field} is outside the valid range")
    return int((decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_amount(cents: int) -> str:
    """Convert integer cents to a frontend-safe decimal string."""
    return f"{Decimal(cents) / 100:.2f}"


def _offsets(value: Any, field: str, default: list[int]) -> list[int]:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        value = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list):
        raise StructuralOfficeValidationError(f"{field} must be a list")
    try:
        result = sorted({int(item) for item in value})
    except (TypeError, ValueError) as err:
        raise StructuralOfficeValidationError(f"{field} contains invalid values") from err
    if any(item < -365 or item > 365 for item in result):
        raise StructuralOfficeValidationError(f"{field} must be between -365 and 365")
    return result


def validate_invoice(value: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
    """Validate and normalize an invoice."""
    if not isinstance(value, dict):
        raise StructuralOfficeValidationError("Accounting record must be an object")

    direction = str(value.get("direction", "")).strip().lower()
    if direction not in VALID_DIRECTIONS:
        raise StructuralOfficeValidationError("Type must be payable or receivable")
    status = str(value.get("status", INVOICE_STATUS_OPEN)).strip().lower()
    if status not in VALID_INVOICE_STATUSES:
        raise StructuralOfficeValidationError("Invalid payment status")

    net_cents = (
        int(value["net_cents"])
        if "net_cents" in value
        else amount_to_cents(value.get("net_amount"), "Net amount")
    )
    tax_cents = (
        int(value["tax_cents"])
        if "tax_cents" in value
        else amount_to_cents(value.get("tax_amount"), "Tax amount")
    )
    gross_cents = (
        int(value["gross_cents"])
        if "gross_cents" in value
        else amount_to_cents(value.get("gross_amount"), "Gross amount")
    )
    if gross_cents == 0 and (net_cents or tax_cents):
        gross_cents = net_cents + tax_cents
    if min(net_cents, tax_cents, gross_cents) < 0:
        raise StructuralOfficeValidationError("Invoice amounts must not be negative")

    outstanding_cents = (
        int(value["outstanding_cents"])
        if "outstanding_cents" in value
        else gross_cents if status == INVOICE_STATUS_OPEN else 0
    )
    if outstanding_cents < 0:
        raise StructuralOfficeValidationError("Outstanding amount must not be negative")

    payment_term_days = int(value.get("payment_term_days", 0))
    if not 0 <= payment_term_days <= 365:
        raise StructuralOfficeValidationError(
            "Payment term must be between 0 and 365 days"
        )
    invoice_date = _date(value.get("invoice_date"), "Invoice date", required=True)
    due_date = _date(value.get("due_date"), "Due date")
    if due_date is None:
        due_date = (
            date.fromisoformat(invoice_date) + timedelta(days=payment_term_days)
        ).isoformat()

    paid_date = _date(value.get("paid_date"), "Paid on")
    if (
        status == INVOICE_STATUS_PAID
        and paid_date is None
        and value.get("source", "manual") == "manual"
    ):
        paid_date = date.today().isoformat()
    if status != INVOICE_STATUS_PAID:
        paid_date = None

    invoice_id = existing_id or _text(value.get("id"), "ID") or new_id()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", invoice_id) is None:
        raise StructuralOfficeValidationError("ID contains invalid characters")

    return {
        "id": invoice_id,
        "direction": direction,
        "contact": _text(value.get("contact"), "Contact", required=True, limit=300),
        "contact_address": _text(
            value.get("contact_address"), "Contact address", limit=1000
        ),
        "invoice_number": _text(
            value.get("invoice_number"), "Invoice number", required=True, limit=200
        ),
        "invoice_date": invoice_date,
        "due_date": due_date,
        "net_cents": net_cents,
        "tax_cents": tax_cents,
        "gross_cents": gross_cents,
        "outstanding_cents": outstanding_cents,
        "currency": (_text(value.get("currency"), "Currency") or "EUR").upper()[:3],
        "status": status,
        "paid_date": paid_date,
        "dunning_level": max(0, min(9, int(value.get("dunning_level", 0)))),
        "payment_reminder_offsets": _offsets(
            value.get("payment_reminder_offsets"),
            "Payment reminders",
            [],
        ),
        "dunning_offsets": [
            item
            for item in _offsets(value.get("dunning_offsets"), "Dunning periods", [])
            if item >= 0
        ],
        "note": _text(value.get("note"), "Note"),
        "payment_term_days": payment_term_days,
        "source": _text(value.get("source"), "Source", limit=100) or "manual",
        "source_customer_number": _text(
            value.get("source_customer_number"), "Source customer number", limit=200
        ),
        "source_sepa_date": _date(value.get("source_sepa_date"), "SEPA debit date"),
        "updated_at": datetime.now().astimezone().isoformat(),
    }


def invoice_for_frontend(invoice: dict[str, Any], today: date) -> dict[str, Any]:
    """Serialize an invoice with derived frontend fields."""
    result = dict(invoice)
    result["net_amount"] = cents_to_amount(invoice["net_cents"])
    result["tax_amount"] = cents_to_amount(invoice["tax_cents"])
    result["gross_amount"] = cents_to_amount(invoice["gross_cents"])
    result["outstanding_amount"] = cents_to_amount(
        invoice.get("outstanding_cents", invoice["gross_cents"])
    )
    result["is_overdue"] = (
        invoice["status"] == INVOICE_STATUS_OPEN and invoice["due_date"] < today.isoformat()
    )
    result["due_state"] = (
        "inactive"
        if invoice["status"] != INVOICE_STATUS_OPEN
        else "overdue"
        if invoice["due_date"] < today.isoformat()
        else "due_today"
        if invoice["due_date"] == today.isoformat()
        else "upcoming"
    )
    return result


def accounting_summary(invoices: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """Calculate accounting dashboard metrics."""
    open_items = [item for item in invoices if item["status"] == INVOICE_STATUS_OPEN]
    payables = [item for item in open_items if item["direction"] == DIRECTION_PAYABLE]
    receivables = [item for item in open_items if item["direction"] == DIRECTION_RECEIVABLE]
    overdue_receivables = [item for item in receivables if item["due_date"] < today.isoformat()]
    due_payments = [item for item in payables if item["due_date"] <= today.isoformat()]
    return {
        "open_payables": len(payables),
        "open_receivables": len(receivables),
        "due_payments": len(due_payments),
        "overdue_receivables": len(overdue_receivables),
        "open_payables_cents": sum(item["gross_cents"] for item in payables),
        "open_receivables_cents": sum(item["gross_cents"] for item in receivables),
    }
