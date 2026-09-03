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
        result = suggest_missing_hours(week_start, calendar)
        assert result is not None
        assert "unallocated" in result

    def test_leave_adjusts_missing_hours(self):
        config = LazyTrackConfig()
        calendar = WorkCalendar(config=config.work)
        calendar.add_leave(LeaveEntry(date=date(2026, 8, 31), hours=Decimal("8")))
        
        from lazytrack.ui.chat import suggest_missing_hours
        
        week_start = date(2026, 8, 31)
        result = suggest_missing_hours(week_start, calendar)
        assert result is not None
        assert "unallocated" in result

    def test_holiday_adjusts_missing_hours(self):
        config = LazyTrackConfig()
        calendar = WorkCalendar(config=config.work)
        calendar.add_holiday(HolidayEntry(date=date(2026, 8, 31)))
        
        from lazytrack.ui.chat import suggest_missing_hours
        
        week_start = date(2026, 8, 31)
        result = suggest_missing_hours(week_start, calendar)
        assert result is not None
        assert "unallocated" in result


class TestFormatWeekStatus:
    def test_format_includes_summary(self):
        from lazytrack.ui.chat import format_week_status
        from lazytrack.domain import WeeklyCapacity
        
        week_start = date(2026, 8, 31)
        weekly = WeeklyCapacity(
            week_start=week_start,
            normal_target=Decimal("40"),
            required_target=Decimal("40"),
        )
        logged = Decimal("24")
        
        result = format_week_status(week_start, weekly, logged)
        assert "40" in result
        assert "24" in result
