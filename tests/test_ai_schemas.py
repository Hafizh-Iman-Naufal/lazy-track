import pytest
from datetime import date
from pydantic import ValidationError

from lazytrack.ai.schemas import (
    IntentType,
    IntentSchema,
    AllocationItem,
    AllocateTimeIntent,
    ReallocateTimeIntent,
    ShowWeekIntent,
    ShowIssuesIntent,
    AddLeaveIntent,
    RemoveLeaveIntent,
    AddHolidayIntent,
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


class TestClarificationRequired:
    def test_clarification_required(self):
        intent = ClarificationRequired(
            reason="Ambiguous issue reference",
            missing_fields=["issue_key"],
        )
        assert intent.type == "clarification_required"
        assert "Ambiguous" in intent.reason


class TestIntentSchemaCaseInsensitive:
    """Test that IntentSchema handles AI responses with various type formats."""
    
    def test_clarification_required_pascal_case(self):
        """AI returns ClarificationRequired (PascalCase) - should parse correctly."""
        data = {"type": "ClarificationRequired", "reason": "ambiguous request"}
        intent = IntentSchema.model_validate(data)
        assert isinstance(intent, ClarificationRequired)
        assert intent.type == "clarification_required"
    
    def test_clarification_required_snake_case(self):
        """AI returns clarification_required (snake_case)."""
        data = {"type": "clarification_required", "reason": "ambiguous request"}
        intent = IntentSchema.model_validate(data)
        assert isinstance(intent, ClarificationRequired)
    
    def test_show_issues_snake_case(self):
        """AI returns show_issues (snake_case)."""
        data = {"type": "show_issues"}
        intent = IntentSchema.model_validate(data)
        assert isinstance(intent, ShowIssuesIntent)

    def test_view_assigned_issues_alias(self):
        """AI returns view_assigned_issues - should map to ShowIssuesIntent."""
        data = {"type": "view_assigned_issues"}
        intent = IntentSchema.model_validate(data)
        assert isinstance(intent, ShowIssuesIntent)
        assert intent.type == "show_issues"

    def test_clarification_required_no_reason_uses_default(self):
        """ClarificationRequired without reason field should not raise."""
        data = {"type": "clarification_required"}
        intent = IntentSchema.model_validate(data)
        assert isinstance(intent, ClarificationRequired)
        assert intent.reason == "Request requires clarification"
    
    def test_allocate_time_variations(self):
        """Test allocate_time variations (snake_case and PascalCase with Intent suffix)."""
        for type_val in ["allocate_time", "AllocateTimeIntent", "allocate"]:
            data = {"type": type_val, "start_date": "2026-09-01", "end_date": "2026-09-05", "allocations": []}
            intent = IntentSchema.model_validate(data)
            assert isinstance(intent, AllocateTimeIntent)
    
    def test_invalid_type_raises_error(self):
        """Invalid type should raise ValueError."""
        data = {"type": "invalid_type"}
        with pytest.raises(ValidationError) as exc_info:
            IntentSchema.model_validate(data)
        assert "Unknown intent type" in str(exc_info.value)
    
    def test_clarification_required_missing_reason_gets_default(self):
        """AI returns ClarificationRequired without reason - should get default."""
        data = {"type": "ClarificationRequired"}
        intent = IntentSchema.model_validate(data)
        assert isinstance(intent, ClarificationRequired)
        assert intent.reason == "Request requires clarification"
        assert intent.missing_fields == []
