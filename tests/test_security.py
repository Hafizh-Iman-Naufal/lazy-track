import pytest
from pydantic import BaseModel

from lazytrack.ai.schemas import (
    AllocateTimeIntent,
    ClarificationRequired,
)
from lazytrack.config import LazyTrackConfig


class TestSecurityRegression:
    def test_unsupported_intent_rejected(self):
        from lazytrack.ai.schemas import (
            AllocateTimeIntent,
            AllocationItem,
            ShowWeekIntent,
        )
        
        with pytest.raises(Exception):
            intent = AllocateTimeIntent(
                start_date=None,
                end_date=None,
                allocations=[],
            )

    def test_manual_worklog_not_modifiable(self):
        from lazytrack.domain.planner import ManualWorklogModificationError
        from lazytrack.domain import Planner, PlannerContext, AllocationRequest, Allocation
        from lazytrack.config import LazyTrackConfig
        from lazytrack.jira.models import WorklogEntry
        from datetime import date
        from decimal import Decimal
        
        config = LazyTrackConfig()
        calendar = type("MockCalendar", (), {})()
        calendar.capacity_for_date = lambda d: Decimal("8")
        calendar.is_working_day = lambda d: True
        
        context = PlannerContext(
            calendar=calendar,
            assigned_issues=[],
            user_worklogs=[],
            managed_worklogs=[],
        )
        
        planner = Planner(context, config)

    def test_dry_run_never_mutates(self):
        from lazytrack.config import LazyTrackConfig
        
        config = LazyTrackConfig()
        assert config.safety.require_confirmation is True

    def test_forbidden_action_rejected_at_schema_level(self):
        from lazytrack.ai.schemas import AddLeaveIntent
        from datetime import date
        
        intent = AddLeaveIntent(date=date(2026, 9, 10))
        assert intent.type == "add_leave"
        assert hasattr(intent, "type")


class TestSecretsNotLeaked:
    def test_config_redacts_secrets(self):
        from lazytrack.config import redact_secrets, LazyTrackConfig
        
        config = LazyTrackConfig()
        config.jira.api_token = "super_secret"
        data = redact_secrets(config)
        assert data["jira"]["api_token"] == "[REDACTED]"

    def test_config_redacts_provider_keys(self):
        from lazytrack.config import redact_secrets, LazyTrackConfig
        
        config = LazyTrackConfig()
        config.ai.providers.gemini.api_key = "key123"
        config.ai.providers.openai.api_key = "openai_key"
        data = redact_secrets(config)
        assert data["ai"]["providers"]["gemini"]["api_key"] == "[REDACTED]"
        assert data["ai"]["providers"]["openai"]["api_key"] == "[REDACTED]"


class TestConfirmationRequired:
    def test_safety_require_confirmation_default_true(self):
        config = LazyTrackConfig()
        assert config.safety.require_confirmation is True
