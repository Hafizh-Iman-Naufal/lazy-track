import pytest
from datetime import date
from pydantic import ValidationError

from lazytrack.ai.schemas import (
    IntentType,
    AllocationItem,
    AllocateTimeIntent,
    ReallocateTimeIntent,
    ShowWeekIntent,
    ShowIssuesIntent,
    AddLeaveIntent,
    RemoveLeaveIntent,
    AddHolidayIntent,
    AddOvertimeIntent,
    ClarificationRequired,
)


class TestAllocationItem:
    def test_valid_allocation_item(self):
        item = AllocationItem(issue_key="ABC-123", hours_per_day=8.0)
        assert item.issue_key == "ABC-123"
        assert item.hours_per_day == 8.0

    def test_invalid_hours_zero(self):
        with pytest.raises(ValidationError):
            AllocationItem(issue_key="ABC-123", hours_per_day=0)

    def test_invalid_hours_negative(self):
        with pytest.raises(ValidationError):
            AllocationItem(issue_key="ABC-123", hours_per_day=-1)


class TestAllocateTimeIntent:
    def test_valid_intent(self):
        intent = AllocateTimeIntent(
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 5),
            allocations=[
                AllocationItem(issue_key="ABC-123", hours_per_day=8.0)
            ],
        )
        assert intent.type == "allocate_time"
        assert intent.start_date == date(2026, 9, 1)
        assert len(intent.allocations) == 1

    def test_end_before_start_invalid(self):
        with pytest.raises(ValidationError):
            AllocateTimeIntent(
                start_date=date(2026, 9, 5),
                end_date=date(2026, 9, 1),
                allocations=[],
            )


class TestShowWeekIntent:
    def test_show_week_with_date(self):
        intent = ShowWeekIntent(week="2026-W36")
        assert intent.type == "show_week"
        assert intent.week == "2026-W36"

    def test_show_week_no_date(self):
        intent = ShowWeekIntent()
        assert intent.week is None


class TestShowIssuesIntent:
    def test_show_issues(self):
        intent = ShowIssuesIntent()
        assert intent.type == "show_issues"


class TestAddLeaveIntent:
    def test_add_leave_full_day(self):
        intent = AddLeaveIntent(date=date(2026, 9, 10))
        assert intent.type == "add_leave"
        assert intent.hours is None

    def test_add_leave_partial(self):
        intent = AddLeaveIntent(date=date(2026, 9, 10), hours=4.0)
        assert intent.hours == 4.0

    def test_add_leave_invalid_hours(self):
        with pytest.raises(ValidationError):
            AddLeaveIntent(date=date(2026, 9, 10), hours=25)


class TestAddOvertimeIntent:
    def test_valid_overtime(self):
        intent = AddOvertimeIntent(date=date(2026, 9, 4), hours=2.0)
        assert intent.hours == 2.0

    def test_invalid_overtime(self):
        with pytest.raises(ValidationError):
            AddOvertimeIntent(date=date(2026, 9, 4), hours=0)


class TestClarificationRequired:
    def test_clarification_required(self):
        intent = ClarificationRequired(
            reason="Ambiguous issue reference",
            missing_fields=["issue_key"],
        )
        assert intent.type == "clarification_required"
        assert "Ambiguous" in intent.reason
