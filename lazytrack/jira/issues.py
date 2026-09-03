from typing import Optional

from lazytrack.jira.client import JiraClient
from lazytrack.jira.models import IssueSummary


def parse_jira_issue(raw: dict, current_user_key: str) -> IssueSummary:
    fields = raw.get("fields", {})
    assignee = fields.get("assignee", {}) or {}
    project = fields.get("project", {}) or {}

    return IssueSummary(
        key=raw["key"],
        summary=fields.get("summary", ""),
        issue_type=fields.get("issuetype", {}).get("name", "Unknown"),
        status=fields.get("status", {}).get("name", "Unknown"),
        project_key=project.get("key", ""),
        assignee_is_current_user=assignee.get("accountId") == current_user_key,
    )


def build_assigned_issues_jql(
    project_filter: Optional[str] = None,
) -> str:
    jql_parts = [
        "assignee = currentUser()",
        "statusCategory != Done",
    ]
    if project_filter:
        jql_parts.append(f"project = {project_filter}")
    return " AND ".join(jql_parts) + " ORDER BY updated DESC"


async def search_issues(
    client: JiraClient,
    jql: str,
    limit: int = 100,
) -> list[dict]:
    issues = []
    start_at = 0
    page_size = 50

    while start_at < limit:
        response = client.get(
            "/rest/api/3/search",
            params={
                "jql": jql,
                "startAt": start_at,
                "maxResults": min(page_size, limit - start_at),
                "fields": "summary,status,issuetype,project,assignee",
            },
        )
        batch = response.get("issues", [])
        issues.extend(batch)
        start_at += len(batch)

        if len(batch) < page_size:
            break

    return issues
