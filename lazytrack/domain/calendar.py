from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from lazytrack.config import LazyTrackConfig, WorkConfig
from lazytrack.domain import (
    DailyCapacity,
    ExceptionType,
    HolidayEntry,
    LeaveEntry,
    OvertimeEntry,
    WeeklyCapacity,
)


@dataclass
class WorkCalendar:
    config: WorkConfig
    leaves: dict[date, LeaveEntry] = field(default_factory=dict)
    holidays: dict[date, HolidayEntry] = field(default_factory=dict)
    overtime: dict[date, OvertimeEntry] = field(default_factory=dict)

    def _is_weekend(self, d: date) -> bool:
        return d.strftime("%a").lower()[:3] in self.config.weekend_days

    def _is_working_day(self, d: date) -> bool:
        return d.strftime("%a").lower()[:3] in self.config.working_days

    def is_working_day(self, d: date) -> bool:
        if self._is_weekend(d):
            return False
        if d in self.holidays:
            return False
        return True

    def capacity_for_date(self, d: date) -> Decimal:
        cap = self._daily_capacity(d)
        return cap.required_capacity

    def max_hours_for_date(self, d: date) -> Decimal:
        cap = self._daily_capacity(d)
        return cap.max_capacity

    def _daily_capacity(self, d: date) -> DailyCapacity:
        normal = Decimal(str(self.config.hours_per_day))
        is_weekend = self._is_weekend(d)
        is_holiday = d in self.holidays
        leave_hours = Decimal("0")
        approved_overtime = Decimal("0")

        if d in self.leaves:
            leave_hours = self.leaves[d].hours

        if d in self.overtime and not is_weekend:
            approved_overtime = self.overtime[d].hours

        return DailyCapacity(
            date=d,
            normal_capacity=normal,
            leave_hours=leave_hours,
            approved_overtime=approved_overtime,
            is_weekend=is_weekend,
            is_holiday=is_holiday,
        )

    def _get_week_start(self, d: date) -> date:
        days_since_monday = d.weekday()
        return d - timedelta(days=days_since_monday)

    def required_hours_for_week(self, week_start: Optional[date] = None) -> WeeklyCapacity:
        if week_start is None:
            week_start = self._get_week_start(date.today())

        normal_target = Decimal(str(self.config.weekly_target))
        required_target = Decimal("0")
        total_overtime = Decimal("0")

        for i in range(7):
            d = week_start + timedelta(days=i)
            cap = self._daily_capacity(d)
            if self._is_working_day(d):
                required_target += cap.required_capacity
                total_overtime += cap.approved_overtime

        return WeeklyCapacity(
            week_start=week_start,
            normal_target=normal_target,
            required_target=required_target,
            total_overtime=total_overtime,
        )

    def add_leave(self, leave: LeaveEntry) -> None:
        if leave.hours < 0:
            raise ValueError("Leave hours cannot be negative")
        if leave.hours > Decimal(str(self.config.hours_per_day)):
            raise ValueError("Leave hours cannot exceed daily hours")
        self.leaves[leave.date] = leave

    def remove_leave(self, d: date) -> bool:
        if d in self.leaves:
            del self.leaves[d]
            return True
        return False

    def add_holiday(self, holiday: HolidayEntry) -> None:
        self.holidays[holiday.date] = holiday

    def remove_holiday(self, d: date) -> bool:
        if d in self.holidays:
            del self.holidays[d]
            return True
        return False

    def add_overtime(self, ot: OvertimeEntry) -> None:
        if ot.hours < 0:
            raise ValueError("Overtime hours cannot be negative")
        self.overtime[ot.date] = ot

    def remove_overtime(self, d: date) -> bool:
        if d in self.overtime:
            del self.overtime[d]
            return True
        return False

    def get_exceptions_for_week(self, week_start: date) -> dict[date, list[str]]:
        exceptions: dict[date, list[str]] = {week_start + timedelta(days=i): [] for i in range(7)}
        
        for d, leave in self.leaves.items():
            if d >= week_start and d < week_start + timedelta(days=7):
                exceptions[d].append(f"leave:{leave.hours}h")
        
        for d, holiday in self.holidays.items():
            if d >= week_start and d < week_start + timedelta(days=7):
                desc = holiday.description or "Holiday"
                exceptions[d].append(f"holiday:{desc}")
        
        for d, ot in self.overtime.items():
            if d >= week_start and d < week_start + timedelta(days=7):
                exceptions[d].append(f"overtime:{ot.hours}h")
        
        return exceptions


def create_calendar(config: LazyTrackConfig) -> WorkCalendar:
    return WorkCalendar(config=config.work)
