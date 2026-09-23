from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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
    focus_weeks: list[str] = field(default_factory=list)
    focus_dates: list[date] = field(default_factory=list)


@dataclass
class DirectSync:
    """Jira sync is handled by the existing sync command, not the model."""

    type: str = "sync"


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


def _iso_week_id(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _normalize_iso_week(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d{4})-w(\d{1,2})", value.strip(), re.IGNORECASE)
    if not match:
        return None
    return f"{match.group(1)}-W{int(match.group(2)):02d}"


def _weeks_covering(start: date, end: date) -> list[str]:
    last = end if end >= start else start
    weeks: list[str] = []
    d = start
    while d <= last:
        wid = _iso_week_id(d)
        if wid not in weeks:
            weeks.append(wid)
        d += timedelta(days=1)
    return weeks


def _inclusive_dates(start: date, end: date) -> list[date]:
    last = end if end >= start else start
    days: list[date] = []
    d = start
    while d <= last:
        days.append(d)
        d += timedelta(days=1)
    return days


_MONTH_NUMBERS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True))
_WEEKDAY_ALT = "|".join(WEEKDAYS)
_DAY_TOKEN = r"\d{1,2}(?:st|nd|rd|th)?"
_ISO_TOKEN = r"\d{4}-\d{2}-\d{2}"
_MD_TOKEN = (
    rf"(?:(?:{_MONTH_ALT})\.?\s+{_DAY_TOKEN}(?:\s*,?\s*\d{{4}})?"
    rf"|{_DAY_TOKEN}\s+(?:{_MONTH_ALT})\.?(?:\s*,?\s*\d{{4}})?"
    rf"|{_ISO_TOKEN})"
)
_RANGE_SEP = r"\s*(?:-|–|—|\bto\b|\bthrough\b|\buntil\b)\s*"
_DATE_RANGE = re.compile(
    rf"(?:\bfrom\s+)?(?P<a>{_MD_TOKEN}){_RANGE_SEP}(?P<b>{_MD_TOKEN})",
    re.IGNORECASE,
)
_SHORT_RANGE = re.compile(
    rf"(?:\bfrom\s+)?"
    rf"(?:(?P<m1>{_MONTH_ALT})\.?\s+(?P<d1>{_DAY_TOKEN})\s*(?:-|–|—)\s*(?P<d2>{_DAY_TOKEN})"
    rf"|(?P<d3>{_DAY_TOKEN})\s*(?:-|–|—)\s*(?P<d4>{_DAY_TOKEN})\s+(?P<m2>{_MONTH_ALT})\.?)"
    rf"(?:\s*,?\s*(?P<y>\d{{4}}))?",
    re.IGNORECASE,
)
_DAY_LIST_THEN_MONTH = re.compile(
    rf"\b(?P<days>{_DAY_TOKEN}(?:\s*,\s*{_DAY_TOKEN})*)(?:\s*,)?\s+and\s+"
    rf"(?P<last>{_DAY_TOKEN})\s+(?P<m>{_MONTH_ALT})\.?(?:\s*,?\s*(?P<y>\d{{4}}))?\b",
    re.IGNORECASE,
)
_MONTH_THEN_DAY_LIST = re.compile(
    rf"\b(?P<m>{_MONTH_ALT})\.?\s+(?P<body>{_DAY_TOKEN}"
    rf"(?:\s*,\s*{_DAY_TOKEN})+(?:\s*(?:,\s*)?and\s+{_DAY_TOKEN})?)"
    rf"(?:\s*,?\s*(?P<y>\d{{4}}))?\b",
    re.IGNORECASE,
)
_ISO_LIST = re.compile(
    rf"\b(?P<days>{_ISO_TOKEN}(?:\s*,\s*{_ISO_TOKEN})*)(?:\s*,)?\s+and\s+(?P<last>{_ISO_TOKEN})\b"
    rf"|\b(?P<csv>{_ISO_TOKEN}(?:\s*,\s*{_ISO_TOKEN})+)\b",
    re.IGNORECASE,
)
_ONE_DATE = re.compile(rf"\b(?P<token>{_MD_TOKEN})\b", re.IGNORECASE)
_ISO_WEEK_TOKEN = re.compile(r"\b(?P<year>\d{4})-w(?P<week>\d{1,2})\b", re.IGNORECASE)
_QUALIFIED_WEEKDAY = re.compile(
    rf"\b(?:(?P<q1>this|last|next)\s+(?P<d1>{_WEEKDAY_ALT})"
    rf"|(?P<d2>{_WEEKDAY_ALT})\s+(?P<q2>this|last|next)\s+week)\b",
    re.IGNORECASE,
)
_WEEKDAY_RANGE = re.compile(
    rf"\b(?P<a>{_WEEKDAY_ALT})\s*(?:through|to|until|-)\s*(?P<b>{_WEEKDAY_ALT})\b",
    re.IGNORECASE,
)
_WEEK_WORD = re.compile(r"\b(?P<q>this|last|next)\s+week\b", re.IGNORECASE)
_BARE_WEEKDAY = re.compile(rf"\b(?P<name>{_WEEKDAY_ALT})\b", re.IGNORECASE)
_LONE_DAY = re.compile(rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b", re.IGNORECASE)
_EXPLICIT_HOURS = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", re.IGNORECASE)
_ISSUE_KEY = re.compile(r"\b[A-Za-z][A-Za-z0-9]+-\d+\b")
_ALLOCATION_WORD = re.compile(r"\b(allocate|allocation|reallocate|log)\b", re.IGNORECASE)
_FOCUS_WEEK = re.compile(
    r"\b(?:show it again|that week|the same week|same week)\b",
    re.IGNORECASE,
)
_SHOW_WEEK_PHRASE = re.compile(
    r"\blogged hours\b|\bhours logged\b|\bweekly status\b|"
    r"\bshow\b(?:\s+\w+){0,6}\s+status\b|"
    r"\bstatus\b(?:\s+\w+){0,6}\s+week\b|"
    r"\b(?:show|check)\b(?:\s+\w+){0,4}\s+week\b",
    re.IGNORECASE,
)
_MISSING_HOURS = re.compile(
    r"\bmissing hours\b|\bwhat(?:'s| is) missing\b|"
    r"\bhow many hours\b.*\bmissing\b|\bmissing\b.*\bhours\b",
    re.IGNORECASE,
)
_SHOW_VERB = re.compile(r"\b(show|check|display|view)\b", re.IGNORECASE)


@dataclass
class DateSpans:
    dates: list[date] = field(default_factory=list)
    weeks: list[str] = field(default_factory=list)
    clarification: Optional[str] = None


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _date_from_iso_week(week_id: str) -> Optional[date]:
    match = re.fullmatch(r"(\d{4})-W(\d{2})", week_id.strip())
    if not match:
        return None
    try:
        return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        return None


def _focused_week_starts(focus_weeks: list[str], focus_dates: list[date]) -> list[date]:
    starts: list[date] = []
    for week_id in focus_weeks:
        start = _date_from_iso_week(week_id)
        if start and start not in starts:
            starts.append(start)
    if not starts:
        for day in focus_dates:
            start = _week_start(day)
            if start not in starts:
                starts.append(start)
    return starts


def _year_for_month(
    month: int,
    today: date,
    focus_weeks: list[str],
    focus_dates: list[date],
) -> int:
    for day in reversed(focus_dates):
        if day.month == month:
            return day.year
    for start in reversed(_focused_week_starts(focus_weeks, [])):
        for offset in range(7):
            day = start + timedelta(days=offset)
            if day.month == month:
                return day.year
    return today.year


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _day_number(token: str) -> Optional[int]:
    match = re.match(r"(\d{1,2})", token)
    if not match:
        return None
    day = int(match.group(1))
    if 1 <= day <= 31:
        return day
    return None


def _parse_month_day_token(
    token: str,
    today: date,
    focus_weeks: list[str],
    focus_dates: list[date],
) -> Optional[date]:
    text = token.strip().lower().replace(".", "")
    if re.fullmatch(_ISO_TOKEN, text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    month_first = re.fullmatch(
        rf"({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?",
        text,
    )
    if month_first:
        month = _MONTH_NUMBERS[month_first.group(1)]
        day = int(month_first.group(2))
        year = (
            int(month_first.group(3))
            if month_first.group(3)
            else _year_for_month(month, today, focus_weeks, focus_dates)
        )
        return _safe_date(year, month, day)
    day_first = re.fullmatch(
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})(?:\s*,?\s*(\d{{4}}))?",
        text,
    )
    if day_first:
        day = int(day_first.group(1))
        month = _MONTH_NUMBERS[day_first.group(2)]
        year = (
            int(day_first.group(3))
            if day_first.group(3)
            else _year_for_month(month, today, focus_weeks, focus_dates)
        )
        return _safe_date(year, month, day)
    return None


