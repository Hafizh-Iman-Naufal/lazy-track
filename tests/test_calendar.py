import pytest
from datetime import date, timedelta
from decimal import Decimal

from lazytrack.config import LazyTrackConfig, WorkConfig
from lazytrack.domain import (
    LeaveEntry,
    HolidayEntry,
    OvertimeEntry,
    DailyCapacity,
)
from lazytrack.domain.calendar import WorkCalendar, create_calendar


@pytest.fixture
def default_config():
    return LazyTrackConfig()


@pytest.fixture
def calendar(default_config):
    return WorkCalendar(config=default_config.work)


class TestIsWorkingDay:
    def test_monday_is_working_day(self, calendar):
        monday = date(2026, 8, 31)  # A Monday
        assert calendar.is_working_day(monday) is True

    def test_friday_is_working_day(self, calendar):
        friday = date(2026, 9, 4)  # A Friday
        assert calendar.is_working_day(friday) is True

    def test_saturday_is_not_working_day(self, calendar):
        saturday = date(2026, 9, 5)  # A Saturday
        assert calendar.is_working_day(saturday) is False

    def test_sunday_is_not_working_day(self, calendar):
        sunday = date(2026, 9, 6)  # A Sunday
        assert calendar.is_working_day(sunday) is False

    def test_holiday_is_not_working_day(self, calendar):
        monday = date(2026, 8, 31)
        calendar.add_holiday(HolidayEntry(date=monday, description="Company Holiday"))
        assert calendar.is_working_day(monday) is False


class TestDailyCapacity:
    def test_monday_capacity_is_8h(self, calendar):
        monday = date(2026, 8, 31)
        assert calendar.capacity_for_date(monday) == Decimal("8")

    def test_saturday_capacity_is_0(self, calendar):
        saturday = date(2026, 9, 5)
        assert calendar.capacity_for_date(saturday) == Decimal("0")

    def test_sunday_capacity_is_0(self, calendar):
        sunday = date(2026, 9, 6)
        assert calendar.capacity_for_date(sunday) == Decimal("0")

    def test_full_leave_reduces_capacity_to_0(self, calendar):
        monday = date(2026, 8, 31)
        calendar.add_leave(LeaveEntry(date=monday, hours=Decimal("8")))
        assert calendar.capacity_for_date(monday) == Decimal("0")

    def test_partial_leave_reduces_capacity(self, calendar):
        monday = date(2026, 8, 31)
        calendar.add_leave(LeaveEntry(date=monday, hours=Decimal("4")))
        assert calendar.capacity_for_date(monday) == Decimal("4")

    def test_holiday_reduces_capacity_to_0(self, calendar):
        monday = date(2026, 8, 31)
        calendar.add_holiday(HolidayEntry(date=monday))
        assert calendar.capacity_for_date(monday) == Decimal("0")

    def test_max_hours_with_overtime(self, calendar):
        monday = date(2026, 8, 31)
        calendar.add_overtime(OvertimeEntry(date=monday, hours=Decimal("2")))
        assert calendar.max_hours_for_date(monday) == Decimal("10")

    def test_weekend_overtime_does_not_count(self, calendar):
        saturday = date(2026, 9, 5)
        calendar.add_overtime(OvertimeEntry(date=saturday, hours=Decimal("4")))
        assert calendar.max_hours_for_date(saturday) == Decimal("0")


class TestInvalidInputs:
    def test_negative_leave_rejected(self, calendar):
        monday = date(2026, 8, 31)
        with pytest.raises(ValueError, match="Leave hours cannot be negative"):
            calendar.add_leave(LeaveEntry(date=monday, hours=Decimal("-1")))

    def test_leave_exceeding_daily_hours_rejected(self, calendar):
        monday = date(2026, 8, 31)
        with pytest.raises(ValueError, match="Leave hours cannot exceed daily hours"):
            calendar.add_leave(LeaveEntry(date=monday, hours=Decimal("12")))

    def test_negative_overtime_rejected(self, calendar):
        monday = date(2026, 8, 31)
        with pytest.raises(ValueError, match="Overtime hours cannot be negative"):
            calendar.add_overtime(OvertimeEntry(date=monday, hours=Decimal("-1")))


