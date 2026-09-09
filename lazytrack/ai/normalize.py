from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

from dateutil import parser as date_parser

from lazytrack.domain.tz import now_in_zone

from pydantic import ValidationError

from lazytrack.ai.schemas import ClarificationRequired, IntentSchema

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

WEEK_OFFSETS = {"this": 0, "last": -1, "next": 1}

TZ_ALIASES = {
    "wita": "Asia/Makassar",
    "wib": "Asia/Jakarta",
    "wit": "Asia/Jayapura",
}

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


@dataclass
class ParseContext:
    today: date
    timezone: str
    hours_per_day: float
    issue_keys: list[str]
    user_message: str = ""
    window_start: Optional[date] = None
    window_end: Optional[date] = None


def today_in_timezone(tz_name: str) -> date:
    return now_in_zone(tz_name).date()


def timezone_from_text(text: str, default: str) -> str:
    lowered = text.lower()
    for alias, iana in TZ_ALIASES.items():
        if alias in lowered:
            return iana
    return default


def extract_json_object(raw: str) -> Optional[dict[str, Any]]:
    text = raw.strip()
    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _weekday_of_week(target: int, today: date, weeks: int) -> date:
    """Bare and "this" weekday names stay inside the Monday-Sunday week of today."""
    week_start = today - timedelta(days=today.weekday())
    return week_start + timedelta(days=target, weeks=weeks)


def _parse_date_value(value: Any, today: date) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ("today", "now"):
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text == "yesterday":
        return today - timedelta(days=1)
    named_week = re.fullmatch(
        r"(" + "|".join(WEEKDAYS) + r")\s+(this|last|next)\s+week",
        text,
    )
    if named_week:
        name, qualifier = named_week.groups()
        return _weekday_of_week(WEEKDAYS[name], today, WEEK_OFFSETS[qualifier])
    qualified_weekday = re.fullmatch(
        r"(last|next|this)\s+(" + "|".join(WEEKDAYS) + r")",
        text,
    )
    if qualified_weekday:
        qualifier, name = qualified_weekday.groups()
        target = WEEKDAYS[name]
        if qualifier == "this":
            return _weekday_of_week(target, today, 0)
        if qualifier == "last":
            delta = (today.weekday() - target) % 7 or 7
            return today - timedelta(days=delta)
        delta = (target - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)
    if text in WEEKDAYS:
        return _weekday_of_week(WEEKDAYS[text], today, 0)
    weekday_match = next(
        ((name, idx) for name, idx in WEEKDAYS.items() if re.search(rf"\b{name}\b", text)),
        None,
    )
    try:
        parsed = date_parser.parse(
            text,
            fuzzy=True,
            default=datetime(today.year, today.month, today.day),
        ).date()
        if weekday_match and parsed.weekday() != weekday_match[1]:
            return None
        return parsed
    except (ValueError, OverflowError):
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _weekday_in_text(text: str, today: date) -> Optional[date]:
    lowered = text.lower()
    for name, idx in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", lowered):
            delta = (idx - today.weekday()) % 7
            return today + timedelta(days=delta)
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_hhmm(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def _anchor_repeated_start_times(worklogs: list[dict[str, Any]]) -> None:
    """One start time stated for several same-day entries anchors only the first."""
    anchored: set[tuple[str, str]] = set()
    for item in worklogs:
        start_time = item.get("start_time")
        if not start_time:
            continue
        marker = (item["date"], start_time)
        if marker in anchored:
            item["start_time"] = None
        else:
            anchored.add(marker)


def _normalize_gaps(raw: Any) -> list[dict[str, str]]:
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    gaps = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = item.get("start") or item.get("from")
        end = item.get("end") or item.get("to")
        if start and end:
            gaps.append({"start": str(start)[:5], "end": str(end)[:5]})
    return gaps


def _gaps_from_text(text: str) -> list[dict[str, str]]:
    match = re.search(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})", text)
    if not match:
        return []
    start = f"{int(match.group(1)):02d}:{match.group(2)}"
    end = f"{int(match.group(3)):02d}:{match.group(4)}"
    return [{"start": start, "end": end}]