def _overlaps(claimed: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < right and end > left for left, right in claimed)


def _claim(claimed: list[tuple[int, int]], start: int, end: int) -> bool:
    if _overlaps(claimed, start, end):
        return False
    claimed.append((start, end))
    return True


class _SpanBuilder:
    def __init__(self) -> None:
        self.dates: list[date] = []
        self.weeks: list[str] = []

    def add(self, start: date, end: date) -> None:
        if end < start:
            start, end = end, start
        for day in _inclusive_dates(start, end):
            if day not in self.dates:
                self.dates.append(day)
        for week_id in _weeks_covering(start, end):
            if week_id not in self.weeks:
                self.weeks.append(week_id)


def _append_days(
    builder: _SpanBuilder,
    days: list[date],
) -> None:
    for day in days:
        builder.add(day, day)


def extract_date_spans(
    text: str,
    today: date,
    focus_weeks: Optional[list[str]] = None,
    focus_dates: Optional[list[date]] = None,
) -> DateSpans:
    """Find explicit date spans. Does not fuzzy-parse the whole sentence."""
    focus_weeks = list(focus_weeks or [])
    focus_dates = list(focus_dates or [])
    if not text or not text.strip():
        return DateSpans()

    lowered = text.lower()
    claimed: list[tuple[int, int]] = []
    builder = _SpanBuilder()

    def take_range(start: date, end: date, left: int, right: int) -> None:
        if _claim(claimed, left, right):
            builder.add(start, end)

    for match in _ISO_WEEK_TOKEN.finditer(lowered):
        if not _claim(claimed, match.start(), match.end()):
            continue
        week_id = f"{match.group('year')}-W{int(match.group('week')):02d}"
        start = _date_from_iso_week(week_id)
        if start:
            builder.add(start, start + timedelta(days=6))

    for match in _DATE_RANGE.finditer(lowered):
        start = _parse_month_day_token(match.group("a"), today, focus_weeks, focus_dates)
        end = _parse_month_day_token(match.group("b"), today, focus_weeks, focus_dates)
        if start and end:
            take_range(start, end, match.start(), match.end())

    for match in _SHORT_RANGE.finditer(lowered):
        if _overlaps(claimed, match.start(), match.end()):
            continue
        if match.group("m1"):
            month = _MONTH_NUMBERS[match.group("m1").lower()]
            first = _day_number(match.group("d1"))
            second = _day_number(match.group("d2"))
        else:
            month = _MONTH_NUMBERS[match.group("m2").lower()]
            first = _day_number(match.group("d3"))
            second = _day_number(match.group("d4"))
        if first is None or second is None:
            continue
        year = (
            int(match.group("y"))
            if match.group("y")
            else _year_for_month(month, today, focus_weeks, focus_dates)
        )
        start = _safe_date(year, month, first)
        end = _safe_date(year, month, second)
        if start and end:
            take_range(start, end, match.start(), match.end())

    for match in _DAY_LIST_THEN_MONTH.finditer(lowered):
        if _overlaps(claimed, match.start(), match.end()):
            continue
        month = _MONTH_NUMBERS[match.group("m").lower()]
        year = (
            int(match.group("y"))
            if match.group("y")
            else _year_for_month(month, today, focus_weeks, focus_dates)
        )
        numbers = [
            n
            for n in (
                _day_number(piece)
                for piece in re.findall(_DAY_TOKEN, match.group("days"), re.IGNORECASE)
            )
            if n is not None
        ]
        last = _day_number(match.group("last"))
        if last is not None:
            numbers.append(last)
        days = [day for n in numbers if (day := _safe_date(year, month, n))]
        if days and _claim(claimed, match.start(), match.end()):
            _append_days(builder, days)

    for match in _MONTH_THEN_DAY_LIST.finditer(lowered):
        if _overlaps(claimed, match.start(), match.end()):
            continue
        month = _MONTH_NUMBERS[match.group("m").lower()]
        year = (
            int(match.group("y"))
            if match.group("y")
            else _year_for_month(month, today, focus_weeks, focus_dates)
        )
        numbers = [
            n
            for n in (
                _day_number(piece)
                for piece in re.findall(_DAY_TOKEN, match.group("body"), re.IGNORECASE)
            )
            if n is not None
        ]
        days = [day for n in numbers if (day := _safe_date(year, month, n))]
        if days and _claim(claimed, match.start(), match.end()):
            _append_days(builder, days)

    for match in _ISO_LIST.finditer(lowered):
        if _overlaps(claimed, match.start(), match.end()):
            continue
        blob = " ".join(part for part in (match.group("days"), match.group("last"), match.group("csv")) if part)
        days = []
        for token in re.findall(_ISO_TOKEN, blob):
            try:
                parsed = date.fromisoformat(token)
            except ValueError:
                continue
            if parsed not in days:
                days.append(parsed)
        if days and _claim(claimed, match.start(), match.end()):
            _append_days(builder, days)

    for match in _ONE_DATE.finditer(lowered):
        if _overlaps(claimed, match.start(), match.end()):
            continue
        parsed = _parse_month_day_token(match.group("token"), today, focus_weeks, focus_dates)
        if parsed and _claim(claimed, match.start(), match.end()):
            builder.add(parsed, parsed)

    for match in _QUALIFIED_WEEKDAY.finditer(lowered):
        if _overlaps(claimed, match.start(), match.end()):
            continue
        parsed = _parse_date_value(match.group(0), today)
        if parsed and _claim(claimed, match.start(), match.end()):
            builder.add(parsed, parsed)

    for match in _WEEKDAY_RANGE.finditer(lowered):
        if _overlaps(claimed, match.start(), match.end()):
            continue
        start_idx = WEEKDAYS[match.group("a").lower()]
        end_idx = WEEKDAYS[match.group("b").lower()]
        if end_idx < start_idx:
            continue
        week_starts = _focused_week_starts(focus_weeks, focus_dates)
        if len(week_starts) > 1:
            if not builder.dates and not builder.weeks:
                return DateSpans(clarification="Which week should I use?")
            continue
        anchor = week_starts[0] if week_starts else _week_start(today)
        if _claim(claimed, match.start(), match.end()):
            builder.add(anchor + timedelta(days=start_idx), anchor + timedelta(days=end_idx))

    if builder.dates or builder.weeks:
        return DateSpans(dates=builder.dates, weeks=builder.weeks)

    for match in _WEEK_WORD.finditer(lowered):
        if not _claim(claimed, match.start(), match.end()):
            continue
        anchor = _week_start(today) + timedelta(weeks=WEEK_OFFSETS[match.group("q").lower()])
        builder.add(anchor, anchor + timedelta(days=6))

    if not builder.dates:
        week_starts = _focused_week_starts(focus_weeks, focus_dates)
        bare_matches = [
            match
            for match in _BARE_WEEKDAY.finditer(lowered)
            if not _overlaps(claimed, match.start(), match.end())
        ]
        if bare_matches:
            if len(week_starts) > 1:
                return DateSpans(clarification="Which week should I use?")
            anchor = week_starts[0] if week_starts else _week_start(today)
            for match in bare_matches:
                if _claim(claimed, match.start(), match.end()):
                    day = anchor + timedelta(days=WEEKDAYS[match.group("name").lower()])
                    builder.add(day, day)

    if builder.dates or builder.weeks:
        return DateSpans(dates=builder.dates, weeks=builder.weeks)

    lone_days: list[int] = []
    for match in _LONE_DAY.finditer(lowered):
        if _overlaps(claimed, match.start(), match.end()):
            continue
        after = lowered[match.end() : match.end() + 16]
        if re.match(r"\s*(?::|hours?\b|hrs?\b|h\b)", after):
            continue
        if re.search(r"\bweek\s+$", lowered[: match.start()]):
            continue
        number = int(match.group("day"))
        if 1 <= number <= 31:
            lone_days.append(number)
    if lone_days:
        week_starts = _focused_week_starts(focus_weeks, focus_dates)
        if not week_starts:
            return DateSpans(clarification="Which month should I use?")
        resolved: list[date] = []
        for number in lone_days:
            hits = []
            for start in week_starts:
                for offset in range(7):
                    day = start + timedelta(days=offset)
                    if day.day == number:
                        hits.append(day)
            if len(hits) != 1:
                return DateSpans(clarification="Which month should I use?")
            resolved.append(hits[0])
        _append_days(builder, resolved)

    return DateSpans(dates=builder.dates, weeks=builder.weeks)


