from datetime import date
from typing import Optional

from lazytrack.jira.client import (
    JiraClient,
    JiraAuthenticationError,
    JiraPermissionError,
    JiraNotFoundError,
    JiraRateLimitError,
    JiraTimeoutError,
    JiraServerError,
)
from lazytrack.jira.gateway import JiraGateway
from lazytrack.jira.issues import build_assigned_issues_jql, parse_jira_issue, search_issues
from lazytrack.jira.worklogs import get_worklogs_for_user, get_worklogs_for_issue
from lazytrack.jira.models import IssueSummary, WorklogEntry


class RestrictiveJiraGateway:
    _client: JiraClient
    _current_user_key: str = ""

    def __init__(self, client: JiraClient):
        self._client = client

    async def get_current_user(self) -> dict:
        try:
            response = self._client.get("/rest/api/3/myself")
            self._current_user_key = response.get("accountId", "")
            return response
        except Exception as e:
            raise JiraAuthenticationError(f"Failed to get current user: {e}")

    async def search_assigned_issues(
        self,
        project_filter: Optional[str] = None,
        limit: int = 100,
    ) -> list[IssueSummary]:
        jql = build_assigned_issues_jql(project_filter)
        raw_issues = await search_issues(self._client, jql, limit)
        return [
            parse_jira_issue(issue, self._current_user_key)
            for issue in raw_issues
        ]

    async def get_issue(self, issue_key: str) -> dict:
        try:
            return self._client.get(f"/rest/api/3/issue/{issue_key}")
        except Exception as e:
            raise JiraNotFoundError(f"Issue not found: {issue_key}")

    async def get_worklogs_for_issue(
        self,
        issue_key: str,
    ) -> list[WorklogEntry]:
        return await get_worklogs_for_issue(
            self._client,
            issue_key,
            self._current_user_key,
        )

    async def get_worklogs_for_user(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[WorklogEntry]:
        return await get_worklogs_for_user(
            self._client,
            self._current_user_key,
            start_date,
            end_date,
        )
