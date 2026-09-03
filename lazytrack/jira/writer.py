from datetime import date
from typing import Optional

from lazytrack.jira.client import JiraClient
from lazytrack.jira.models import WorklogEntry


class WorklogWriter:
    def __init__(self, client: JiraClient):
        self._client = client

    def create_worklog(
        self,
        issue_key: str,
        work_date: date,
        seconds: int,
        plan_id: str,
    ) -> WorklogEntry:
        import datetime
        started = datetime.datetime(
            work_date.year, work_date.month, work_date.day,
            10, 0, 0
        ).isoformat() + "Z"

        response = self._client.post(
            f"/rest/api/3/issue/{issue_key}/worklog",
            json={
                "started": started,
                "timeSpentSeconds": seconds,
            },
        )

        return WorklogEntry(
            id=str(response["id"]),
            issue_key=issue_key,
            work_date=work_date,
            seconds=seconds,
            author_is_current_user=True,
            managed_by_lazytrack=True,
            plan_id=plan_id,
        )

    def update_worklog(
        self,
        worklog_id: str,
        issue_key: str,
        seconds: int,
    ) -> WorklogEntry:
        response = self._client.put(
            f"/rest/api/3/issue/{issue_key}/worklog/{worklog_id}",
            json={
                "timeSpentSeconds": seconds,
            },
        )

        from lazytrack.jira.worklogs import parse_worklog
        return parse_worklog(response, "current_user")

    def move_worklog(
        self,
        worklog_id: str,
        from_issue_key: str,
        to_issue_key: str,
        work_date: date,
        seconds: int,
        plan_id: str,
    ) -> tuple[WorklogEntry, str]:
        import datetime
        started = datetime.datetime(
            work_date.year, work_date.month, work_date.day,
            10, 0, 0
        ).isoformat() + "Z"

        self._client.delete(
            f"/rest/api/3/issue/{from_issue_key}/worklog/{worklog_id}"
        )

        response = self._client.post(
            f"/rest/api/3/issue/{to_issue_key}/worklog",
            json={
                "started": started,
                "timeSpentSeconds": seconds,
            },
        )

        new_worklog = WorklogEntry(
            id=str(response["id"]),
            issue_key=to_issue_key,
            work_date=work_date,
            seconds=seconds,
            author_is_current_user=True,
            managed_by_lazytrack=True,
            plan_id=plan_id,
        )

        return new_worklog, worklog_id

    def delete_worklog(
        self,
        worklog_id: str,
        issue_key: str,
    ) -> bool:
        self._client.delete(
            f"/rest/api/3/issue/{issue_key}/worklog/{worklog_id}"
        )
        return True