def asks_missing_hours(text: str) -> bool:
    return bool(_MISSING_HOURS.search(text or ""))


def refers_to_focused_week(text: str) -> bool:
    return bool(_FOCUS_WEEK.search(text or ""))


def route_utterance(text: str) -> Optional[str]:
    """Return a deterministic route, or None when the model should decide."""
    if not text or not text.strip():
        return None
    if re.search(r"\b(sync|refresh|update)\b", text, re.IGNORECASE) and re.search(
        r"\bjira\b", text, re.IGNORECASE
    ):
        return "sync"
    if re.search(r"\bleave\b", text, re.IGNORECASE) and re.search(
        r"\b(remove|undo|unrevert|delete|clear)\b|\btake (?:it |the leave )?off\b",
        text,
        re.IGNORECASE,
    ):
        return "remove_leave"
    if re.search(r"\bleave\b", text, re.IGNORECASE):
        return "add_leave"
    if re.search(r"\bholiday\b", text, re.IGNORECASE) and re.search(
        r"\b(remove|undo|unrevert|delete|clear)\b",
        text,
        re.IGNORECASE,
    ):
        return "remove_holiday"
    if re.search(r"\bholiday\b", text, re.IGNORECASE):
        return "add_holiday"
    if _ISSUE_KEY.search(text) or _ALLOCATION_WORD.search(text):
        return None
    if refers_to_focused_week(text) or _SHOW_WEEK_PHRASE.search(text) or asks_missing_hours(text):
        return "show_week"
    if _SHOW_VERB.search(text):
        spans = extract_date_spans(text, date.today())
        if spans.dates or spans.weeks:
            return "show_week"
    return None


