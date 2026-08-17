"""Pure data helpers for StructuralOffice routines and occurrences."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import STATUS_OPEN, VALID_STATUSES


class StructuralOfficeValidationError(ValueError):
    """Raised when StructuralOffice input is invalid."""


def new_id() -> str:
    """Return a stable random object identifier."""
    return uuid4().hex


def _text(
    value: Any, field: str, *, required: bool = False, limit: int = 5000
) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise StructuralOfficeValidationError(f"{field} must not be empty")
    if len(result) > limit:
        raise StructuralOfficeValidationError(f"{field} is too long")
    return result


def validate_topic(value: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
    """Validate and normalize a topic."""
    if not isinstance(value, dict):
        raise StructuralOfficeValidationError("Topic must be an object")
    raw_checklist = value.get("checklist", [])
    if not isinstance(raw_checklist, list) or len(raw_checklist) > 100:
        raise StructuralOfficeValidationError("Checklist is invalid")
    checklist = [_text(item, "Checklist item", required=True) for item in raw_checklist]
    raw_steps = value.get("steps")
    if raw_steps is None:
        steps = [
            {
                "enabled": True,
                "estimated_minutes": 0,
                "id": f"step-{position}",
                "required": True,
                "title": title,
            }
            for position, title in enumerate(checklist)
        ]
    else:
        if not isinstance(raw_steps, list) or len(raw_steps) > 100:
            raise StructuralOfficeValidationError("Topic steps are invalid")
        steps = []
        for position, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict):
                raise StructuralOfficeValidationError("Topic step must be an object")
            step_minutes = int(raw_step.get("estimated_minutes", 0))
            if not 0 <= step_minutes <= 100_000:
                raise StructuralOfficeValidationError(
                    "Step estimated minutes are outside the valid range"
                )
            steps.append(
                {
                    "enabled": bool(raw_step.get("enabled", True)),
                    "estimated_minutes": step_minutes,
                    "id": _text(raw_step.get("id"), "Step ID") or f"step-{position}",
                    "required": bool(raw_step.get("required", True)),
                    "title": _text(raw_step.get("title"), "Step title", required=True),
                }
            )
        checklist = [step["title"] for step in steps if step["enabled"]]
    step_ids = [step["id"] for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise StructuralOfficeValidationError("Topic step IDs must be unique")
    priority = str(value.get("priority", "normal")).strip().lower()
    if priority not in {"low", "normal", "high", "critical"}:
        raise StructuralOfficeValidationError("Invalid topic priority")
    estimated_minutes = int(value.get("estimated_minutes", 0))
    if not 0 <= estimated_minutes <= 100_000:
        raise StructuralOfficeValidationError("Estimated minutes are outside the valid range")
    return {
        "id": existing_id or _text(value.get("id"), "ID") or new_id(),
        "name": _text(value.get("name"), "Name", required=True),
        "description": _text(value.get("description"), "Description"),
        "category": _text(value.get("category"), "Category"),
        "checklist": checklist,
        "enabled": bool(value.get("enabled", True)),
        "estimated_minutes": estimated_minutes,
        "instructions": _text(value.get("instructions"), "Instructions"),
        "priority": priority,
        "steps": steps,
    }


def validate_contact(value: dict[str, Any], existing_id: str | None = None) -> dict[str, Any]:
    """Validate and normalize a business contact."""
    if not isinstance(value, dict):
        raise StructuralOfficeValidationError("Contact must be an object")
    return {
        "address": _text(value.get("address"), "Address"),
        "customer_number": _text(value.get("customer_number"), "Customer number"),
        "email": _text(value.get("email"), "Email", limit=320),
        "id": existing_id or _text(value.get("id"), "ID") or new_id(),
        "name": _text(value.get("name"), "Name", required=True),
        "note": _text(value.get("note"), "Note"),
        "phone": _text(value.get("phone"), "Phone", limit=100),
    }


def validate_routine(
    value: dict[str, Any],
    topic_ids: set[str],
    existing_id: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize a routine."""
    if not isinstance(value, dict):
        raise StructuralOfficeValidationError("Routine must be an object")

    selected_topics = value.get("topic_ids", [])
    if not isinstance(selected_topics, list):
        raise StructuralOfficeValidationError("Topic IDs must be a list")
    if unknown := set(selected_topics) - topic_ids:
        raise StructuralOfficeValidationError(f"Unknown topic IDs: {', '.join(sorted(unknown))}")

    schedule = _validate_schedule(value.get("schedule", {}))
    reminders_raw = value.get("reminder_offsets", [-1, 0])
    if not isinstance(reminders_raw, list):
        raise StructuralOfficeValidationError("Reminders must be a list")
    reminders = sorted({int(item) for item in reminders_raw})
    if any(item < -365 or item > 365 for item in reminders):
        raise StructuralOfficeValidationError(
            "Reminders must be between -365 and 365 days"
        )

    due_time = _text(value.get("due_time"), "Time") or "09:00"
    try:
        datetime.strptime(due_time, "%H:%M")
    except ValueError as err:
        raise StructuralOfficeValidationError("Time must use HH:MM format") from err

    end_date = _date_or_none(value.get("end_date"), "End date")
    if end_date and date.fromisoformat(end_date) < date.fromisoformat(
        schedule["start_date"]
    ):
        raise StructuralOfficeValidationError("End date must not precede start date")
    catch_up_policy = str(value.get("catch_up_policy", "configured_window"))
    if catch_up_policy not in {"configured_window", "latest_only", "skip_missed"}:
        raise StructuralOfficeValidationError("Invalid catch-up policy")
    timezone = _text(value.get("timezone"), "Timezone") or "Europe/Berlin"
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as err:
        raise StructuralOfficeValidationError("Unknown timezone") from err
    if existing_id is None and "estimated_minutes" not in value:
        raise StructuralOfficeValidationError("Estimated minutes are required")
    estimated_minutes = int(value.get("estimated_minutes", 10))
    if not 1 <= estimated_minutes <= 1440:
        raise StructuralOfficeValidationError(
            "Estimated minutes must be between 1 and 1440"
        )
    priority = str(value.get("priority", "normal")).strip().lower()
    if priority not in {"low", "normal", "high", "critical"}:
        raise StructuralOfficeValidationError("Invalid routine priority")

    return {
        "id": existing_id or _text(value.get("id"), "ID") or new_id(),
        "name": _text(value.get("name"), "Name", required=True),
        "description": _text(value.get("description"), "Description"),
        "enabled": bool(value.get("enabled", True)),
        "topic_ids": list(dict.fromkeys(selected_topics)),
        "schedule": schedule,
        "due_time": due_time,
        "reminder_offsets": reminders,
        "timezone": timezone,
        "end_date": end_date,
        "catch_up_policy": catch_up_policy,
        "estimated_minutes": estimated_minutes,
        "priority": priority,
    }


