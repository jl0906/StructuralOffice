"""Analytics helpers for StructuralOffice."""

from __future__ import annotations

from datetime import date
from typing import Any

from .accounting import (
    DIRECTION_PAYABLE,
    DIRECTION_RECEIVABLE,
    INVOICE_STATUS_OPEN,
)


def _month_shift(value: date, delta: int) -> tuple[int, int]:
    absolute = value.year * 12 + value.month - 1 + delta
    return absolute // 12, absolute % 12 + 1


def build_accounting_analytics(
    invoices: list[dict[str, Any]], today: date
) -> dict[str, Any]:
    """Build rolling monthly totals and receivables aging."""
    months = []
    for delta in range(-11, 1):
        year, month = _month_shift(today, delta)
        key = f"{year:04d}-{month:02d}"
        months.append(
            {
                "key": key,
                "label": f"{month:02d}/{str(year)[2:]}",
                "payables_cents": 0,
                "receivables_cents": 0,
                "paid_payables_cents": 0,
                "paid_receivables_cents": 0,
            }
        )
    by_key = {item["key"]: item for item in months}

    aging = {
        "not_due_cents": 0,
        "days_1_7_cents": 0,
        "days_8_14_cents": 0,
        "days_15_30_cents": 0,
        "days_31_plus_cents": 0,
    }
    for invoice in invoices:
        invoice_month = invoice["invoice_date"][:7]
        if invoice_month in by_key:
            target = by_key[invoice_month]
            key = (
                "payables_cents"
                if invoice["direction"] == DIRECTION_PAYABLE
                else "receivables_cents"
            )
            target[key] += invoice["gross_cents"]

        paid_date = invoice.get("paid_date")
        if paid_date and paid_date[:7] in by_key:
            target = by_key[paid_date[:7]]
            key = (
                "paid_payables_cents"
                if invoice["direction"] == DIRECTION_PAYABLE
                else "paid_receivables_cents"
            )
            target[key] += invoice["gross_cents"]

        if (
            invoice["status"] != INVOICE_STATUS_OPEN
            or invoice["direction"] != DIRECTION_RECEIVABLE
        ):
            continue
        days = (today - date.fromisoformat(invoice["due_date"])).days
        if days <= 0:
            aging["not_due_cents"] += invoice["gross_cents"]
        elif days <= 7:
            aging["days_1_7_cents"] += invoice["gross_cents"]
        elif days <= 14:
            aging["days_8_14_cents"] += invoice["gross_cents"]
        elif days <= 30:
            aging["days_15_30_cents"] += invoice["gross_cents"]
        else:
            aging["days_31_plus_cents"] += invoice["gross_cents"]

    max_monthly_cents = max(
        (
            max(item["payables_cents"], item["receivables_cents"])
            for item in months
        ),
        default=0,
    )
    return {
        "months": months,
        "aging": aging,
        "max_monthly_cents": max_monthly_cents,
    }


def build_workflow_analytics(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    """Build task completion metrics."""
    total = len(occurrences)
    completed = sum(item["status"] == "completed" for item in occurrences)
    skipped = sum(item["status"] == "skipped" for item in occurrences)
    open_count = sum(item["status"] == "open" for item in occurrences)
    return {
        "total": total,
        "completed": completed,
        "skipped": skipped,
        "open": open_count,
        "completion_rate": round(completed / total * 100, 1) if total else 0,
    }