def _explicit_hours(text: str) -> Optional[float]:
    match = _EXPLICIT_HOURS.search(text or "")
    if not match:
        return None
    return float(match.group(1))


def _retarget_intent(data: dict[str, Any], route: str, text: str) -> None:
    data["type"] = route
    if route == "add_leave":
        for key in ("allocations", "worklogs", "issue_key", "week", "weeks", "gaps", "total_hours"):
            data.pop(key, None)
        hours = _explicit_hours(text)
        if hours is None:
            data.pop("hours", None)
            data.pop("hours_per_day", None)
        else:
            data["hours"] = hours
    elif route in ("remove_leave", "add_holiday", "remove_holiday"):
        for key in ("allocations", "worklogs", "issue_key", "week", "weeks", "gaps"):
            data.pop(key, None)
    elif route == "show_week":
        for key in ("allocations", "worklogs", "issue_key", "dates", "date", "hours", "hours_per_day"):
            data.pop(key, None)


def _apply_focus(data: dict[str, Any], ctx: ParseContext, show: bool, calendar: bool):
    weeks = list(ctx.focus_weeks)
    if not weeks and ctx.focus_dates:
        for day in ctx.focus_dates:
            week_id = _iso_week_id(day)
            if week_id not in weeks:
                weeks.append(week_id)
    dates = list(ctx.focus_dates)
    if not dates:
        for week_id in weeks:
            start = _date_from_iso_week(week_id)
            if start:
                dates.extend(start + timedelta(days=offset) for offset in range(7))
    if show:
        if not weeks:
            return ClarificationRequired(
                reason="Which week should I show?",
                missing_fields=["week"],
            )
        data["weeks"] = weeks
        data["week"] = weeks[0]
        data.pop("start_date", None)
        data.pop("end_date", None)
        return None
    if calendar:
        if not dates:
            return ClarificationRequired(
                reason="Which dates should I use?",
                missing_fields=["date"],
            )
        data["dates"] = [day.isoformat() for day in dates]
        data["date"] = dates[0].isoformat()
        data.pop("start_date", None)
        data.pop("end_date", None)
    return None


