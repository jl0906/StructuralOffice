"""Portable CSV export adapter for StructuralOffice."""

from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from typing import Any

from .accounting import cents_to_amount


def export_invoices_csv(invoices: list[dict[str, Any]]) -> bytes:
    """Export invoices as semicolon-separated UTF-8 CSV."""
    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "ID",
            "Type",
            "Contact",
            "Invoice_Number",
            "Invoice_Date",
            "Due_Date",
            "Net_Amount",
            "Tax_Amount",
            "Gross_Amount",
            "Currency",
            "Status",
            "Paid_On",
            "Dunning_Level",
            "Note",
        ]
    )
    for invoice in invoices:
        writer.writerow(
            [
                invoice["id"],
                invoice["direction"],
                invoice["contact"],
                invoice["invoice_number"],
                invoice["invoice_date"],
                invoice["due_date"],
                cents_to_amount(invoice["net_cents"]),
                cents_to_amount(invoice["tax_cents"]),
                cents_to_amount(invoice["gross_cents"]),
                invoice["currency"],
                invoice["status"],
                invoice.get("paid_date") or "",
                invoice["dunning_level"],
                invoice["note"],
            ]
        )
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def csv_filename() -> str:
    """Return a stable dated filename."""
    return f"StructuralOffice-Accounting-{date.today().isoformat()}.csv"
