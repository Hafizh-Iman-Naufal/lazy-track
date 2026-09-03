import pytest
from datetime import date, timedelta
from decimal import Decimal

from lazytrack.config import LazyTrackConfig
from lazytrack.domain import (
    Allocation,
    AllocationRequest,
    InvalidDateError,
    OverCapacityError,
    Planner,
    PlannerContext,
    WeekendAllocationError,
)
from lazytrack.domain.calendar import WorkCalendar
from lazytrack.jira.models import IssueSummary, WorklogEntry


@pytest.fixture
def config():
    return LazyTrackConfig()


@pytest.fixture
def calendar(config):
    return WorkCalendar(config=config.work)


@pytest.fixture
def assigned_issues():
    return [
        IssueSummary(
            key="ABC-123",
            summary="Test story",
            issue_type="Story",
            status="In Progress",
            project_key="ABC",
            assignee_is_current_user=True,
        ),
        IssueSummary(
            key="XYZ-456",
            summary="Another story",
            issue_type="Story",
            status="To Do",
            project_key="XYZ",
            assignee_is_current_user=True,
        ),
    ]


@pytest.fixture
def empty_worklogs():
    return []


@pytest.fixture
def planner_context(calendar, assigned_issues, empty_worklogs):
    return PlannerContext(
        calendar=calendar,
        assigned_issues=assigned_issues,
        user_worklogs=empty_worklogs,
        managed_worklogs=empty_worklogs,
    )


@pytest.fixture
def planner(planner_context, config):
    return Planner(planner_context, config)


class TestSimpleAllocation:
    def test_one_day_allocation(self, planner, assigned_issues):
        monday = date(2026, 8, 31)
        request = AllocationRequest(
            start_date=monday,
            end_date=monday,
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        plan = planner.create_plan(request)
        
        assert plan.status == "validated"
        assert len(plan.operations) == 1
        assert plan.operations[0].issue_key == "ABC-123"
        assert plan.operations[0].seconds == 28800

    def test_full_week_allocation(self, planner):
        monday = date(2026, 8, 31)
        friday = date(2026, 9, 4)
        request = AllocationRequest(
            start_date=monday,
            end_date=friday,
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        plan = planner.create_plan(request)
        
        assert len(plan.operations) == 5
        total_hours = sum(op.seconds for op in plan.operations) / 3600
        assert total_hours == 40

    def test_split_week_across_issues(self, planner):
        monday = date(2026, 8, 31)
        request = AllocationRequest(
            start_date=monday,
            end_date=monday + timedelta(days=4),
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        plan = planner.create_plan(request)
        
        assert len(plan.operations) == 5


class TestWeekendRejection:
    def test_weekend_rejected(self, planner):
        saturday = date(2026, 9, 5)
        request = AllocationRequest(
            start_date=saturday,
            end_date=saturday,
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        with pytest.raises(WeekendAllocationError):
            planner.create_plan(request)


class TestOverCapacityRejection:
    def test_over_capacity_rejected(self, planner, calendar):
        monday = date(2026, 8, 31)
        request = AllocationRequest(
            start_date=monday,
            end_date=monday,
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("10")),
            ],
        )
        
        with pytest.raises(OverCapacityError):
            planner.create_plan(request)


class TestInvalidDates:
    def test_invalid_date_order(self, planner):
        monday = date(2026, 8, 31)
        request = AllocationRequest(
            start_date=monday + timedelta(days=1),
            end_date=monday,
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        with pytest.raises(InvalidDateError):
            planner.create_plan(request)


class TestLeaveWeek:
    def test_leave_week_reduces_target(self, planner_context, config):
        monday = date(2026, 8, 31)
        tuesday = monday + timedelta(days=1)
        
        planner_context.calendar.add_leave(
            type("LeaveEntry", (), {"date": monday, "hours": Decimal("8")})()
        )
        
        planner = Planner(planner_context, config)
        request = AllocationRequest(
            start_date=tuesday,
            end_date=tuesday + timedelta(days=3),
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        plan = planner.create_plan(request)
        assert len(plan.operations) == 4
        total_hours = sum(op.seconds for op in plan.operations) / 3600
        assert total_hours == Decimal("32")


class TestWeeklyTotal:
    def test_weekly_total_calculation(self, planner):
        monday = date(2026, 8, 31)
        request = AllocationRequest(
            start_date=monday,
            end_date=monday + timedelta(days=4),
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        plan = planner.create_plan(request)
        
        total_seconds = sum(op.seconds for op in plan.operations)
        total_hours = Decimal(str(total_seconds)) / 3600
        assert total_hours == Decimal("40")


class TestPreview:
    def test_preview_format(self, planner):
        monday = date(2026, 8, 31)
        request = AllocationRequest(
            start_date=monday,
            end_date=monday,
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        plan = planner.create_plan(request)
        preview = planner.preview_plan(plan)
        
        assert plan.id in preview
        assert "ABC-123" in preview
        assert "8h" in preview
        assert "No Jira changes" in preview
