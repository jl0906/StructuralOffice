"""Parser for invoice-list CSV exports consumed by StructuralOffice."""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import StringIO
from typing import Any

from .accounting import (
    DIRECTION_RECEIVABLE,
    INVOICE_STATUS_CANCELLED,
    INVOICE_STATUS_OPEN,
    INVOICE_STATUS_PAID,
    validate_invoice,
)
from .models import StructuralOfficeValidationError

EXPECTED_HEADERS = (
    "Rechnungsnummer",
    "Kundennummer",
    "Rechnungsempfänger",
    "Rechnungsdatum",
    "Netto gesamt",
    "Mehrwertsteuer gesamt",
    "Brutto gesamt",
    "Eingangsdatum",
    "Eingangsbetrag gesamt",
    "Offener Rechnungsbetrag",
)


def source_checksum(content: bytes) -> str:
    """Return the stable SHA-256 checksum of an uploaded source file."""
    return hashlib.sha256(content).hexdigest()


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise StructuralOfficeValidationError("CSV encoding must be UTF-8 or Windows-1252")


def _cents(value: Any, field: str) -> int:
    raw = str(value or "").strip().replace(" ", "")
    if not raw:
        return 0
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation as err:
        raise StructuralOfficeValidationError(f"{field} is not a valid amount") from err
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _source_date(value: Any, field: str, *, required: bool = False) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise StructuralOfficeValidationError(f"{field} is required")
        return None
    for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    raise StructuralOfficeValidationError(f"{field} is not a valid date")


def _record_id(invoice_number: str) -> str:
    digest = hashlib.sha256(invoice_number.encode("utf-8")).hexdigest()[:24]
    return f"csv_{digest}"


def parse_invoice_list_csv(
    content: bytes,
    payment_term_days: int,
    *,
    use_sepa_date: bool = True,
) -> dict[str, Any]:
    """Parse and consolidate a semicolon-separated invoice-list export."""
    if not 0 <= payment_term_days <= 365:
        raise StructuralOfficeValidationError("Payment term must be between 0 and 365 days")
    if len(content) > 20_000_000:
        raise StructuralOfficeValidationError("CSV file is larger than 20 MB")

    reader = csv.DictReader(StringIO(_decode(content)), delimiter=";")
    headers = tuple(reader.fieldnames or ())
    missing = [header for header in EXPECTED_HEADERS if header not in headers]
    if missing:
        raise StructuralOfficeValidationError(
            f"CSV is missing required columns: {', '.join(missing)}"
        )

    groups: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    errors: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        invoice_number = str(row.get("Rechnungsnummer") or "").strip()
        if not invoice_number:
            if any(str(value or "").strip() for value in row.values()):
                errors.append({"row": row_number, "message": "Invoice number is missing"})
            continue
        groups[invoice_number].append((row_number, row))

    records: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    cancelled = 0
    for invoice_number, rows in groups.items():
        try:
            parsed = [
                (row_number, row, _cents(row["Offener Rechnungsbetrag"], "Open amount"))
                for row_number, row in rows
            ]
            is_cancelled = any(open_cents < 0 for _, _, open_cents in parsed)
            candidates = [item for item in parsed if item[2] >= 0] or parsed
            row_number, source, open_cents = max(
                candidates,
                key=lambda item: abs(_cents(item[1]["Brutto gesamt"], "Gross amount")),
            )
            invoice_date = _source_date(
                source["Rechnungsdatum"], "Invoice date", required=True
            )
            sepa_date = _source_date(source.get("Abbuchungstag SEPA"), "SEPA debit date")
            due_date = (
                sepa_date
                if use_sepa_date and sepa_date is not None
                else invoice_date + timedelta(days=payment_term_days)
            )
            status = (
                INVOICE_STATUS_CANCELLED
                if is_cancelled
                else INVOICE_STATUS_OPEN
                if open_cents > 0
                else INVOICE_STATUS_PAID
            )
            paid_date = _source_date(source.get("Eingangsdatum"), "Payment date")
            raw = {
                "id": _record_id(invoice_number),
                "direction": DIRECTION_RECEIVABLE,
                "contact": source.get("Rechnungsempfänger") or "Unknown recipient",
                "invoice_number": invoice_number,
                "invoice_date": invoice_date.isoformat(),
                "due_date": due_date.isoformat(),
                "net_cents": abs(_cents(source["Netto gesamt"], "Net amount")),
                "tax_cents": abs(
                    _cents(source["Mehrwertsteuer gesamt"], "Tax amount")
                ),
                "gross_cents": abs(_cents(source["Brutto gesamt"], "Gross amount")),
                "outstanding_cents": max(0, open_cents),
                "currency": "EUR",
                "status": status,
                "paid_date": paid_date.isoformat() if paid_date else None,
                "payment_term_days": payment_term_days,
                "source": "invoice_list_csv",
                "source_customer_number": str(source.get("Kundennummer") or "").strip(),
                "source_sepa_date": sepa_date.isoformat() if sepa_date else None,
            }
            records.append(validate_invoice(raw, raw["id"]))
            if len(rows) > 1:
                warnings.append(
                    {
                        "row": row_number,
                        "message": (
                            f"Invoice {invoice_number} was consolidated from {len(rows)} rows"
                        ),
                    }
                )
            cancelled += int(is_cancelled)
        except (KeyError, StructuralOfficeValidationError) as err:
            errors.append({"row": rows[0][0], "message": str(err)})

    return {
        "cancelled": cancelled,
        "checksum": source_checksum(content),
        "errors": errors,
        "records": sorted(records, key=lambda item: item["invoice_number"]),
        "source_rows": sum(len(rows) for rows in groups.values()),
        "warnings": warnings,
    }