def _merge_spans(data: dict[str, Any], ctx: ParseContext):
    text = ctx.user_message or ""
    if not text.strip():
        return None
    intent_type = str(data.get("type", "")).lower().replace(" ", "_")
    calendar = intent_type in _CALENDAR_TYPES
    show = intent_type in ("show_week", "showweekintent")
    if not calendar and not show:
        return None
    spans = extract_date_spans(text, ctx.today, ctx.focus_weeks, ctx.focus_dates)
    if spans.dates or spans.weeks:
        if show and spans.weeks:
            data["weeks"] = list(spans.weeks)
            data["week"] = spans.weeks[0]
            data.pop("start_date", None)
            data.pop("end_date", None)
        if calendar and spans.dates:
            data["dates"] = [day.isoformat() for day in spans.dates]
            data["date"] = spans.dates[0].isoformat()
            data.pop("start_date", None)
            data.pop("end_date", None)
        return None
    if refers_to_focused_week(text):
        return _apply_focus(data, ctx, show, calendar)
    if spans.clarification:
        missing = ["date"] if calendar else ["week"]
        return ClarificationRequired(reason=spans.clarification, missing_fields=missing)
    return None


def _prepare_intent_data(data: dict[str, Any], ctx: ParseContext):
    prepared = dict(data)
    route = route_utterance(ctx.user_message)
    if route and route != "sync":
        _retarget_intent(prepared, route, ctx.user_message)
    problem = _merge_spans(prepared, ctx)
    if problem is not None:
        return problem
    return prepared


