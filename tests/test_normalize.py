import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock

from lazytrack.ai.base import AITimeoutError
from lazytrack.ai.generate import generate_structured_intent
from lazytrack.ai.normalize import ParseContext, parse_intent, timezone_from_text
from lazytrack.ai.prompts import build_intent_prompt
from lazytrack.ai.schemas import (
    AddLeaveIntent,
    AllocateTimeIntent,
    ClarificationRequired,
    ShowWeekIntent,
)
from lazytrack.cli import _format_response
from lazytrack.config import LazyTrackConfig
from lazytrack.domain import Allocation, AllocationRequest, Planner, PlannerContext
from lazytrack.domain.calendar import WorkCalendar
from lazytrack.domain.planner import parse_hhmm
from lazytrack.domain.tz import zone_for
from lazytrack.jira.models import IssueSummary
from lazytrack.jira.writer import WorklogWriter, format_jira_started


CTX = ParseContext(
    today=date(2026, 9, 4),
    timezone="Asia/Makassar",
    hours_per_day=8.0,
    issue_keys=["SP-8412"],
)

SPLIT_CTX = ParseContext(
    today=date(2026, 9, 4),
    timezone="Asia/Makassar",
    hours_per_day=8.0,
    issue_keys=["SP-8412", "SP-8517"],
)

GEMINI_RAW = {
    "type": "allocate",
    "issue_key": "SP-8412",
    "start_date": "today",
    "week": (
        "this week Friday for jumatan 12:00-13:00 WITA time for gap in worklog, "
        "I mean, I want to allocate 8 hours on this stories key SP-8412 for today, "
        "and the time is friday"
    ),
}