class TestWeeklyCapacity:
    def test_full_week_required_is_40h(self, calendar):
        week_start = date(2026, 8, 31)  # Monday
        weekly = calendar.required_hours_for_week(week_start)
        assert weekly.required_target == Decimal("40")
        assert weekly.normal_target == Decimal("40")

    def test_week_with_full_leave_reduces_required(self, calendar):
        week_start = date(2026, 8, 31)
        monday = date(2026, 8, 31)
        calendar.add_leave(LeaveEntry(date=monday, hours=Decimal("8")))
        
        weekly = calendar.required_hours_for_week(week_start)
        assert weekly.required_target == Decimal("32")

    def test_week_with_holiday_reduces_required(self, calendar):
        week_start = date(2026, 8, 31)
        monday = date(2026, 8, 31)
        calendar.add_holiday(HolidayEntry(date=monday))
        
        weekly = calendar.required_hours_for_week(week_start)
        assert weekly.required_target == Decimal("32")

    def test_week_with_overtime_tracked(self, calendar):
        week_start = date(2026, 8, 31)
        monday = date(2026, 8, 31)
        calendar.add_overtime(OvertimeEntry(date=monday, hours=Decimal("2")))
        
        weekly = calendar.required_hours_for_week(week_start)
        assert weekly.total_overtime == Decimal("2")

    def test_missing_hours_calculation(self, calendar):
        week_start = date(2026, 8, 31)
        weekly = calendar.required_hours_for_week(week_start)
        weekly.logged_hours = Decimal("24")
        
        assert weekly.missing_hours == Decimal("16")

    def test_missing_hours_not_negative(self, calendar):
        week_start = date(2026, 8, 31)
        weekly = calendar.required_hours_for_week(week_start)
        weekly.logged_hours = Decimal("48")
        
        assert weekly.missing_hours == Decimal("0")


class TestWeekBoundaryCrossing:
    def test_week_crossing_month_boundary(self, calendar):
        week_start = date(2026, 9, 28)  # Monday of last week in Sept
        weekly = calendar.required_hours_for_week(week_start)
        assert weekly.required_target == Decimal("40")

    def test_week_crossing_year_boundary(self, calendar):
        week_start = date(2025, 12, 29)  # Monday before New Year
        weekly = calendar.required_hours_for_week(week_start)
        assert weekly.required_target == Decimal("40")


class TestLeapYear:
    def test_leap_year_feb_29(self, calendar):
        feb_29 = date(2028, 2, 29)  # Leap year
        assert calendar.is_working_day(feb_29) is True
        assert calendar.capacity_for_date(feb_29) == Decimal("8")


class TestCRUDOperations:
    def test_add_and_remove_leave(self, calendar):
        monday = date(2026, 8, 31)
        calendar.add_leave(LeaveEntry(date=monday, hours=Decimal("8")))
        assert calendar.capacity_for_date(monday) == Decimal("0")
        
        calendar.remove_leave(monday)
        assert calendar.capacity_for_date(monday) == Decimal("8")

    def test_add_and_remove_holiday(self, calendar):
        monday = date(2026, 8, 31)
        calendar.add_holiday(HolidayEntry(date=monday))
        assert calendar.is_working_day(monday) is False
        
        calendar.remove_holiday(monday)
        assert calendar.is_working_day(monday) is True

    def test_add_and_remove_overtime(self, calendar):
        monday = date(2026, 8, 31)
        calendar.add_overtime(OvertimeEntry(date=monday, hours=Decimal("2")))
        assert calendar.max_hours_for_date(monday) == Decimal("10")
        
        calendar.remove_overtime(monday)
        assert calendar.max_hours_for_date(monday) == Decimal("8")

    def test_remove_nonexistent_returns_false(self, calendar):
        monday = date(2026, 8, 31)
        assert calendar.remove_leave(monday) is False
        assert calendar.remove_holiday(monday) is False
        assert calendar.remove_overtime(monday) is False


class TestGetExceptionsForWeek:
    def test_returns_all_exception_types(self, calendar):
        week_start = date(2026, 8, 31)
        monday = date(2026, 8, 31)
        tuesday = date(2026, 9, 1)
        
        calendar.add_leave(LeaveEntry(date=monday, hours=Decimal("8")))
        calendar.add_holiday(HolidayEntry(date=tuesday, description="Team Event"))
        calendar.add_overtime(OvertimeEntry(date=monday, hours=Decimal("2")))
        
        exceptions = calendar.get_exceptions_for_week(week_start)
        
        assert "leave:8h" in exceptions[monday]
        assert "overtime:2h" in exceptions[monday]
        assert "holiday:Team Event" in exceptions[tuesday]


class TestCustomWorkSchedule:
    def test_custom_hours_per_day(self):
        config = LazyTrackConfig(work={"hours_per_day": 6})
        calendar = WorkCalendar(config=config.work)
        monday = date(2026, 8, 31)
        assert calendar.capacity_for_date(monday) == Decimal("6")

    def test_custom_weekly_target(self):
        config = LazyTrackConfig(work={"weekly_target": 30, "hours_per_day": 6})
        calendar = WorkCalendar(config=config.work)
        week_start = date(2026, 8, 31)
        weekly = calendar.required_hours_for_week(week_start)
        assert weekly.required_target == Decimal("30")
        assert weekly.normal_target == Decimal("30")