_CALENDAR_TYPES = {
    "add_leave",
    "addleaveintent",
    "remove_leave",
    "removeleveintent",
    "add_holiday",
    "addholidayintent",
    "remove_holiday",
    "removeholidayintent",
}


def parse_followup_date(text: str, today: date) -> Optional[date]:
    stripped = text.strip().strip("'\"")
    stripped = re.sub(r"^date\s*=\s*", "", stripped, flags=re.I).strip().strip("'\"")
    if not stripped:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        try:
            return date.fromisoformat(stripped)
        except ValueError:
            return None
    lowered = stripped.lower()
    weekday = "|".join(WEEKDAYS)
    if (
        lowered in ("today", "now", "tomorrow", "yesterday")
        or lowered in WEEKDAYS
        or re.fullmatch(rf"(last|next|this)\s+({weekday})", lowered)
        or re.fullmatch(rf"({weekday})\s+(this|last|next)\s+week", lowered)
    ):
        return _parse_date_value(stripped, today)
    return None


def _dates_from_user_message(ctx: ParseContext) -> list[date]:
    if not ctx.user_message:
        return []
    lines = [line.strip() for line in ctx.user_message.splitlines() if line.strip()]
    candidates: list[str] = []
    if lines:
        last = lines[-1]
        if last.lower().startswith("clarification:"):
            last = last.split(":", 1)[1].strip()
        candidates.append(last.strip("'\""))
    candidates.append(ctx.user_message.strip().strip("'\""))
    for candidate in candidates:
        parsed = parse_followup_date(candidate, ctx.today)
        if parsed:
            return [parsed]
    found = re.findall(r"\d{4}-\d{2}-\d{2}", ctx.user_message)
    days: list[date] = []
    for item in found:
        try:
            parsed = date.fromisoformat(item)
        except ValueError:
            continue
        if parsed not in days:
            days.append(parsed)
    return days


