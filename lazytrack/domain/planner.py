from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional

from lazytrack.config import LazyTrackConfig
from lazytrack.domain.calendar import WorkCalendar, create_calendar
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
class AllocationRequest:
    start_date: date
    end_date: date
    allocations: list[Allocation]


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


@dataclass
class Plan:
    id: str
    created_at: date
    operations: list[PlanOperation] = field(default_factory=list)
    status: str = "draft"
    summary: str = ""


class ValidationError(Exception):
    pass


class InvalidDateError(ValidationError):
    pass


class WeekendAllocationError(ValidationError):
    pass


class OverCapacityError(ValidationError):
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
    return str(uuid.uuid4())[:8]


def validate_dates(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise InvalidDateError(f"Start date {start_date} is after end date {end_date}")
    
    try:
        date(2099, 12, 31)
    except ValueError:
        raise InvalidDateError("Invalid date range")


def validate_not_weekend(d: date, calendar: WorkCalendar) -> None:
    if not calendar.is_working_day(d):
        raise WeekendAllocationError(f"Cannot allocate to weekend: {d}")


def validate_capacity(
    requested_hours: Decimal,
    date: date,
    calendar: WorkCalendar,
    existing_hours: Decimal = Decimal("0"),
) -> None:
    max_hours = calendar.max_hours_for_date(date)
    required_hours = calendar.capacity_for_date(date)
    available = required_hours - existing_hours
    
    if requested_hours > available + Decimal("0.01"):
        raise OverCapacityError(
            f"Requested {requested_hours}h on {date}, but only {available}h available "
            f"(max with overtime: {max_hours}h)"
        )


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
) -> bool:
    for op in plan.operations:
        if (
            op.issue_key == issue_key
            and op.work_date == work_date
            and op.operation_type == operation_type
            and op.status == OperationStatus.PENDING
        ):
            return True
    return False


class Planner:
    def __init__(self, context: PlannerContext, config: LazyTrackConfig):
        self.context = context
        self.config = config

    def create_plan(
        self,
        request: AllocationRequest,
    ) -> Plan:
        validate_dates(request.start_date, request.end_date)
        
        plan = Plan(
            id=generate_plan_id(),
            created_at=date.today(),
            summary=f"Allocation plan for {request.start_date} to {request.end_date}",
        )
        
        current_date = request.start_date
        while current_date <= request.end_date:
            validate_not_weekend(current_date, self.context.calendar)
            
            for allocation in request.allocations:
                validate_issue_assigned(
                    allocation.issue_key,
                    self.context.assigned_issues,
                    self.config.jira.assigned_only,
                )
                
                existing_hours = self._get_existing_hours_for_date(
                    current_date, allocation.issue_key
                )
                
                hours_to_add = Decimal(str(allocation.hours_per_day))
                validate_capacity(
                    hours_to_add,
                    current_date,
                    self.context.calendar,
                    existing_hours,
                )
                
                if check_duplicate_operation(
                    plan, allocation.issue_key, current_date, OperationType.CREATE_WORKLOG
                ):
                    continue
                
                op = PlanOperation(
                    id=generate_operation_id(),
                    plan_id=plan.id,
                    operation_type=OperationType.CREATE_WORKLOG,
                    issue_key=allocation.issue_key,
                    work_date=current_date,
                    seconds=int(hours_to_add * 3600),
                )
                plan.operations.append(op)
            
            current_date += timedelta(days=1)
        
        plan.status = "validated"
        return plan

    def _get_existing_hours_for_date(self, d: date, issue_key: str) -> Decimal:
        total = Decimal("0")
        for wl in self.context.user_worklogs:
            if wl.work_date == d and wl.issue_key == issue_key:
                total += Decimal(str(wl.seconds)) / 3600
        for wl in self.context.managed_worklogs:
            if wl.work_date == d and wl.issue_key == issue_key:
                total += Decimal(str(wl.seconds)) / 3600
        return total

    def reallocate(
        self,
        from_issue_key: str,
        to_issue_key: str,
        work_date: date,
        hours: Decimal,
    ) -> Plan:
        validate_not_weekend(work_date, self.context.calendar)
        validate_issue_assigned(to_issue_key, self.context.assigned_issues, self.config.jira.assigned_only)
        
        existing = self._find_existing_worklog(from_issue_key, work_date)
        
        plan = Plan(
            id=generate_plan_id(),
            created_at=date.today(),
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
            "DATE         ISSUE      HOURS",
            "-" * 40,
        ]
        
        total_seconds = 0
        for op in sorted(plan.operations, key=lambda x: (x.work_date, x.issue_key)):
            if op.status == OperationStatus.PENDING:
                hours = Decimal(str(op.seconds)) / 3600
                lines.append(f"{op.work_date}  {op.issue_key:<10}  {hours}h")
                total_seconds += op.seconds
        
        total_hours = Decimal(str(total_seconds)) / 3600
        lines.append("-" * 40)
        lines.append(f"Total: {total_hours}h")
        lines.append("")
        lines.append("No Jira changes have been made.")
        
        return "\n".join(lines)
