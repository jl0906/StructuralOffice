"""Pure data helpers for StructuralOffice routines and occurrences."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from .const import STATUS_OPEN, VALID_STATUSES


class StructuralOfficeValidationError(ValueError):
    """Raised when StructuralOffice input is invalid."""


def new_id() -> str:
    """Return a stable random object identifier."""
    return uuid4().hex


def _text(value: Any, field: str, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise StructuralOfficeValidationError(f"{field} darf nicht leer sein")
    if len(result) > 5000:
        raise StructuralOfficeValidationError(f"{field} ist zu lang")
    return result


def validate_topic(value: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
    """Validate and normalize a topic."""
    if not isinstance(value, dict):
        raise StructuralOfficeValidationError("Topic muss ein Objekt sein")
    raw_checklist = value.get("checklist", [])
    if not isinstance(raw_checklist, list) or len(raw_checklist) > 100:
        raise StructuralOfficeValidationError("Checkliste ist ungültig")
    checklist = [_text(item, "Checklistenpunkt", required=True) for item in raw_checklist]
    return {
        "id": existing_id or _text(value.get("id"), "ID") or new_id(),
        "name": _text(value.get("name"), "Name", required=True),
        "description": _text(value.get("description"), "Beschreibung"),
        "category": _text(value.get("category"), "Kategorie"),
        "checklist": checklist,
    }


def validate_routine(
    value: dict[str, Any],
    topic_ids: set[str],
    existing_id: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize a routine."""
    if not isinstance(value, dict):
        raise StructuralOfficeValidationError("Routine muss ein Objekt sein")

    selected_topics = value.get("topic_ids", [])
    if not isinstance(selected_topics, list) or not selected_topics:
        raise StructuralOfficeValidationError("Mindestens ein Topic ist erforderlich")
    if unknown := set(selected_topics) - topic_ids:
        raise StructuralOfficeValidationError(f"Unbekannte Topic-IDs: {', '.join(sorted(unknown))}")

    schedule = _validate_schedule(value.get("schedule", {}))
    reminders_raw = value.get("reminder_offsets", [-1, 0])
    if not isinstance(reminders_raw, list):
        raise StructuralOfficeValidationError("Erinnerungen müssen eine Liste sein")
    reminders = sorted({int(item) for item in reminders_raw})
    if any(item < -365 or item > 365 for item in reminders):
        raise StructuralOfficeValidationError(
            "Erinnerungen müssen zwischen -365 und 365 Tagen liegen"
        )

    due_time = _text(value.get("due_time"), "Uhrzeit") or "09:00"
    try:
        datetime.strptime(due_time, "%H:%M")
    except ValueError as err:
        raise StructuralOfficeValidationError("Uhrzeit muss HH:MM entsprechen") from err

    return {
        "id": existing_id or _text(value.get("id"), "ID") or new_id(),
        "name": _text(value.get("name"), "Name", required=True),
        "description": _text(value.get("description"), "Beschreibung"),
        "enabled": bool(value.get("enabled", True)),
        "topic_ids": list(dict.fromkeys(selected_topics)),
        "schedule": schedule,
        "due_time": due_time,
        "reminder_offsets": reminders,
    }


def _validate_schedule(value: Any) -> dict[str, Any]:
    """Validate a recurrence schedule."""
    if not isinstance(value, dict):
        raise StructuralOfficeValidationError("Zeitplan muss ein Objekt sein")
    frequency = value.get("frequency", "monthly")
    if frequency not in {"once", "daily", "weekly", "monthly", "yearly"}:
        raise StructuralOfficeValidationError("Unbekannte Wiederholung")
    interval = int(value.get("interval", 1))
    if not 1 <= interval <= 100:
        raise StructuralOfficeValidationError("Intervall muss zwischen 1 und 100 liegen")
    start_date = _parse_date(value.get("start_date") or date.today().isoformat(), "Startdatum")

    weekdays = sorted({int(item) for item in value.get("weekdays", [start_date.weekday()])})
    if any(item < 0 or item > 6 for item in weekdays):
        raise StructuralOfficeValidationError("Wochentage müssen zwischen 0 und 6 liegen")
    month_days = sorted({int(item) for item in value.get("month_days", [start_date.day])})
    if any(item < 1 or item > 31 for item in month_days):
        raise StructuralOfficeValidationError("Monatstage müssen zwischen 1 und 31 liegen")
    months = sorted({int(item) for item in value.get("months", [start_date.month])})
    if any(item < 1 or item > 12 for item in months):
        raise StructuralOfficeValidationError("Monate müssen zwischen 1 und 12 liegen")
    dates = sorted(
        {_parse_date(item, "Fälligkeitsdatum").isoformat() for item in value.get("dates", [])}
    )
    if frequency == "once" and not dates:
        dates = [start_date.isoformat()]

    return {
        "frequency": frequency,
        "interval": interval,
        "start_date": start_date.isoformat(),
        "weekdays": weekdays,
        "month_days": month_days,
        "months": months,
        "dates": dates,
    }


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as err:
        raise StructuralOfficeValidationError(f"{field} ist kein gültiges Datum") from err


def iter_due_dates(routine: dict[str, Any], start: date, end: date) -> Iterable[date]:
    """Yield due dates for a routine in an inclusive range."""
    schedule = routine["schedule"]
    anchor = date.fromisoformat(schedule["start_date"])
    if end < anchor:
        return
    start = max(start, anchor)
    frequency = schedule["frequency"]
    interval = schedule["interval"]

    if frequency == "once":
        for raw in schedule["dates"]:
            candidate = date.fromisoformat(raw)
            if start <= candidate <= end:
                yield candidate
        return

    if frequency == "daily":
        delta = (start - anchor).days
        first = start + timedelta(days=(-delta) % interval)
        candidate = first
        while candidate <= end:
            yield candidate
            candidate += timedelta(days=interval)
        return

    candidate = start
    anchor_week = anchor - timedelta(days=anchor.weekday())
    while candidate <= end:
        if frequency == "weekly":
            candidate_week = candidate - timedelta(days=candidate.weekday())
            weeks = (candidate_week - anchor_week).days // 7
            matches = weeks % interval == 0 and candidate.weekday() in schedule["weekdays"]
        elif frequency == "monthly":
            months = (candidate.year - anchor.year) * 12 + candidate.month - anchor.month
            matches = months % interval == 0 and candidate.day in schedule["month_days"]
        else:
            years = candidate.year - anchor.year
            matches = (
                years % interval == 0
                and candidate.month in schedule["months"]
                and candidate.day in schedule["month_days"]
                and candidate.day <= monthrange(candidate.year, candidate.month)[1]
            )
        if matches:
            yield candidate
        candidate += timedelta(days=1)


def occurrence_id(routine_id: str, topic_id: str, due_date: date) -> str:
    """Return the deterministic ID of a topic occurrence."""
    return f"{routine_id}:{topic_id}:{due_date.isoformat()}"


def occurrence_status(
    states: dict[str, dict[str, Any]], routine_id: str, topic_id: str, due_date: date
) -> str:
    """Return the stored or default status for an occurrence."""
    state = states.get(occurrence_id(routine_id, topic_id, due_date), {})
    status = state.get("status", STATUS_OPEN)
    return status if status in VALID_STATUSES else STATUS_OPEN