def _calendar_dates(
    out: dict[str, Any],
    start: Optional[date],
    end: Optional[date],
    ctx: ParseContext,
) -> None:
    parsed_dates: list[date] = []
    raw_dates = out.get("dates")
    if isinstance(raw_dates, list):
        for item in raw_dates:
            parsed = _parse_date_value(item, ctx.today)
            if parsed:
                parsed_dates.append(parsed)
    if not parsed_dates and start:
        parsed_dates = _inclusive_dates(start, end or start)
    if not parsed_dates:
        parsed_dates = _dates_from_user_message(ctx)
    if parsed_dates:
        out["dates"] = [d.isoformat() for d in parsed_dates]
        out["date"] = parsed_dates[0].isoformat()
        if start and end and end != start and len(parsed_dates) > 1:
            out["end_date"] = parsed_dates[-1].isoformat()


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

    if intent_type in _CALENDAR_TYPES:
        _calendar_dates(out, start, end, ctx)

    if intent_type in ("show_week", "showweekintent"):
        weeks: list[str] = []
        raw_weeks = out.get("weeks")
        if isinstance(raw_weeks, list):
            for item in raw_weeks:
                wid = _normalize_iso_week(item)
                if wid and wid not in weeks:
                    weeks.append(wid)
        week = _normalize_iso_week(out.get("week"))
        if week and week not in weeks:
            weeks.insert(0, week)
        if not weeks and start:
            weeks = _weeks_covering(start, end or start)
        if weeks:
            out["weeks"] = weeks
            out["week"] = weeks[0]
    else:
        out.pop("week", None)
        out.pop("weeks", None)
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

    if route_utterance(ctx.user_message) == "sync":
        return DirectSync()

    prepared = _prepare_intent_data(data, ctx)
    if isinstance(prepared, ClarificationRequired):
        return prepared
    data = prepared

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
            has_hours = any(
                _as_float(normalized.get(key)) is not None
                for key in ("hours", "hours_per_day", "total_hours")
            )
            if has_hours:
                return ClarificationRequired(
                    reason="Which issue should I log those hours on?",
                    missing_fields=["issue_key"],
                )
            return ClarificationRequired(
                reason="Which issue and how many hours should I log?",
                missing_fields=["issue_key", "hours"],
            )
    try:
        return IntentSchema.model_validate(normalized)
    except (ValidationError, ValueError, TypeError):
        if intent_type in ("add_leave", "addleaveintent"):
            return ClarificationRequired(
                reason="Which dates should I mark as leave? Use YYYY-MM-DD.",
                missing_fields=["date"],
            )
        if intent_type in ("show_week", "showweekintent"):
            return ClarificationRequired(
                reason="Which week should I show? Use YYYY-Www or a date range.",
                missing_fields=["week"],
            )
        if intent_type in ("add_holiday", "addholidayintent", "remove_holiday", "removeholidayintent"):
            return ClarificationRequired(
                reason="Which date should I use for that holiday? Use YYYY-MM-DD.",
                missing_fields=["date"],
            )
        if intent_type in ("remove_leave", "removeleveintent"):
            return ClarificationRequired(
                reason="Which leave date should I remove? Use YYYY-MM-DD.",
                missing_fields=["date"],
            )
        return ClarificationRequired(
            reason="Which issue, hours, and date should I use?",
            missing_fields=["issue_key", "hours", "date"],
        )
