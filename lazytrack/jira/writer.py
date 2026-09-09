import logging
from datetime import date, datetime, timezone as dt_timezone
from typing import Optional

from lazytrack.domain.tz import parse_hhmm, resolve_zone, zone_for
from lazytrack.jira.client import JiraClient
from lazytrack.jira.models import WorklogEntry

logger = logging.getLogger(__name__)


def format_jira_started(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    offset = dt.strftime("%z")  # +0800
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000") + offset


class WorklogWriter:
    def __init__(
        self,
        client: JiraClient,
        timezone: str = "UTC",
        day_start: str = "10:00",
    ):
        self._client = client
        self._timezone = timezone
        self._day_start = day_start
        self._display_tz: Optional[str] = None

    def display_timezone(self) -> str:
        """Timezone Jira renders worklogs in, i.e. the account's profile timezone."""
        if self._display_tz is None:
            self._display_tz = self._fetch_display_timezone()
        return self._display_tz

    def _fetch_display_timezone(self) -> str:
        try:
            profile_tz = self._client.get("/rest/api/3/myself").get("timeZone")
        except Exception:
            logger.warning(
                "Could not read the Jira profile timezone, using %s instead.",
                self._timezone,
            )
            return self._timezone

        if not profile_tz:
            return self._timezone
        if resolve_zone(profile_tz) is None:
            logger.warning(
                "Jira profile timezone %r is not recognised, using %s instead. "
                "Worklogs may display at the wrong time.",
                profile_tz,
                self._timezone,
            )
            return self._timezone
        return profile_tz

    def _anchor(self, dt: datetime) -> datetime:
        """Keep the wall clock, swap the offset, so Jira displays the intended time."""
        return dt.replace(tzinfo=zone_for(self.display_timezone()))

    def _default_started(self, work_date: date) -> datetime:
        t = parse_hhmm(self._day_start)
        return datetime.combine(work_date, t, tzinfo=zone_for(self._timezone))

    def create_worklog(
        self,
        issue_key: str,
        work_date: date,
        seconds: int,
        plan_id: str,
        started_at: Optional[datetime] = None,
    ) -> WorklogEntry:
        started = format_jira_started(
            self._anchor(started_at or self._default_started(work_date))
        )

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
        started_at: Optional[datetime] = None,
    ) -> tuple[WorklogEntry, str]:
        started = format_jira_started(
            self._anchor(started_at or self._default_started(work_date))
        )

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
