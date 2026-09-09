from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional

from lazytrack.config import LazyTrackConfig
from lazytrack.domain.calendar import WorkCalendar, create_calendar
from lazytrack.domain.tz import now_in_zone, zone_for, parse_hhmm
from lazytrack.domain import OvertimeEntry
from lazytrack.domain.window import plan_span_ok, write_date_ok
from lazytrack.jira.models import IssueSummary, WorklogEntry


class OperationType(str, Enum):
    CREATE_WORKLOG = "CREATE_WORKLOG"
    UPDATE_WORKLOG = "UPDATE_WORKLOG"
    MOVE_WORKLOG = "MOVE_WORKLOG"
    DELETE_WORKLOG = "DELETE_WORKLOG"


class OperationStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Allocation:
    issue_key: str
    hours_per_day: Decimal


@dataclass
class WorklogAllocation:
    work_date: date
    issue_key: str
    hours: Decimal
    start_time: Optional[time] = None


@dataclass
class AllocationRequest:
    start_date: date
    end_date: date
    allocations: list[Allocation]
    gaps: list[tuple[time, time]] = field(default_factory=list)
    timezone: str = "UTC"
    worklogs: list[WorklogAllocation] = field(default_factory=list)


@dataclass
class PlanOperation:
    id: str
    plan_id: str
    operation_type: OperationType
    issue_key: str
    work_date: date
    seconds: int
    existing_worklog_id: Optional[str] = None
    status: OperationStatus = OperationStatus.PENDING
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None


@dataclass
class Plan:
    id: str
    created_at: date
    operations: list[PlanOperation] = field(default_factory=list)
    status: str = "draft"
    summary: str = ""
    overtime: list[OvertimeEntry] = field(default_factory=list)


class ValidationError(Exception):
    pass


class InvalidDateError(ValidationError):
    pass


class WeekendAllocationError(ValidationError):
    pass


class UnassignedIssueError(ValidationError):
    pass


class AmbiguousIssueError(ValidationError):
    pass


class DuplicateOperationError(ValidationError):
    pass


class ManualWorklogModificationError(ValidationError):
    pass


@dataclass
class PlannerContext:
    calendar: WorkCalendar
    assigned_issues: list[IssueSummary]
    user_worklogs: list[WorklogEntry]
    managed_worklogs: list[WorklogEntry]
    assigned_only: bool = True


def generate_plan_id() -> str:
    from datetime import datetime
    return f"PL-{datetime.now().strftime('%Y%m%d%H%M%S')}"


def generate_operation_id() -> str:
    import uuid
    return uuid.uuid4().hex