def normalize_intent_dict(data: dict[str, Any], ctx: ParseContext) -> dict[str, Any]:
    out = dict(data)
    blob = json.dumps(data).lower()
    intent_type = str(out.get("type", "")).lower().replace(" ", "_")

    if intent_type in ("allocate_time", "allocate", "allocatetimeintent") and out.get("worklogs"):
        normalized_worklogs: list[Optional[dict[str, Any]]] = []
        unresolved_indexes = []
        fixed_total = 0.0
        for item in out["worklogs"]:
            if not isinstance(item, dict):
                continue
            item_date = _parse_date_value(
                item.get("date") or item.get("date_expression") or out.get("date"),
                ctx.today,
            )
            key = str(item.get("issue_key") or "").upper()
            hours = _as_float(item.get("hours"))
            start_time = _normalize_hhmm(item.get("start_time"))
            if item.get("start_time") and start_time is None:
                continue
            remaining = bool(item.get("remaining")) or str(item.get("hours", "")).lower() == "remaining"
            if remaining:
                unresolved_indexes.append(len(normalized_worklogs))
                normalized_worklogs.append(
                    {
                        "issue_key": key,
                        "date": item_date.isoformat() if item_date else None,
                        "hours": None,
                        "start_time": start_time,
                    }
                )
                continue
            if item_date and key and hours is not None:
                normalized_worklogs.append(
                    {
                        "issue_key": key,
                        "date": item_date.isoformat(),
                        "hours": hours,
                        "start_time": start_time,
                    }
                )
                fixed_total += hours

        total = _as_float(out.get("total_hours"))
        if len(unresolved_indexes) == 1 and total is not None:
            remaining_hours = total - fixed_total
            if remaining_hours > 0:
                normalized_worklogs[unresolved_indexes[0]]["hours"] = remaining_hours
        out["worklogs"] = [
            item
            for item in normalized_worklogs
            if item and item.get("date") and item.get("issue_key") and item.get("hours")
        ]
        _anchor_repeated_start_times(out["worklogs"])

    start = _parse_date_value(out.get("start_date") or out.get("date"), ctx.today)
    if start is None:
        start = _weekday_in_text(str(out.get("week") or ""), ctx.today)
    if start is None:
        start = _weekday_in_text(blob, ctx.today)

    end = _parse_date_value(out.get("end_date"), ctx.today)
    single = _parse_date_value(out.get("date"), ctx.today)

    if start:
        out["start_date"] = start.isoformat()
        if end is None:
            out["end_date"] = start.isoformat()
        else:
            out["end_date"] = end.isoformat()
    if single:
        out["date"] = single.isoformat()
    elif start and out.get("type", "").lower() not in (
        "allocate_time",
        "allocate",
        "allocatetimeintent",
    ):
        out.setdefault("date", start.isoformat())

    hours = _as_float(out.get("hours_per_day")) or _as_float(out.get("hours"))
    if hours is None:
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", blob)
        if match:
            hours = float(match.group(1))
    issue_key = out.get("issue_key")
    allocations = out.get("allocations")
    if not allocations and issue_key:
        if hours is None:
            hours = ctx.hours_per_day
        out["allocations"] = [{"issue_key": issue_key, "hours_per_day": hours}]
        out["hours_per_day"] = hours

    if out.get("allocations"):
        cleaned = []
        for item in out["allocations"]:
            if not isinstance(item, dict):
                continue
            key = item.get("issue_key") or issue_key
            h = _as_float(item.get("hours_per_day")) or hours
            if key and h is not None:
                cleaned.append({"issue_key": key, "hours_per_day": h})
        if cleaned:
            out["allocations"] = cleaned

    gaps = _normalize_gaps(out.get("gaps") or out.get("gap"))
    if not gaps:
        gaps = _gaps_from_text(blob)
    if gaps:
        out["gaps"] = gaps

    if intent_type in ("show_week", "showweekintent"):
        week = out.get("week")
        if isinstance(week, str):
            match = re.fullmatch(r"(\d{4})-w(\d{1,2})", week.strip(), re.IGNORECASE)
            if match:
                out["week"] = f"{match.group(1)}-W{int(match.group(2)):02d}"
    else:
        out.pop("week", None)
    return out


