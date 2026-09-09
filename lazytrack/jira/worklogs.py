from datetime import date, datetime, timedelta
from typing import Optional

from lazytrack.domain.tz import zone_for
from lazytrack.jira.client import JiraClient
from lazytrack.jira.models import WorklogEntry

PAGE_SIZE = 100


def _parse_started(started: str, timezone: str) -> date:
    s = started.replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and ":" not in s[-5:]:
        s = s[:-2] + ":" + s[-2:]
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone_for("UTC"))
    return dt.astimezone(zone_for(timezone)).date()


def parse_worklog(
    raw: dict,
    current_user_key: str,
    issue_key: str = "",
    timezone: str = "UTC",
) -> WorklogEntry:
    started = raw.get("started") or ""
    work_date = date.today()
    if started:
        try:
            work_date = _parse_started(started, timezone)
        except (ValueError, TypeError):
            pass

    author_account_id = raw.get("author", {}).get("accountId", "")
    author_is_current_user = author_account_id == current_user_key
    key = issue_key or str(raw.get("issueId", ""))

    return WorklogEntry(
        id=str(raw["id"]),
        issue_key=key,
        work_date=work_date,
        seconds=raw.get("timeSpentSeconds", 0),
        author_is_current_user=author_is_current_user,
        managed_by_lazytrack=False,
    )


def fetch_issue_worklogs(
    client: JiraClient,
    issue_key: str,
    current_user_key: str,
    timezone: str = "UTC",
) -> list[WorklogEntry]:
    start_at = 0
    entries: list[WorklogEntry] = []
    while True:
        response = client.get(
            f"/rest/api/3/issue/{issue_key}/worklog",
            params={"startAt": start_at, "maxResults": PAGE_SIZE},
        )
        page = response.get("worklogs", [])
        for w in page:
            entries.append(
                parse_worklog(w, current_user_key, issue_key=issue_key, timezone=timezone)
            )
        start_at += len(page)
        total = response.get("total", start_at)
        if start_at >= total or not page:
            break
    return entries


async def get_worklogs_for_issue(
    client: JiraClient,
    issue_key: str,
    current_user_key: str,
    timezone: str = "UTC",
) -> list[WorklogEntry]:
    return fetch_issue_worklogs(client, issue_key, current_user_key, timezone)


async def get_worklogs_for_user(
    client: JiraClient,
    current_user_key: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    timezone: str = "UTC",
) -> list[WorklogEntry]:
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=7)

    jql = (
        f'worklogAuthor = "{current_user_key}" '
        f"AND worklogDate >= {start_date.isoformat()} "
        f"AND worklogDate <= {end_date.isoformat()}"
    )

    issue_keys: list[str] = []
    next_token = None
    while True:
        params = {
            "jql": jql,
            "startAt": 0,
            "maxResults": 100,
            "fields": "key",
        }
        if next_token:
            params["nextPageToken"] = next_token

        response = client.get("/rest/api/3/search/jql", params=params)
        for issue in response.get("issues", []):
            key = issue.get("key", "")
            if key:
                issue_keys.append(key)

        if response.get("isLast", True):
            break
        next_token = response.get("nextPageToken")
        if not next_token:
            break

    seen: set[str] = set()
    all_worklogs: list[WorklogEntry] = []
    for key in issue_keys:
        for wl in fetch_issue_worklogs(client, key, current_user_key, timezone):
            if wl.id in seen:
                continue
            if not wl.author_is_current_user:
                continue
            if not (start_date <= wl.work_date <= end_date):
                continue
            seen.add(wl.id)
            all_worklogs.append(wl)
    return all_worklogs
