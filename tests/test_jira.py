import pytest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from lazytrack.jira.models import IssueSummary, WorklogEntry
from lazytrack.jira.issues import build_assigned_issues_jql, parse_jira_issue
from lazytrack.jira.worklogs import parse_worklog


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
