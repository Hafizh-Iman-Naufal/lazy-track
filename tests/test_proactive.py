import pytest
from datetime import date, timedelta
from decimal import Decimal

from lazytrack.config import LazyTrackConfig
from lazytrack.domain import LeaveEntry, HolidayEntry
from lazytrack.domain.calendar import WorkCalendar


class TestSuggestMissingHours:
    def test_incomplete_week_returns_suggestion(self):
        config = LazyTrackConfig()
        calendar = WorkCalendar(config=config.work)
        
        from lazytrack.ui.chat import suggest_missing_hours
        
        week_start = date(2026, 8, 31)
        result = suggest_missing_hours(week_start, calendar, {})
        assert result is not None
        assert "unallocated" in result
        assert "2026-W36" in result

    def test_leave_adjusts_missing_hours(self):
        config = LazyTrackConfig()
        calendar = WorkCalendar(config=config.work)
        calendar.add_leave(LeaveEntry(date=date(2026, 8, 31), hours=Decimal("8")))
        
        from lazytrack.ui.chat import suggest_missing_hours
        
        week_start = date(2026, 8, 31)
        result = suggest_missing_hours(week_start, calendar, {})
        assert result is not None
        assert "unallocated" in result

    def test_holiday_adjusts_missing_hours(self):
        config = LazyTrackConfig()
        calendar = WorkCalendar(config=config.work)
        calendar.add_holiday(HolidayEntry(date=date(2026, 8, 31)))
        
        from lazytrack.ui.chat import suggest_missing_hours
        
        week_start = date(2026, 8, 31)
        result = suggest_missing_hours(week_start, calendar, {})
        assert result is not None
        assert "unallocated" in result

    def test_fully_logged_week_returns_none(self):
        config = LazyTrackConfig()
        calendar = WorkCalendar(config=config.work)

        from lazytrack.ui.chat import suggest_missing_hours

        week_start = date(2026, 8, 31)
        logged = {week_start + timedelta(days=i): Decimal("9") for i in range(5)}
        assert suggest_missing_hours(week_start, calendar, logged) is None

    def test_partial_week_omits_covered_days(self):
        config = LazyTrackConfig()
        calendar = WorkCalendar(config=config.work)

        from lazytrack.ui.chat import suggest_missing_hours

        week_start = date(2026, 8, 31)
        logged = {week_start + timedelta(days=i): Decimal("8") for i in range(4)}
        result = suggest_missing_hours(week_start, calendar, logged)

        assert result is not None
        assert "8.0h unallocated" in result
        assert "Friday need hours" in result
        assert "Monday" not in result


class TestLoggedHoursByDate:
    def test_only_counts_days_inside_the_week(self):
        from lazytrack.jira.models import WorklogEntry
        from lazytrack.ui.chat import logged_hours_by_date

        week_start = date(2026, 8, 31)
        worklogs = [
            WorklogEntry(
                id="1",
                issue_key="SP-1",
                work_date=week_start,
                seconds=3600,
                author_is_current_user=True,
            ),
            WorklogEntry(
                id="2",
                issue_key="SP-1",
                work_date=week_start,
                seconds=1800,
                author_is_current_user=True,
            ),
            WorklogEntry(
                id="3",
                issue_key="SP-1",
                work_date=week_start - timedelta(days=1),
                seconds=7200,
                author_is_current_user=True,
            ),
        ]

        totals = logged_hours_by_date(worklogs, week_start)

        assert len(totals) == 7
        assert totals[week_start] == Decimal("1.5")
        assert totals[week_start + timedelta(days=1)] == Decimal("0")


class TestFormatWeekStatus:
    def test_format_includes_summary(self):
        from rich.console import Console

        from lazytrack.ui.status import week_status_renderable
        
        week_start = date(2026, 8, 31)
        calendar = WorkCalendar(config=LazyTrackConfig().work)
        logged = {
            week_start: Decimal("8"),
            week_start + timedelta(days=1): Decimal("16"),
        }

        console = Console(record=True, width=100, color_system=None)
        console.print(week_status_renderable(week_start, calendar, logged))
        text = console.export_text()

        assert "Week 2026-W36: 2026-08-31 - 2026-09-06" in text
        assert "Required:  40.0h" in text
        assert "Logged:    24h" in text
        assert "Missing:   16.0h" in text
        assert "Weekend" in text

    def test_format_notes_leave_and_holiday(self):
        from rich.console import Console

        from lazytrack.domain import HolidayEntry, LeaveEntry
        from lazytrack.ui.status import week_status_renderable

        week_start = date(2026, 8, 3)
        calendar = WorkCalendar(config=LazyTrackConfig().work)
        calendar.add_leave(LeaveEntry(date=date(2026, 8, 3), hours=Decimal("8")))
        calendar.add_holiday(HolidayEntry(date=date(2026, 8, 4), description="x"))
        console = Console(record=True, width=120, color_system=None)
        console.print(week_status_renderable(week_start, calendar, {}))
        text = console.export_text()
        assert "Leave" in text
        assert "Holiday" in text
        assert "Weekend" in text
