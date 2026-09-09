import pytest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from lazytrack.jira.models import IssueSummary, WorklogEntry
from lazytrack.jira.issues import build_assigned_issues_jql, parse_jira_issue
from lazytrack.jira.worklogs import fetch_issue_worklogs, get_worklogs_for_user, parse_worklog


class TestIssueSummary:
    def test_issue_summary_creation(self):
        issue = IssueSummary(
            key="ABC-123",
            summary="Test issue",
            issue_type="Story",
            status="In Progress",
            project_key="ABC",
            assignee_is_current_user=True,
        )
        assert issue.key == "ABC-123"
        assert issue.summary == "Test issue"
        assert issue.issue_type == "Story"
        assert issue.assignee_is_current_user is True


class TestWorklogEntry:
    def test_worklog_entry_creation(self):
        worklog = WorklogEntry(
            id="12345",
            issue_key="ABC-123",
            work_date=date(2026, 9, 4),
            seconds=28800,
            author_is_current_user=True,
        )
        assert worklog.id == "12345"
        assert worklog.seconds == 28800
        assert worklog.hours == Decimal("8")

    def test_worklog_hours_calculation(self):
        worklog = WorklogEntry(
            id="12345",
            issue_key="ABC-123",
            work_date=date(2026, 9, 4),
            seconds=14400,
            author_is_current_user=True,
        )
        assert worklog.hours == Decimal("4")


class TestBuildAssignedIssuesJql:
    def test_basic_jql(self):
        jql = build_assigned_issues_jql()
        assert "assignee = currentUser()" in jql
        assert "statusCategory != Done" in jql

    def test_jql_with_project_filter(self):
        jql = build_assigned_issues_jql(project_filter="ABC")
        assert "project = ABC" in jql


class TestParseJiraIssue:
    def test_parse_minimal_issue(self):
        raw = {
            "key": "ABC-123",
            "fields": {
                "summary": "Test",
                "issuetype": {"name": "Bug"},
                "status": {"name": "Open"},
                "project": {"key": "ABC"},
                "assignee": None,
            },
        }
        issue = parse_jira_issue(raw, "user123")
        assert issue.key == "ABC-123"
        assert issue.summary == "Test"
        assert issue.issue_type == "Bug"

    def test_parse_issue_with_assignee(self):
        raw = {
            "key": "ABC-123",
            "fields": {
                "summary": "Test",
                "issuetype": {"name": "Story"},
                "status": {"name": "In Progress"},
                "project": {"key": "ABC"},
                "assignee": {"accountId": "user123"},
            },
        }
        issue = parse_jira_issue(raw, "user123")
        assert issue.assignee_is_current_user is True

    def test_parse_issue_different_assignee(self):
        raw = {
            "key": "ABC-123",
            "fields": {
                "summary": "Test",
                "issuetype": {"name": "Story"},
                "status": {"name": "In Progress"},
                "project": {"key": "ABC"},
                "assignee": {"accountId": "other_user"},
            },
        }
        issue = parse_jira_issue(raw, "user123")
        assert issue.assignee_is_current_user is False


class TestParseWorklog:
    def test_parse_minimal_worklog(self):
        raw = {
            "id": "12345",
            "issueId": "ABC-123",
            "started": "2026-09-04T10:00:00.000+0000",
            "timeSpentSeconds": 28800,
            "author": {"accountId": "user123"},
        }
        worklog = parse_worklog(raw, "user123")
        assert worklog.id == "12345"
        assert worklog.issue_key == "ABC-123"
        assert worklog.seconds == 28800
        assert worklog.author_is_current_user is True

    def test_parse_worklog_different_user(self):
        raw = {
            "id": "12345",
            "issueId": "ABC-123",
            "started": "2026-09-04T10:00:00.000+0000",
            "timeSpentSeconds": 28800,
            "author": {"accountId": "other_user"},
        }
        worklog = parse_worklog(raw, "user123")
        assert worklog.author_is_current_user is False

    def test_parse_worklog_no_start_date(self):
        raw = {
            "id": "12345",
            "issueId": "ABC-123",
            "started": None,
            "timeSpentSeconds": 28800,
            "author": {"accountId": "user123"},
        }
        worklog = parse_worklog(raw, "user123")
        assert worklog.work_date == date.today()

    def test_started_in_work_timezone(self):
        raw = {
            "id": "1",
            "started": "2026-09-03T17:00:00.000+00:00",
            "timeSpentSeconds": 3600,
            "author": {"accountId": "user123"},
        }
        worklog = parse_worklog(raw, "user123", issue_key="SP-1", timezone="Asia/Makassar")
        assert worklog.work_date == date(2026, 9, 4)
        assert worklog.issue_key == "SP-1"


class TestPaginateIssueWorklogs:
    def test_two_pages(self):
        class Client:
            def get(self, path, params=None):
                start = params["startAt"]
                if start == 0:
                    logs = [
                        {
                            "id": str(i),
                            "started": "2026-09-04T10:00:00.000+08:00",
                            "timeSpentSeconds": 3600,
                            "author": {"accountId": "u"},
                        }
                        for i in range(100)
                    ]
                    return {"worklogs": logs, "total": 120}
                logs = [
                    {
                        "id": str(i),
                        "started": "2026-09-04T10:00:00.000+08:00",
                        "timeSpentSeconds": 3600,
                        "author": {"accountId": "u"},
                    }
                    for i in range(100, 120)
                ]
                return {"worklogs": logs, "total": 120}

        entries = fetch_issue_worklogs(Client(), "SP-1", "u", "Asia/Makassar")
        assert len(entries) == 120
        assert entries[0].issue_key == "SP-1"
        assert entries[-1].id == "119"

    def test_fetch_propagates_errors(self):
        class Client:
            def get(self, path, params=None):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            fetch_issue_worklogs(Client(), "SP-1", "u")


@pytest.mark.asyncio
async def test_get_worklogs_for_user_filters_and_paginates():
    class Client:
        def get(self, path, params=None):
            if path.endswith("/search/jql"):
                return {"issues": [{"key": "SP-1"}], "isLast": True}
            start = (params or {}).get("startAt", 0)
            logs = [
                {
                    "id": "keep",
                    "started": "2026-09-04T10:00:00.000+08:00",
                    "timeSpentSeconds": 7200,
                    "author": {"accountId": "u"},
                },
                {
                    "id": "other",
                    "started": "2026-09-04T10:00:00.000+08:00",
                    "timeSpentSeconds": 7200,
                    "author": {"accountId": "x"},
                },
                {
                    "id": "old",
                    "started": "2025-01-06T10:00:00.000+08:00",
                    "timeSpentSeconds": 7200,
                    "author": {"accountId": "u"},
                },
            ]
            if start > 0:
                return {"worklogs": [], "total": 3}
            return {"worklogs": logs, "total": 3}

    worklogs = await get_worklogs_for_user(
        Client(),
        "u",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 9, 6),
        timezone="Asia/Makassar",
    )
    assert [w.id for w in worklogs] == ["keep"]

