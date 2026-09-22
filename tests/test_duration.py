from datetime import date
from decimal import Decimal

from rich.console import Console

from lazytrack.config import LazyTrackConfig
from lazytrack.domain.calendar import WorkCalendar
from lazytrack.domain.duration import format_hours
from lazytrack.ui.status import week_status_renderable


def test_format_hours_whole_hours():
    assert format_hours(Decimal(8)) == "8h"
    assert format_hours(Decimal("8.0")) == "8h"


def test_format_hours_half_hour():
    assert format_hours(Decimal("8.5")) == "8h 30m"


def test_format_hours_nearest_minute_from_seconds():
    assert format_hours(Decimal(31620) / 3600) == "8h 47m"


def test_format_hours_zero():
    assert format_hours(Decimal(0)) == "0h"


def test_week_status_formats_logged_seconds():
    week_start = date(2026, 8, 31)
    calendar = WorkCalendar(config=LazyTrackConfig().work)
    logged = {date(2026, 9, 2): Decimal(31620) / 3600}

    console = Console(record=True, width=120, color_system=None)
    console.print(week_status_renderable(week_start, calendar, logged))
    text = console.export_text()

    assert "8h 47m" in text
    assert "7833" not in text
