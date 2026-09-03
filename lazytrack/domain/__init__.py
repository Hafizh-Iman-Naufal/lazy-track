from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ExceptionType(str, Enum):
    LEAVE = "leave"
    HOLIDAY = "holiday"
    OVERTIME = "overtime"


class CalendarException(BaseModel):
    date: date
    exception_type: ExceptionType
    hours: Decimal = Field(default=Decimal("0"))
    description: Optional[str] = None


class LeaveEntry(BaseModel):
    date: date
    hours: Decimal = Field(default=Decimal("8"))
    description: Optional[str] = None


class HolidayEntry(BaseModel):
    date: date
    description: Optional[str] = None


class OvertimeEntry(BaseModel):
    date: date
    hours: Decimal
    description: Optional[str] = None


class DailyCapacity(BaseModel):
    date: date
    normal_capacity: Decimal
    leave_hours: Decimal = Field(default=Decimal("0"))
    approved_overtime: Decimal = Field(default=Decimal("0"))
    is_weekend: bool = False
    is_holiday: bool = False

    @property
    def required_capacity(self) -> Decimal:
        if self.is_weekend or self.is_holiday:
            return Decimal("0")
        base = self.normal_capacity - self.leave_hours
        return max(base, Decimal("0"))

    @property
    def max_capacity(self) -> Decimal:
        if self.is_weekend:
            return Decimal("0")
        return self.required_capacity + self.approved_overtime


class WeeklyCapacity(BaseModel):
    week_start: date
    normal_target: Decimal
    required_target: Decimal
    total_overtime: Decimal = Field(default=Decimal("0"))
    allocated_hours: Decimal = Field(default=Decimal("0"))
    logged_hours: Decimal = Field(default=Decimal("0"))

    @property
    def effective_logged(self) -> Decimal:
        return self.allocated_hours + self.logged_hours

    @property
    def missing_hours(self) -> Decimal:
        return max(
            self.required_target + self.total_overtime - self.effective_logged,
            Decimal("0")
        )

    @property
    def remaining_capacity(self) -> Decimal:
        return max(
            self.required_target + self.total_overtime - self.effective_logged,
            Decimal("0")
        )


from lazytrack.domain.planner import (
    Allocation,
    AllocationRequest,
    AmbiguousIssueError,
    DuplicateOperationError,
    InvalidDateError,
    ManualWorklogModificationError,
    OperationStatus,
    OperationType,
    OverCapacityError,
    Planner,
    PlannerContext,
    Plan,
    PlanOperation,
    UnassignedIssueError,
    ValidationError,
    WeekendAllocationError,
)

__all__ = [
    "ExceptionType",
    "CalendarException",
    "LeaveEntry",
    "HolidayEntry",
    "OvertimeEntry",
    "DailyCapacity",
    "WeeklyCapacity",
    "OperationType",
    "OperationStatus",
    "Allocation",
    "AllocationRequest",
    "PlanOperation",
    "Plan",
    "Planner",
    "PlannerContext",
    "ValidationError",
    "InvalidDateError",
    "WeekendAllocationError",
    "OverCapacityError",
    "UnassignedIssueError",
    "AmbiguousIssueError",
    "DuplicateOperationError",
    "ManualWorklogModificationError",
]