def _validate_schedule(value: Any) -> dict[str, Any]:
    """Validate a recurrence schedule."""
    if not isinstance(value, dict):
        raise StructuralOfficeValidationError("Schedule must be an object")
    frequency = value.get("frequency", "monthly")
    if frequency not in {"once", "daily", "weekly", "monthly", "yearly"}:
        raise StructuralOfficeValidationError("Unknown recurrence")
    interval = int(value.get("interval", 1))
    if not 1 <= interval <= 100:
        raise StructuralOfficeValidationError("Interval must be between 1 and 100")
    start_date = _parse_date(value.get("start_date") or date.today().isoformat(), "Start date")

    weekdays = sorted({int(item) for item in value.get("weekdays", [start_date.weekday()])})
    if any(item < 0 or item > 6 for item in weekdays):
        raise StructuralOfficeValidationError("Weekdays must be between 0 and 6")
    month_days = sorted({int(item) for item in value.get("month_days", [start_date.day])})
    if any(item < 1 or item > 31 for item in month_days):
        raise StructuralOfficeValidationError("Month days must be between 1 and 31")
    months = sorted({int(item) for item in value.get("months", [start_date.month])})
    if any(item < 1 or item > 12 for item in months):
        raise StructuralOfficeValidationError("Months must be between 1 and 12")
    dates = sorted(
        {_parse_date(item, "Due date").isoformat() for item in value.get("dates", [])}
    )
    non_working_dates = sorted(
        {
            _parse_date(item, "Non-working date").isoformat()
            for item in value.get("non_working_dates", [])
        }
    )
    if frequency == "once" and not dates:
        dates = [start_date.isoformat()]

    business_day_rule = str(value.get("business_day_rule", "none"))
    if business_day_rule not in {"none", "previous_business_day", "next_business_day"}:
        raise StructuralOfficeValidationError("Invalid business-day rule")
    invalid_day_rule = str(value.get("invalid_day_rule", "skip"))
    if invalid_day_rule not in {"skip", "last_day"}:
        raise StructuralOfficeValidationError("Invalid invalid-day rule")

    return {
        "frequency": frequency,
        "interval": interval,
        "start_date": start_date.isoformat(),
        "weekdays": weekdays,
        "month_days": month_days,
        "months": months,
        "dates": dates,
        "business_day_rule": business_day_rule,
        "invalid_day_rule": invalid_day_rule,
        "non_working_dates": non_working_dates,
    }


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as err:
        raise StructuralOfficeValidationError(f"{field} is not a valid date") from err


