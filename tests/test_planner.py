import pytest
from datetime import date, time, timedelta
from decimal import Decimal

from lazytrack.config import LazyTrackConfig
from lazytrack.domain import (
    Allocation,
    AllocationRequest,
    HolidayEntry,
    InvalidDateError,
    LeaveEntry,
    Planner,
    PlannerContext,
    ValidationError,
    WorklogAllocation,
)
from lazytrack.domain.calendar import WorkCalendar
from lazytrack.jira.models import IssueSummary, WorklogEntry


def this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


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
        monday = this_monday()
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
        assert plan.operations[0].started_at is not None
        assert plan.operations[0].started_at.strftime("%H:%M") == "10:00"

    def test_ordered_worklogs_are_scheduled_sequentially(self, planner):
        monday = this_monday()
        request = AllocationRequest(
            start_date=monday,
            end_date=monday,
            allocations=[],
            worklogs=[
                WorklogAllocation(monday, "ABC-123", Decimal("4")),
                WorklogAllocation(monday, "XYZ-456", Decimal("5")),
            ],
        )

        plan = planner.create_plan(request)

        assert [op.issue_key for op in plan.operations] == ["ABC-123", "XYZ-456"]
        assert [op.started_at.strftime("%H:%M") for op in plan.operations] == ["10:00", "14:00"]

    def test_explicit_start_time_is_preserved(self, planner):
        monday = this_monday()
        request = AllocationRequest(
            start_date=monday,
            end_date=monday,
            allocations=[],
            worklogs=[
                WorklogAllocation(monday, "ABC-123", Decimal("2"), time(11, 0))
            ],
        )

        plan = planner.create_plan(request)

        assert plan.operations[0].started_at.strftime("%H:%M") == "11:00"

    def test_entries_after_an_anchored_start_run_sequentially(self, planner):
        monday = this_monday()
        plan = planner.create_plan(
            AllocationRequest(
                start_date=monday,
                end_date=monday,
                allocations=[],
                worklogs=[
                    WorklogAllocation(monday, "ABC-123", Decimal("7"), time(9, 0)),
                    WorklogAllocation(monday, "XYZ-456", Decimal("5")),
                ],
            )
        )

        assert [op.started_at.strftime("%H:%M") for op in plan.operations] == [
            "09:00",
            "16:00",
        ]

    def test_conflicting_start_time_names_the_earlier_entry(self, planner):
        monday = this_monday()
        request = AllocationRequest(
            start_date=monday,
            end_date=monday,
            allocations=[],
            worklogs=[
                WorklogAllocation(monday, "ABC-123", Decimal("7"), time(9, 0)),
                WorklogAllocation(monday, "XYZ-456", Decimal("5"), time(10, 0)),
            ],
        )

        with pytest.raises(ValidationError, match="earlier entry in this request"):
            planner.create_plan(request)

    def test_weekend_allocation_creates_previewable_plan(self, planner):
        saturday = this_monday() + timedelta(days=5)
        request = AllocationRequest(
            start_date=saturday,
            end_date=saturday,
            allocations=[],
            worklogs=[
                WorklogAllocation(saturday, "ABC-123", Decimal("2"), time(9, 0))
            ],
        )

        plan = planner.create_plan(request)

        assert plan.operations[0].seconds == 7200
        assert plan.overtime[0].hours == Decimal("2")
        assert "Overtime to register" in planner.preview_plan(plan)

    def test_full_week_allocation(self, planner):
        monday = this_monday()
        friday = monday + timedelta(days=4)
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
        monday = this_monday()
        request = AllocationRequest(
            start_date=monday,
            end_date=monday + timedelta(days=4),
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        
        plan = planner.create_plan(request)
        
        assert len(plan.operations) == 5


class TestAutomaticOvertime:
    def test_weekend_all_hours_are_overtime(self, planner):
        saturday = this_monday() + timedelta(days=5)
        request = AllocationRequest(
            start_date=saturday,
            end_date=saturday,
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )

        plan = planner.create_plan(request)

        assert sum(op.seconds for op in plan.operations) == 8 * 3600
        assert [(item.date, item.hours) for item in plan.overtime] == [
            (saturday, Decimal("8"))
        ]

    def test_over_capacity_creates_plan_with_derived_overtime(self, planner):
        monday = this_monday()
        request = AllocationRequest(
            start_date=monday,
            end_date=monday,
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("12")),
            ],
        )

        plan = planner.create_plan(request)

        assert sum(op.seconds for op in plan.operations) == 12 * 3600
        assert plan.overtime[0].date == monday
        assert plan.overtime[0].hours == Decimal("4")

    def test_existing_hours_count_toward_derived_overtime(
        self, config, calendar, assigned_issues
    ):
        monday = this_monday()
        existing = WorklogEntry(
            id="existing",
            issue_key="XYZ-456",
            work_date=monday,
            seconds=4 * 3600,
            author_is_current_user=True,
        )
        planner = Planner(
            PlannerContext(calendar, assigned_issues, [existing], []),
            config,
        )
        request = AllocationRequest(
            start_date=monday,
            end_date=monday,
            allocations=[Allocation("ABC-123", Decimal("5"))],
        )

        plan = planner.create_plan(request)

        assert plan.overtime[0].hours == Decimal("1")

    def test_existing_overtime_is_not_double_counted(
        self, config, calendar, assigned_issues
    ):
        monday = this_monday()
        existing = WorklogEntry(
            id="existing",
            issue_key="XYZ-456",
            work_date=monday,
            seconds=10 * 3600,
            author_is_current_user=True,
        )
        planner = Planner(
            PlannerContext(calendar, assigned_issues, [existing], []),
            config,
        )
        plan = planner.create_plan(
            AllocationRequest(
                start_date=monday,
                end_date=monday,
                allocations=[Allocation("ABC-123", Decimal("2"))],
            )
        )

        assert plan.overtime[0].hours == Decimal("2")

    def test_same_day_split_derives_total_overflow(self, planner):
        monday = this_monday()
        plan = planner.create_plan(
            AllocationRequest(
                start_date=monday,
                end_date=monday,
                allocations=[],
                worklogs=[
                    WorklogAllocation(monday, "ABC-123", Decimal("2")),
                    WorklogAllocation(monday, "XYZ-456", Decimal("8")),
                ],
            )
        )

        assert sum(op.seconds for op in plan.operations) == 10 * 3600
        assert plan.overtime[0].hours == Decimal("2")

    def test_mixed_dates_derive_overtime_per_date(self, planner):
        monday = this_monday()
        tuesday = monday + timedelta(days=1)
        saturday = monday + timedelta(days=5)
        plan = planner.create_plan(
            AllocationRequest(
                start_date=monday,
                end_date=saturday,
                allocations=[],
                worklogs=[
                    WorklogAllocation(monday, "ABC-123", Decimal("10")),
                    WorklogAllocation(tuesday, "ABC-123", Decimal("8")),
                    WorklogAllocation(saturday, "ABC-123", Decimal("3")),
                ],
            )
        )

        assert [(item.date, item.hours) for item in plan.overtime] == [
            (monday, Decimal("2")),
            (saturday, Decimal("3")),
        ]

    def test_leave_and_holiday_reduce_normal_capacity(self, planner, calendar):
        monday = this_monday()
        tuesday = monday + timedelta(days=1)
        calendar.add_leave(LeaveEntry(date=monday, hours=Decimal("4")))
        calendar.add_holiday(HolidayEntry(date=tuesday))
        plan = planner.create_plan(
            AllocationRequest(
                start_date=monday,
                end_date=tuesday,
                allocations=[],
                worklogs=[
                    WorklogAllocation(monday, "ABC-123", Decimal("6")),
                    WorklogAllocation(tuesday, "ABC-123", Decimal("3")),
                ],
            )
        )

        assert [(item.date, item.hours) for item in plan.overtime] == [
            (monday, Decimal("2")),
            (tuesday, Decimal("3")),
        ]


class TestInvalidDates:
    def test_invalid_date_order(self, planner):
        monday = this_monday()
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
        monday = this_monday()
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
        monday = this_monday()
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
        monday = this_monday()
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


class TestWriteWindow:
    def test_year_range_rejected(self, planner):
        request = AllocationRequest(
            start_date=date(2025, 1, 1),
            end_date=date(2026, 9, 6),
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        with pytest.raises(InvalidDateError):
            planner.create_plan(request)

    def test_date_before_lookback_rejected(self, planner):
        request = AllocationRequest(
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 6),
            allocations=[
                Allocation(issue_key="ABC-123", hours_per_day=Decimal("8")),
            ],
        )
        with pytest.raises(InvalidDateError):
            planner.create_plan(request)

