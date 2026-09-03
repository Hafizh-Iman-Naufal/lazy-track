from datetime import date, datetime, timedelta
from typing import Optional

from lazytrack.jira.client import JiraClient
from lazytrack.jira.models import WorklogEntry


def parse_worklog(raw: dict, current_user_key: str) -> WorklogEntry:
    started = raw.get("started", "")
    work_date = date.today()
    if started:
        try:
            dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            work_date = dt.date()
        except (ValueError, TypeError):
            pass

    author_account_id = raw.get("author", {}).get("accountId", "")
    author_is_current_user = author_account_id == current_user_key

    return WorklogEntry(
        id=str(raw["id"]),
        issue_key=raw.get("issueId", ""),
        work_date=work_date,
        seconds=raw.get("timeSpentSeconds", 0),
        author_is_current_user=author_is_current_user,
        managed_by_lazytrack=False,
    )


async def get_worklogs_for_issue(
    client: JiraClient,
    issue_key: str,
    current_user_key: str,
) -> list[WorklogEntry]:
    try:
        response = client.get(f"/rest/api/3/issue/{issue_key}/worklog")
        worklogs = response.get("worklogs", [])
        return [parse_worklog(w, current_user_key) for w in worklogs]
    except Exception:
        return []


async def get_worklogs_for_user(
    client: JiraClient,
    current_user_key: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
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

    all_worklogs = []
    start_at = 0
    page_size = 100

    while True:
        response = client.get(
            "/rest/api/3/search",
            params={
                "jql": jql,
                "startAt": start_at,
                "maxResults": page_size,
                "fields": "worklog",
            },
        )

        issues = response.get("issues", [])
        for issue in issues:
            worklogs = issue.get("fields", {}).get("worklog", {}).get("worklogs", [])
            for w in worklogs:
                w["issueId"] = issue["key"]
                all_worklogs.append(parse_worklog(w, current_user_key))

        total = response.get("total", 0)
        start_at += len(issues)
        if start_at >= total:
            break

    return all_worklogs