def validate_dates(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise InvalidDateError(f"Start date {start_date} is after end date {end_date}")


def validate_not_weekend(
    d: date, calendar: WorkCalendar, proposed_overtime: Decimal = Decimal("0")
) -> None:
    if (
        not calendar.is_working_day(d)
        and calendar.max_hours_for_date(d) + proposed_overtime <= 0
    ):
        raise WeekendAllocationError(f"Cannot allocate to weekend: {d}")


def validate_issue_assigned(
    issue_key: str,
    issues: list[IssueSummary],
    assigned_only: bool,
) -> None:
    if not assigned_only:
        return
    
    matching = [i for i in issues if i.key == issue_key]
    if not matching:
        raise UnassignedIssueError(
            f"Issue {issue_key} is not assigned to you. "
            "Only assigned issues can receive worklogs."
        )


def check_duplicate_operation(
    plan: Plan,
    issue_key: str,
    work_date: date,
    operation_type: OperationType,
    started_at: Optional[datetime] = None,
) -> bool:
    for op in plan.operations:
        if (
            op.issue_key == issue_key
            and op.work_date == work_date
            and op.operation_type == operation_type
            and op.status == OperationStatus.PENDING
            and op.started_at == started_at
        ):
            return True
    return False


def work_segments(
    work_date: date,
    hours: Decimal,
    gaps: list[tuple[time, time]],
    tz_name: str,
    day_start: time = time(10, 0),
    start_time: Optional[time] = None,
) -> list[tuple[datetime, Decimal]]:
    if any(gap_end <= gap_start for gap_start, gap_end in gaps):
        raise ValidationError("Each gap end must be after its start")
    remaining = hours
    zone = zone_for(tz_name)
    cursor = datetime.combine(work_date, start_time or day_start, tzinfo=zone)
    segments: list[tuple[datetime, Decimal]] = []
    for gap_start, gap_end in sorted(gaps, key=lambda g: g[0]):
        gap_start_dt = datetime.combine(work_date, gap_start, tzinfo=zone)
        gap_end_dt = datetime.combine(work_date, gap_end, tzinfo=zone)
        if gap_start_dt > cursor and remaining > 0:
            avail = Decimal(str((gap_start_dt - cursor).total_seconds() / 3600))
            take = min(remaining, avail)
            if take > 0:
                segments.append((cursor, take))
                remaining -= take
        if gap_end_dt > cursor:
            cursor = gap_end_dt
    if remaining > 0:
        segments.append((cursor, remaining))
    return segments


class Planner:
    def __init__(self, context: PlannerContext, config: LazyTrackConfig):
        self.context = context
        self.config = config

    def create_plan(
        self,
        request: AllocationRequest,
    ) -> Plan:
        validate_dates(request.start_date, request.end_date)
        self._validate_write_window(request.start_date, request.end_date)
        
        plan = Plan(
            id=generate_plan_id(),
            created_at=now_in_zone(self.config.work.timezone).date(),
            summary=f"Allocation plan for {request.start_date} to {request.end_date}",
        )

        if request.worklogs:
            work = list(request.worklogs)
        else:
            work = []
            current_date = request.start_date
            while current_date <= request.end_date:
                for allocation in request.allocations:
                    work.append(
                        WorklogAllocation(
                            work_date=current_date,
                            issue_key=allocation.issue_key,
                            hours=allocation.hours_per_day,
                        )
                    )
                current_date += timedelta(days=1)

        cursors: dict[date, time] = {}
        for allocation in work:
            current_date = allocation.work_date
            validate_issue_assigned(
                allocation.issue_key,
                self.context.assigned_issues,
                self.config.jira.assigned_only,
            )

            hours_to_add = Decimal(str(allocation.hours))
            tz_name = request.timezone or self.config.work.timezone
            day_start = parse_hhmm(self.config.work.day_start)
            if (
                allocation.start_time is not None
                and current_date in cursors
                and allocation.start_time < cursors[current_date]
            ):
                raise ValidationError(
                    f"The start time for {allocation.issue_key} overlaps an earlier "
                    f"entry in this request, which runs until "
                    f"{cursors[current_date].strftime('%H:%M')}"
                )
            start_time = allocation.start_time or cursors.get(current_date) or day_start
            segments = work_segments(
                current_date,
                hours_to_add,
                request.gaps,
                tz_name,
                day_start=day_start,
                start_time=start_time,
            )

            for started_at, segment_hours in segments:
                if check_duplicate_operation(
                    plan,
                    allocation.issue_key,
                    current_date,
                    OperationType.CREATE_WORKLOG,
                    started_at,
                ):
                    continue

                op = PlanOperation(
                    id=generate_operation_id(),
                    plan_id=plan.id,
                    operation_type=OperationType.CREATE_WORKLOG,
                    issue_key=allocation.issue_key,
                    work_date=current_date,
                    seconds=int(segment_hours * 3600),
                    started_at=started_at,
                )
                plan.operations.append(op)
            last_start, last_hours = segments[-1]
            allocation_end = last_start + timedelta(hours=float(last_hours))
            if allocation_end.date() != current_date:
                raise ValidationError(
                    f"The worklog for {allocation.issue_key} would run past midnight"
                )
            cursors[current_date] = allocation_end.timetz().replace(tzinfo=None)
        
        plan.overtime = self._derive_overtime(plan)
        plan.status = "validated"
        return plan

    def _derive_overtime(self, plan: Plan) -> list[OvertimeEntry]:
        planned_by_date: dict[date, Decimal] = {}
        for op in plan.operations:
            planned_by_date[op.work_date] = (
                planned_by_date.get(op.work_date, Decimal("0"))
                + Decimal(op.seconds) / 3600
            )

        overtime = []
        for work_date, planned_hours in sorted(planned_by_date.items()):
            if not self.context.calendar.is_working_day(work_date):
                overtime_hours = planned_hours
            else:
                normal_hours = self.context.calendar.capacity_for_date(work_date)
                existing_hours = self._get_existing_hours_for_date(work_date)
                before = max(Decimal("0"), existing_hours - normal_hours)
                after = max(
                    Decimal("0"),
                    existing_hours + planned_hours - normal_hours,
                )
                overtime_hours = after - before
            if overtime_hours > 0:
                overtime.append(
                    OvertimeEntry(date=work_date, hours=overtime_hours)
                )
        return overtime

    def _get_existing_hours_for_date(self, d: date) -> Decimal:
        total = Decimal("0")
        seen = set()
        for wl in self.context.user_worklogs + self.context.managed_worklogs:
            if wl.work_date == d and wl.id not in seen:
                total += Decimal(str(wl.seconds)) / 3600
                seen.add(wl.id)
        return total

    def reallocate(
        self,
        from_issue_key: str,
        to_issue_key: str,
        work_date: date,
        hours: Decimal,
    ) -> Plan:
        self._validate_write_window(work_date, work_date)
        validate_not_weekend(work_date, self.context.calendar)
        validate_issue_assigned(to_issue_key, self.context.assigned_issues, self.config.jira.assigned_only)
        
        existing = self._find_existing_worklog(from_issue_key, work_date)
        
        plan = Plan(
            id=generate_plan_id(),
            created_at=now_in_zone(self.config.work.timezone).date(),
            summary=f"Reallocation from {from_issue_key} to {to_issue_key} on {work_date}",
        )
        
        if existing and existing.managed_by_lazytrack:
            plan.operations.append(PlanOperation(
                id=generate_operation_id(),
                plan_id=plan.id,
                operation_type=OperationType.MOVE_WORKLOG,
                issue_key=to_issue_key,
                work_date=work_date,
                seconds=int(hours * 3600),
                existing_worklog_id=existing.id,
            ))
        else:
            raise ManualWorklogModificationError(
                f"Cannot reallocate: worklog on {from_issue_key} is not managed by LazyTrack"
            )
        
        plan.status = "validated"
        return plan

    def _find_existing_worklog(self, issue_key: str, work_date: date) -> Optional[WorklogEntry]:
        for wl in self.context.user_worklogs:
            if wl.issue_key == issue_key and wl.work_date == work_date:
                return wl
        return None

    def preview_plan(self, plan: Plan) -> str:
        lines = [
            f"Plan: {plan.id}",
            "",
            "DATE         ISSUE      START-END     HOURS  TIMEZONE",
            "-" * 68,
        ]
        
        total_seconds = 0
        for op in sorted(plan.operations, key=lambda x: x.work_date):
            if op.status == OperationStatus.PENDING:
                hours = Decimal(str(op.seconds)) / 3600
                schedule = "-"
                timezone = self.config.work.timezone
                if op.started_at:
                    end = op.started_at + timedelta(seconds=op.seconds)
                    schedule = f"{op.started_at:%H:%M}-{end:%H:%M}"
                    timezone = getattr(op.started_at.tzinfo, "key", None) or timezone
                lines.append(
                    f"{op.work_date}  {op.issue_key:<10} {schedule:<13} {hours}h    {timezone}"
                )
                total_seconds += op.seconds
        
        total_hours = Decimal(str(total_seconds)) / 3600
        lines.append("-" * 68)
        lines.append(f"Total: {total_hours}h")
        for overtime in plan.overtime:
            lines.append(
                f"Overtime to register: {overtime.date}  {overtime.hours}h"
            )
        lines.append("")
        lines.append("No Jira changes have been made. Reply yes to apply, no to cancel,")
        lines.append("or describe a correction.")
        
        return "\n".join(lines)

    def _validate_write_window(self, start_date: date, end_date: date) -> None:
        safety = self.config.safety
        today = now_in_zone(self.config.work.timezone).date()
        if not plan_span_ok(start_date, end_date, safety.max_plan_span_days):
            raise InvalidDateError(
                f"Plan span {start_date} to {end_date} exceeds "
                f"{safety.max_plan_span_days} days"
            )
        for d, label in ((start_date, "start"), (end_date, "end")):
            if not write_date_ok(
                d, today, safety.worklog_lookback_weeks, safety.max_plan_span_days
            ):
                raise InvalidDateError(
                    f"Plan {label} date {d} is outside the allowed worklog window"
                )

