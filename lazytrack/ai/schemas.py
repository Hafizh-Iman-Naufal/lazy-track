from __future__ import annotations

from datetime import date, timedelta
from enum import Enum
from typing import Optional, Union, Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class IntentType(str, Enum):
    ALLOCATE_TIME = "allocate_time"
    REALLOCATE_TIME = "reallocate_time"
    REMOVE_ALLOCATION = "remove_allocation"
    SHOW_WEEK = "show_week"
    SHOW_ISSUES = "show_issues"
    VIEW_ASSIGNED_ISSUES = "view_assigned_issues"
    ADD_LEAVE = "add_leave"
    REMOVE_LEAVE = "remove_leave"
    ADD_HOLIDAY = "add_holiday"
    REMOVE_HOLIDAY = "remove_holiday"
    CLARIFICATION_REQUIRED = "clarification_required"


class AllocationItem(BaseModel):
    issue_key: str
    hours_per_day: float = Field(gt=0, le=24)


class WorklogItem(BaseModel):
    issue_key: str
    date: date
    hours: float = Field(gt=0, le=24)
    start_time: Optional[str] = None


class TimeGap(BaseModel):
    start: str
    end: str


class AllocateTimeIntent(BaseModel):
    type: Literal["allocate_time"] = "allocate_time"
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    allocations: list[AllocationItem] = Field(default_factory=list)
    worklogs: list[WorklogItem] = Field(default_factory=list)
    gaps: list[TimeGap] = Field(default_factory=list)

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v, info):
        if v is not None and info.data.get("start_date") is not None and v < info.data["start_date"]:
            raise ValueError("end_date must be after start_date")
        return v

    @model_validator(mode="after")
    def has_dates_or_worklogs(self):
        if not self.worklogs and (self.start_date is None or self.end_date is None):
            raise ValueError("allocate_time requires dates or worklogs")
        return self


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
    weeks: list[str] = Field(default_factory=list)


class ShowIssuesIntent(BaseModel):
    type: Literal["show_issues"] = "show_issues"


class AddLeaveIntent(BaseModel):
    type: Literal["add_leave"] = "add_leave"
    dates: list[date] = Field(default_factory=list)
    end_date: Optional[date] = None
    hours: Optional[float] = Field(default=None, ge=0, le=24)

    @model_validator(mode="before")
    @classmethod
    def hoist_single_date(cls, values):
        if not isinstance(values, dict):
            return values
        data = dict(values)
        days = list(data.get("dates") or [])
        single = data.get("date")
        if not days and single is not None:
            days = [single]
        data["dates"] = days
        return data

    @model_validator(mode="after")
    def resolve_leave_dates(self):
        days = list(self.dates)
        if len(days) == 1 and self.end_date is not None:
            start = days[0]
            if self.end_date < start:
                raise ValueError("end_date must be after start date")
            days = []
            d = start
            while d <= self.end_date:
                days.append(d)
                d += timedelta(days=1)
        if not days:
            raise ValueError("add_leave requires date or dates")
        self.dates = days
        return self


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


class ClarificationRequired(BaseModel):
    type: Literal["clarification_required"] = "clarification_required"
    reason: str = ""
    missing_fields: list[str] = Field(default_factory=list)


class IntentSchema(BaseModel):
    type: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    allocations: Optional[list[AllocationItem]] = None
    worklogs: Optional[list[WorklogItem]] = None
    total_hours: Optional[float] = None
    gaps: Optional[list[TimeGap]] = None
    date: Optional[date] = None
    from_issue_key: Optional[str] = None
    to_issue_key: Optional[str] = None
    issue_key: Optional[str] = None
    hours: Optional[float] = None
    hours_per_day: Optional[float] = None
    week: Optional[str] = None
    weeks: Optional[list[str]] = None
    dates: Optional[list[date]] = None
    description: Optional[str] = None
    reason: Optional[str] = None
    missing_fields: Optional[list[str]] = None

    @model_validator(mode="wrap")
    @classmethod
    def parse_discriminated_union(cls, values, info):
        if not isinstance(values, dict):
            return values
        raw_type = values.get("type", "")
        intent_type = raw_type.lower().replace(" ", "_")
        
        normalized_values = {**values}
        
        if intent_type in ("allocate_time", "allocatetimeintent", "allocate"):
            normalized_values["type"] = "allocate_time"
            return AllocateTimeIntent(**normalized_values)
        elif intent_type in ("reallocate_time", "reallocatetimeintent"):
            normalized_values["type"] = "reallocate_time"
            return ReallocateTimeIntent(**normalized_values)
        elif intent_type in ("remove_allocation", "removeallocationintent"):
            normalized_values["type"] = "remove_allocation"
            return RemoveAllocationIntent(**normalized_values)
        elif intent_type in ("show_week", "showweekintent"):
            normalized_values["type"] = "show_week"
            return ShowWeekIntent(**normalized_values)
        elif intent_type in ("show_issues", "showissuesintent", "view_assigned_issues", "viewassignedissues"):
            normalized_values["type"] = "show_issues"
            return ShowIssuesIntent(**normalized_values)
        elif intent_type in ("add_leave", "addleaveintent"):
            normalized_values["type"] = "add_leave"
            return AddLeaveIntent(**normalized_values)
        elif intent_type in ("remove_leave", "removeleveintent"):
            normalized_values["type"] = "remove_leave"
            return RemoveLeaveIntent(**normalized_values)
        elif intent_type in ("add_holiday", "addholidayintent"):
            normalized_values["type"] = "add_holiday"
            return AddHolidayIntent(**normalized_values)
        elif intent_type in ("remove_holiday", "removeholidayintent"):
            normalized_values["type"] = "remove_holiday"
            return RemoveHolidayIntent(**normalized_values)
        elif intent_type in ("clarification_required", "clarificationrequired"):
            normalized_values["type"] = "clarification_required"
            if "reason" not in normalized_values or not normalized_values["reason"]:
                normalized_values["reason"] = "Request requires clarification"
            if "missing_fields" not in normalized_values:
                normalized_values["missing_fields"] = []
            return ClarificationRequired(**normalized_values)
        else:
            raise ValueError(f"Unknown intent type: '{raw_type}' (normalized: '{intent_type}')")


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
        ClarificationRequired,
    ],
    Field(discriminator="type"),
]

__all__ = [
    "IntentType",
    "AllocationItem",
    "WorklogItem",
    "TimeGap",
    "AllocateTimeIntent",
    "ReallocateTimeIntent",
    "RemoveAllocationIntent",
    "ShowWeekIntent",
    "ShowIssuesIntent",
    "AddLeaveIntent",
    "RemoveLeaveIntent",
    "AddHolidayIntent",
    "RemoveHolidayIntent",
    "ClarificationRequired",
    "IntentSchema",
    "UnionIntent",
]