def parse_intent(raw: str | dict[str, Any], ctx: ParseContext):
    if isinstance(raw, str):
        data = extract_json_object(raw)
        if data is None:
            return ClarificationRequired(
                reason=(
                    "Sorry, I could not understand that request. "
                    "I can help with issues, weekly status, allocating time, leave, and holidays."
                ),
                missing_fields=[],
            )
    else:
        data = dict(raw)

    intent_type = str(data.get("type", "")).lower().replace(" ", "_")
    known = {
        "allocate_time",
        "allocate",
        "allocatetimeintent",
        "reallocate_time",
        "reallocatetimeintent",
        "remove_allocation",
        "removeallocationintent",
        "show_week",
        "showweekintent",
        "show_issues",
        "showissuesintent",
        "view_assigned_issues",
        "viewassignedissues",
        "add_leave",
        "addleaveintent",
        "remove_leave",
        "removeleveintent",
        "add_holiday",
        "addholidayintent",
        "remove_holiday",
        "removeholidayintent",
        "clarification_required",
        "clarificationrequired",
    }
    if intent_type and intent_type not in known:
        return ClarificationRequired(
            reason=(
                "Sorry, I can't do that. LazyTrack only handles assigned issues, "
                "worklogs, time allocation, leave, and holidays."
            ),
            missing_fields=[],
        )

    normalized = normalize_intent_dict(data, ctx)
    if intent_type in ("allocate_time", "allocate", "allocatetimeintent"):
        keys = {
            str(item.get("issue_key", "")).upper()
            for field in ("worklogs", "allocations")
            for item in normalized.get(field, [])
            if isinstance(item, dict)
        }
        unknown = sorted(key for key in keys if ctx.issue_keys and key not in ctx.issue_keys)
        if unknown:
            return ClarificationRequired(
                reason=f"I can't find {', '.join(unknown)} in your assigned issues. Which issue should I use?",
                missing_fields=["issue_key"],
            )
        dates = [
            date.fromisoformat(item["date"])
            for item in normalized.get("worklogs", [])
            if isinstance(item, dict) and item.get("date")
        ]
        dates.extend(
            d
            for d in (
                _parse_date_value(normalized.get("start_date"), ctx.today),
                _parse_date_value(normalized.get("end_date"), ctx.today),
            )
            if d is not None
        )
        if (
            dates
            and ctx.window_start
            and ctx.window_end
            and any(d < ctx.window_start or d > ctx.window_end for d in dates)
        ):
            return ClarificationRequired(
                reason=(
                    f"That date is outside the allowed worklog window "
                    f"({ctx.window_start} to {ctx.window_end}). Which allowed date should I use?"
                ),
                missing_fields=["date"],
            )
        if data.get("worklogs") and len(normalized.get("worklogs", [])) != len(data["worklogs"]):
            return ClarificationRequired(
                reason="I couldn't resolve every date or duration. What date and hours should I use?",
                missing_fields=["date", "hours"],
            )
        if not normalized.get("worklogs") and not normalized.get("allocations"):
            return ClarificationRequired(
                reason="Which issue and duration should I log?",
                missing_fields=["issue_key", "hours"],
            )
    try:
        return IntentSchema.model_validate(normalized)
    except (ValidationError, ValueError, TypeError):
        return ClarificationRequired(
            reason=(
                "Sorry, I need a bit more detail. For allocation I need an issue key, "
                "hours, and a date (today or YYYY-MM-DD)."
            ),
            missing_fields=["start_date", "allocations"],
        )
