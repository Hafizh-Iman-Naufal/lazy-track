from datetime import date
from typing import Protocol

from lazytrack.jira.models import IssueSummary, WorklogEntry


class JiraGateway(Protocol):
    async def get_current_user(self) -> dict:
        ...

    async def search_assigned_issues(
        self,
        project_filter: str | None = None,
        limit: int = 100,
    ) -> list[IssueSummary]:
        ...

    async def get_issue(self, issue_key: str) -> dict:
        ...

    async def get_worklogs_for_issue(self, issue_key: str) -> list[WorklogEntry]:
        ...

    async def get_worklogs_for_user(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[WorklogEntry]:
        ...
