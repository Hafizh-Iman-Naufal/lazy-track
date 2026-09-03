from datetime import date
from enum import Enum
from typing import Optional, Union, Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class IntentType(str, Enum):
    ALLOCATE_TIME = "allocate_time"
    REALLOCATE_TIME = "reallocate_time"
    REMOVE_ALLOCATION = "remove_allocation"
    SHOW_WEEK = "show_week"
    SHOW_ISSUES = "show_issues"
    ADD_LEAVE = "add_leave"
    REMOVE_LEAVE = "remove_leave"
    ADD_HOLIDAY = "add_holiday"
    REMOVE_HOLIDAY = "remove_holiday"
    ADD_OVERTIME = "add_overtime"
    REMOVE_OVERTIME = "remove_overtime"
    CLARIFICATION_REQUIRED = "clarification_required"


class AllocationItem(BaseModel):
    issue_key: str
    hours_per_day: float = Field(gt=0, le=24)


class AllocateTimeIntent(BaseModel):
    type: Literal["allocate_time"] = "allocate_time"
    start_date: date
    end_date: date
    allocations: list[AllocationItem]

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v, info):
        if "start_date" in info.data and v < info.data["start_date"]:
            raise ValueError("end_date must be after start_date")
        return v


class ReallocateTimeIntent(BaseModel):
    type: Literal["reallocate_time"] = "reallocate_time"
    date: date
    from_issue_key: Optional[str] = None
    to_issue_key: str
    hours: float = Field(gt=0, le=24)


class RemoveAllocationIntent(BaseModel):
    type: Literal["remove_allocation"] = "remove_allocation"
    date: date
    issue_key: str
    hours: Optional[float] = Field(default=None, gt=0, le=24)


class ShowWeekIntent(BaseModel):
    type: Literal["show_week"] = "show_week"
    week: Optional[str] = None


class ShowIssuesIntent(BaseModel):
    type: Literal["show_issues"] = "show_issues"


class AddLeaveIntent(BaseModel):
    type: Literal["add_leave"] = "add_leave"
    date: date
    hours: Optional[float] = Field(default=None, ge=0, le=24)


class RemoveLeaveIntent(BaseModel):
    type: Literal["remove_leave"] = "remove_leave"
    date: date


class AddHolidayIntent(BaseModel):
    type: Literal["add_holiday"] = "add_holiday"
    date: date
    description: Optional[str] = None


class RemoveHolidayIntent(BaseModel):
    type: Literal["remove_holiday"] = "remove_holiday"
    date: date


class AddOvertimeIntent(BaseModel):
    type: Literal["add_overtime"] = "add_overtime"
    date: date
    hours: float = Field(gt=0, le=24)


class RemoveOvertimeIntent(BaseModel):
    type: Literal["remove_overtime"] = "remove_overtime"
    date: date


class ClarificationRequired(BaseModel):
    type: Literal["clarification_required"] = "clarification_required"
    reason: str
    missing_fields: list[str] = Field(default_factory=list)


UnionIntent = Annotated[
    Union[
        AllocateTimeIntent,
        ReallocateTimeIntent,
        RemoveAllocationIntent,
        ShowWeekIntent,
        ShowIssuesIntent,
        AddLeaveIntent,
        RemoveLeaveIntent,
        AddHolidayIntent,
        RemoveHolidayIntent,
        AddOvertimeIntent,
        RemoveOvertimeIntent,
        ClarificationRequired,
    ],
    Field(discriminator="type"),
]

__all__ = [
    "IntentType",
    "AllocationItem",
    "AllocateTimeIntent",
    "ReallocateTimeIntent",
    "RemoveAllocationIntent",
    "ShowWeekIntent",
    "ShowIssuesIntent",
    "AddLeaveIntent",
    "RemoveLeaveIntent",
    "AddHolidayIntent",
    "RemoveHolidayIntent",
    "AddOvertimeIntent",
    "RemoveOvertimeIntent",
    "ClarificationRequired",
    "UnionIntent",
]
