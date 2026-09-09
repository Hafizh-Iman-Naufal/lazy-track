import re
from datetime import date, timedelta
from decimal import Decimal

from rich.console import Group
from rich.table import Table
from rich.text import Text

from lazytrack.domain.calendar import WorkCalendar

_ISO_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")


def _day_note(calendar: WorkCalendar, d: date) -> str:
    if d in calendar.leaves:
        return "Leave"
    if d in calendar.holidays:
        return "Holiday"
    if d.strftime("%a").lower()[:3] in calendar.config.weekend_days:
        return "Weekend"
    return ""


def iso_week_id(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def parse_iso_week(value: str) -> date:
    match = _ISO_WEEK.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid week format: {value}. Use YYYY-Www")
    try:
        return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    except ValueError as exc:
        raise ValueError(f"Invalid week format: {value}. Use YYYY-Www") from exc


def week_status_renderable(
    week_start: date,
    calendar: WorkCalendar,
    logged_by_date: dict[date, Decimal],
) -> Group:
    week_end = week_start + timedelta(days=6)
    weekly = calendar.required_hours_for_week(week_start)
    total_logged = sum(
        (logged_by_date.get(week_start + timedelta(days=i), Decimal("0")) for i in range(7)),
        Decimal("0"),
    )

    table = Table(
        title=f"Week {iso_week_id(week_start)}: {week_start} - {week_end}"
    )
    table.add_column("Day", style="cyan")
    table.add_column("Date", style="white")
    table.add_column("Expected", style="yellow")
    table.add_column("Logged", style="green")
    table.add_column("Note", style="magenta")

    for i, day_name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        d = week_start + timedelta(days=i)
        table.add_row(
            day_name,
            d.isoformat(),
            f"{calendar.max_hours_for_date(d)}h",
            f"{logged_by_date.get(d, Decimal('0'))}h",
            _day_note(calendar, d),
        )

    required = weekly.required_target + weekly.total_overtime
    missing = max(Decimal("0"), required - total_logged)
    summary = Text.from_markup(
        "\n[bold]Summary:[/bold]\n"
        f"  Required:  {required}h\n"
        f"  Logged:    {total_logged}h\n"
        f"  Missing:   {missing}h"
    )
    return Group(table, summary)