class TestParseIntentLoggedGemini:
    def test_logged_gemini_json_becomes_allocate(self):
        intent = parse_intent(GEMINI_RAW, CTX)
        assert isinstance(intent, AllocateTimeIntent)
        assert intent.start_date == date(2026, 9, 4)
        assert intent.end_date == date(2026, 9, 4)
        assert intent.allocations[0].issue_key == "SP-8412"
        assert intent.allocations[0].hours_per_day == 8.0
        assert intent.gaps[0].start == "12:00"
        assert intent.gaps[0].end == "13:00"

    def test_friday_relative_to_frozen_today(self):
        intent = parse_intent(
            {"type": "allocate", "issue_key": "SP-8412", "start_date": "friday", "hours": 8},
            CTX,
        )
        assert isinstance(intent, AllocateTimeIntent)
        assert intent.start_date == date(2026, 9, 4)

    def test_qualified_relative_weekdays_are_deterministic(self):
        last_monday = parse_intent(
            {
                "type": "allocate",
                "issue_key": "SP-8412",
                "start_date": "last monday",
                "hours": 1,
            },
            CTX,
        )
        next_friday = parse_intent(
            {
                "type": "allocate",
                "issue_key": "SP-8412",
                "start_date": "next friday",
                "hours": 1,
            },
            CTX,
        )
        assert last_monday.start_date == date(2026, 8, 31)
        assert next_friday.start_date == date(2026, 9, 11)

    def test_bare_and_this_weekdays_stay_in_the_current_week(self):
        # CTX today is Friday 2026-09-04, so its week runs 08-31 to 09-06.
        for wording, expected in (
            ("monday", date(2026, 8, 31)),
            ("this monday", date(2026, 8, 31)),
            ("monday this week", date(2026, 8, 31)),
            ("wednesday", date(2026, 9, 2)),
            ("monday last week", date(2026, 8, 24)),
            ("monday next week", date(2026, 9, 7)),
        ):
            intent = parse_intent(
                {
                    "type": "allocate",
                    "issue_key": "SP-8412",
                    "start_date": wording,
                    "hours": 1,
                },
                CTX,
            )
            assert intent.start_date == expected, wording

    def test_unknown_intent_is_clarification(self):
        intent = parse_intent({"type": "create_jira_issue", "summary": "nope"}, CTX)
        assert isinstance(intent, ClarificationRequired)
        assert "can't" in intent.reason.lower() or "cannot" in intent.reason.lower()

    def test_wita_alias(self):
        assert timezone_from_text("gap 12:00-13:00 WITA", "UTC") == "Asia/Makassar"

    def test_show_week_preserves_and_normalizes_iso_week(self):
        intent = parse_intent({"type": "show_week", "week": "2026-w6"}, CTX)
        assert isinstance(intent, ShowWeekIntent)
        assert intent.week == "2026-W06"
        assert intent.weeks == ["2026-W06"]

    def test_show_week_date_range_fills_weeks(self):
        intent = parse_intent(
            {
                "type": "show_week",
                "start_date": "2026-08-03",
                "end_date": "2026-08-16",
            },
            CTX,
        )
        assert isinstance(intent, ShowWeekIntent)
        assert intent.weeks == ["2026-W32", "2026-W33"]

    def test_add_leave_list_and_range(self):
        listed = parse_intent(
            {
                "type": "add_leave",
                "dates": ["2026-08-03", "2026-08-04", "2026-08-05"],
            },
            CTX,
        )
        spanned = parse_intent(
            {
                "type": "add_leave",
                "start_date": "2026-08-03",
                "end_date": "2026-08-05",
            },
            CTX,
        )
        assert isinstance(listed, AddLeaveIntent)
        assert listed.dates == [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
        assert spanned.dates == listed.dates

    def test_add_leave_validation_is_not_allocate_copy(self):
        intent = parse_intent({"type": "add_leave"}, CTX)
        assert isinstance(intent, ClarificationRequired)
        assert "leave" in intent.reason.lower()
        assert intent.missing_fields == ["date"]
        assert "allocations" not in intent.missing_fields

    def test_sync_intent_explains_cli(self):
        intent = parse_intent({"type": "sync_jira"}, CTX)
        assert isinstance(intent, ClarificationRequired)
        assert "lazytrack sync" in intent.reason

    def test_prompt_includes_current_iso_week(self):
        prompt = build_intent_prompt(
            "what week is today?",
            today=CTX.today,
            timezone=CTX.timezone,
            hours_per_day=CTX.hours_per_day,
        )
        assert "Current ISO week: 2026-W36" in prompt
        assert '{"type": "show_week", "week": "2026-W36"}' in prompt
        assert '"dates": ["2026-08-03", "2026-08-04", "2026-08-05"]' in prompt

    def test_multi_issue_remaining_is_resolved_in_order(self):
        intent = parse_intent(
            {
                "type": "allocate_time",
                "worklogs": [
                    {"date": "yesterday", "issue_key": "SP-8412", "hours": 4},
                    {
                        "date": "yesterday",
                        "issue_key": "SP-8412",
                        "hours": "remaining",
                        "remaining": True,
                    },
                ],
                "total_hours": 9,
            },
            CTX,
        )
        assert isinstance(intent, AllocateTimeIntent)
        assert [item.hours for item in intent.worklogs] == [4, 5]
        assert all(item.date == date(2026, 9, 3) for item in intent.worklogs)

    def test_conflicting_weekday_and_date_requires_clarification(self):
        intent = parse_intent(
            {
                "type": "allocate_time",
                "worklogs": [
                    {
                        "date": "Saturday, September 6",
                        "issue_key": "SP-8412",
                        "hours": 2,
                    }
                ],
            },
            CTX,
        )
        assert isinstance(intent, ClarificationRequired)

    def test_one_start_time_anchors_the_first_same_day_entry(self):
        intent = parse_intent(
            {
                "type": "allocate_time",
                "worklogs": [
                    {
                        "date": "yesterday",
                        "issue_key": "SP-8412",
                        "hours": 7,
                        "start_time": "09:00",
                    },
                    {
                        "date": "yesterday",
                        "issue_key": "SP-8517",
                        "hours": 5,
                        "start_time": "09:00",
                    },
                ],
            },
            SPLIT_CTX,
        )
        assert isinstance(intent, AllocateTimeIntent)
        assert [item.start_time for item in intent.worklogs] == ["09:00", None]

    def test_distinct_start_times_are_preserved(self):
        intent = parse_intent(
            {
                "type": "allocate_time",
                "worklogs": [
                    {
                        "date": "yesterday",
                        "issue_key": "SP-8412",
                        "hours": 2,
                        "start_time": "09:00",
                    },
                    {
                        "date": "yesterday",
                        "issue_key": "SP-8517",
                        "hours": 3,
                        "start_time": "14:00",
                    },
                ],
            },
            SPLIT_CTX,
        )
        assert [item.start_time for item in intent.worklogs] == ["09:00", "14:00"]

    def test_repeated_start_time_on_different_dates_is_kept(self):
        intent = parse_intent(
            {
                "type": "allocate_time",
                "worklogs": [
                    {
                        "date": "yesterday",
                        "issue_key": "SP-8412",
                        "hours": 2,
                        "start_time": "09:00",
                    },
                    {
                        "date": "today",
                        "issue_key": "SP-8517",
                        "hours": 3,
                        "start_time": "09:00",
                    },
                ],
            },
            SPLIT_CTX,
        )
        assert [item.start_time for item in intent.worklogs] == ["09:00", "09:00"]

    def test_unknown_worklog_issue_requires_clarification(self):
        intent = parse_intent(
            {
                "type": "allocate_time",
                "worklogs": [
                    {"date": "today", "issue_key": "NOPE-1", "hours": 2}
                ],
            },
            CTX,
        )
        assert isinstance(intent, ClarificationRequired)
        assert "NOPE-1" in intent.reason


class TestGapPlan:
    def test_friday_gap_two_worklogs(self):
        config = LazyTrackConfig()
        config.work.timezone = "Asia/Makassar"
        calendar = WorkCalendar(config=config.work)
        issues = [
            IssueSummary(
                key="SP-8412",
                summary="Story",
                issue_type="Story",
                status="In Progress",
                project_key="SP",
                assignee_is_current_user=True,
            )
        ]
        planner = Planner(
            PlannerContext(
                calendar=calendar,
                assigned_issues=issues,
                user_worklogs=[],
                managed_worklogs=[],
            ),
            config,
        )
        friday = date(2026, 9, 4)
        plan = planner.create_plan(
            AllocationRequest(
                start_date=friday,
                end_date=friday,
                allocations=[Allocation(issue_key="SP-8412", hours_per_day=8)],
                gaps=[(parse_hhmm("12:00"), parse_hhmm("13:00"))],
                timezone="Asia/Makassar",
            )
        )
        assert len(plan.operations) == 2
        total = sum(op.seconds for op in plan.operations)
        assert total == 28800
        starts = [op.started_at.strftime("%H:%M") for op in plan.operations]
        assert starts == ["10:00", "13:00"]
        assert [op.seconds for op in plan.operations] == [7200, 21600]
        assert plan.operations[0].started_at.tzinfo is not None
        assert plan.operations[0].started_at.utcoffset().total_seconds() == 8 * 3600


class TestFormatPreview:
    def test_plan_preview_string(self):
        out = _format_response(
            {"type": "plan_preview", "preview": "Plan: PL-1\n8h on SP-8412"},
            None,
        )
        assert "Plan: PL-1" in out
        assert "Proposed plan preview" in out


class TestJiraStarted:
    def test_wita_offset(self):
        dt = datetime(2026, 9, 4, 8, 0, tzinfo=zone_for("Asia/Makassar"))
        assert format_jira_started(dt) == "2026-09-04T08:00:00.000+0800"


class FakeJiraClient:
    def __init__(self, profile_timezone=None, fail_profile=False):
        self._profile_timezone = profile_timezone
        self._fail_profile = fail_profile
        self.posted = []

    def get(self, path, **kwargs):
        if self._fail_profile:
            raise RuntimeError("no profile")
        return {"accountId": "user123", "timeZone": self._profile_timezone}

    def post(self, path, **kwargs):
        self.posted.append(kwargs["json"])
        return {"id": "62961"}


class TestWorklogDisplayTimezone:
    def _write(self, client):
        writer = WorklogWriter(client, timezone="Asia/Makassar", day_start="10:00")
        writer.create_worklog(
            "SP-8412",
            date(2026, 9, 4),
            7200,
            "PL-1",
            started_at=datetime(2026, 9, 4, 10, 0, tzinfo=zone_for("Asia/Makassar")),
        )
        return client.posted[0]["started"]

    def test_started_uses_jira_profile_timezone(self):
        client = FakeJiraClient(profile_timezone="Europe/Amsterdam")
        assert self._write(client) == "2026-09-04T10:00:00.000+0200"

    def test_matching_timezone_is_unchanged(self):
        client = FakeJiraClient(profile_timezone="Asia/Makassar")
        assert self._write(client) == "2026-09-04T10:00:00.000+0800"

    def test_profile_lookup_failure_falls_back_to_work_timezone(self):
        client = FakeJiraClient(fail_profile=True)
        assert self._write(client) == "2026-09-04T10:00:00.000+0800"

    def test_unresolvable_profile_zone_falls_back_to_work_timezone(self):
        client = FakeJiraClient(profile_timezone="Mars/Olympus_Mons")
        assert self._write(client) == "2026-09-04T10:00:00.000+0800"

    def test_profile_is_fetched_once(self):
        client = FakeJiraClient(profile_timezone="Europe/Amsterdam")
        writer = WorklogWriter(client, timezone="Asia/Makassar", day_start="10:00")
        writer.display_timezone()
        client._fail_profile = True
        assert writer.display_timezone() == "Europe/Amsterdam"


class TestTimeoutRetry:
    def test_timeout_then_success(self):
        provider = AsyncMock()
        provider.generate = AsyncMock(
            side_effect=[
                AITimeoutError("Request timed out: "),
                '{"type": "show_issues"}',
            ]
        )
        intent = asyncio.run(generate_structured_intent(provider, "show issues", CTX))
        assert intent.type == "show_issues"
        assert provider.generate.call_count == 2

    def test_double_timeout_apologizes(self):
        provider = AsyncMock()
        provider.generate = AsyncMock(side_effect=AITimeoutError("Request timed out: "))
        intent = asyncio.run(generate_structured_intent(provider, "hello", CTX))
        assert isinstance(intent, ClarificationRequired)
        assert "timed out" in intent.reason.lower()