def _date_or_none(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    return _parse_date(value, field).isoformat()


def iter_due_dates(routine: dict[str, Any], start: date, end: date) -> Iterable[date]:
    """Yield adjusted, unique due dates for a routine in an inclusive range."""
    end_date = routine.get("end_date")
    if end_date:
        end = min(end, date.fromisoformat(end_date))
    if end < start:
        return
    seen: set[date] = set()
    non_working_dates = {
        date.fromisoformat(item)
        for item in routine["schedule"].get("non_working_dates", [])
    }
    for candidate in _iter_base_due_dates(
        routine, start - timedelta(days=3), end + timedelta(days=3)
    ):
        adjusted = _adjust_business_day(
            candidate,
            routine["schedule"].get("business_day_rule", "none"),
            non_working_dates,
        )
        if start <= adjusted <= end and adjusted not in seen:
            seen.add(adjusted)
            yield adjusted


def _adjust_business_day(value: date, rule: str, non_working_dates: set[date]) -> date:
    if rule == "none":
        return value
    direction = -1 if rule == "previous_business_day" else 1
    while value.weekday() >= 5 or value in non_working_dates:
        value += timedelta(days=direction)
    return value


def _iter_base_due_dates(
    routine: dict[str, Any], start: date, end: date
) -> Iterable[date]:
    """Yield unadjusted recurrence dates."""
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
            last_day = monthrange(candidate.year, candidate.month)[1]
            valid_days = {day for day in schedule["month_days"] if day <= last_day}
            if schedule.get("invalid_day_rule") == "last_day" and any(
                day > last_day for day in schedule["month_days"]
            ):
                valid_days.add(last_day)
            matches = months % interval == 0 and candidate.day in valid_days
        else:
            years = candidate.year - anchor.year
            last_day = monthrange(candidate.year, candidate.month)[1]
            valid_days = {day for day in schedule["month_days"] if day <= last_day}
            if schedule.get("invalid_day_rule") == "last_day" and any(
                day > last_day for day in schedule["month_days"]
            ):
                valid_days.add(last_day)
            matches = (
                years % interval == 0
                and candidate.month in schedule["months"]
                and candidate.day in valid_days
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
