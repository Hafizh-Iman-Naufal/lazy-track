import asyncio
import json
import logging
import sys
from datetime import date, timedelta
from decimal import Decimal

import typer
from InquirerPy import inquirer
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.table import Table

from lazytrack import __version__
from lazytrack.ai.base import AIError, AIAuthenticationError, AIRateLimitError, AITimeoutError
from lazytrack.ai.generate import generate_structured_intent
from lazytrack.ai.normalize import ParseContext, today_in_timezone, timezone_from_text
from lazytrack.ai.prompts import build_intent_prompt
from lazytrack.ai.providers.gemini import GeminiProvider
from lazytrack.ai.providers.deepseek import DeepSeekProvider
from lazytrack.ai.providers.minimax import MiniMaxProvider
from lazytrack.config import load_config, redact_secrets
from lazytrack.domain import LeaveEntry, HolidayEntry
from lazytrack.domain.calendar import create_calendar
from lazytrack.jira import (
    JiraClient,
    RestrictiveJiraGateway,
    JiraAuthenticationError,
    JiraRateLimitError,
    JiraTimeoutError,
    JiraServerError,
)
from lazytrack.ai.schemas import ShowIssuesIntent, ShowWeekIntent
from lazytrack.ui.banner import print_chat_banner
from lazytrack.ui.chat import (
    CHAT_HELP,
    ChatSessionState,
    ChatOrchestrator,
    chat_history_path,
    confirmation_reply,
    parse_chat_command,
    parse_status_week,
)
from lazytrack.ui.issues import issues_table
from lazytrack.ui.status import parse_iso_week, week_status_renderable
from lazytrack.storage import get_db, CalendarRepository, WorklogCacheRepository
from lazytrack.domain.window import cache_window

_HELP_CTX = {"help_option_names": ["-h", "--help"]}

cli = typer.Typer(
    name="lazytrack",
    help="AI-assisted Jira worklog CLI.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings=_HELP_CTX,
    epilog="Try: lazytrack sync · lazytrack status · lazytrack chat",
)
config_cmd = typer.Typer(
    help="Show configuration.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings=_HELP_CTX,
)
cli.add_typer(config_cmd, name="config")

leave_cmd = typer.Typer(
    help="Add or remove leave.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings=_HELP_CTX,
)
cli.add_typer(leave_cmd, name="leave")

holiday_cmd = typer.Typer(
    help="Add or remove holidays.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings=_HELP_CTX,
)
cli.add_typer(holiday_cmd, name="holiday")

plan_cmd = typer.Typer(
    help="List or show allocation plans.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings=_HELP_CTX,
)
cli.add_typer(plan_cmd, name="plan")

console = Console()


def _confirm(message: str) -> bool:
    return inquirer.confirm(message=message, default=False).execute()


def _confirm_apply_to_jira() -> bool:
    return _confirm("Apply this to Jira?")


def _proceed_remote_write(*, yes: bool, message: str) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        console.print("[red]Refusing to write without --yes in non-interactive mode.[/red]")
        raise typer.Exit(1)
    if not _confirm(message):
        console.print("[yellow]Cancelled.[/yellow]")
        return False
    return True


def get_jira_gateway():
    config = load_config()
    if not config.jira.base_url:
        raise typer.Exit("Jira base URL not configured. Run: lazytrack config show")
    if not config.jira.api_token:
        raise typer.Exit("Jira API token not configured. Set JIRA_API_TOKEN in .env")

    client = JiraClient(
        base_url=config.jira.base_url,
        email=config.jira.email,
        api_token=config.jira.api_token,
    )
    return RestrictiveJiraGateway(client)


@cli.command()
def version():
    """Show the version of LazyTrack."""
    typer.echo(__version__)


@config_cmd.command("show")
def config_show():
    """Show current configuration."""
    try:
        config = load_config()
        data = redact_secrets(config)

        console.print("\n[bold]Work:[/bold]")
        work = data.get("work", {})
        console.print(f"  Hours/day: {work.get('hours_per_day', 8)}")
        console.print(f"  Weekly target: {work.get('weekly_target', 40)}")
        working_days = work.get("working_days", [])
        console.print(f"  Working days: {' '.join(d.title() for d in working_days)}")

        console.print("\n[bold]Jira:[/bold]")
        jira = data.get("jira", {})
        base_url = jira.get("base_url", "")
        console.print(f"  Base URL: {base_url if base_url else '(not configured)'}")
        api_token = jira.get("api_token", "")
        console.print(f"  Token: {'configured' if api_token and api_token != '[REDACTED]' else 'not configured'}")

        console.print("\n[bold]AI:[/bold]")
        ai = data.get("ai", {})
        console.print(f"  Provider: {ai.get('provider', 'gemini')}")

    except Exception as e:
        console.print(f"[red]Error loading configuration: {e}[/red]")
        raise typer.Exit(1)


@cli.command()
def sync(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print JQL and raw results"),
    include_done: bool = typer.Option(False, "--include-done", "-d", help="Include Done status issues"),
    sync_worklogs: bool = typer.Option(True, "--sync-worklogs/--no-worklogs", help="Sync worklogs"),
):
    """Sync issues and worklogs from Jira."""
    console.print("[yellow]Syncing with Jira...[/yellow]")

    try:
        config = load_config()
        gateway = get_jira_gateway()

        use_done = include_done or config.jira.include_done
        project = config.jira.project or None

        async def do_sync():
            user = await gateway.get_current_user()
            if verbose:
                console.print(f"[dim]Logged in as:[/dim] [cyan]{user.get('displayName', '?')} ({user.get('accountId', '?')})[/cyan]")
            issues = await gateway.search_assigned_issues(project, use_done)
            return issues

        issues = asyncio.run(do_sync())

        if verbose:
            from lazytrack.jira.issues import build_assigned_issues_jql
            jql = build_assigned_issues_jql(project, use_done)
            console.print(f"[dim]JQL:[/dim] [cyan]{jql}[/cyan]")
            console.print(f"[dim]Found:[/dim] [cyan]{len(issues)}[/cyan] issues")
            if not issues and not use_done:
                console.print("[dim]Hint: try -d to include Done status issues[/dim]")
            for issue in issues[:10]:
                console.print(f"  {issue.key} [{issue.status}] {issue.summary}")
            if len(issues) > 10:
                console.print(f"  [dim]... and {len(issues) - 10} more[/dim]")

        db = get_db()
        conn = db.connect()

        for issue in issues:
            conn.execute(
                """
                INSERT OR REPLACE INTO issues_cache 
                (key, summary, issue_type, status, project_key, assignee)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    issue.key,
                    issue.summary,
                    issue.issue_type,
                    issue.status,
                    issue.project_key,
                    "current_user" if issue.assignee_is_current_user else None,
                ),
            )

        conn.commit()

        worklog_count = 0
        if sync_worklogs:
            start_date, end_date = cache_window(
                date.today(), config.safety.worklog_lookback_weeks
            )

            if verbose:
                console.print(f"[dim]Syncing worklogs from {start_date} to {end_date}...[/dim]")

            async def do_sync_worklogs():
                return await gateway.get_worklogs_for_user(
                    start_date, end_date, timezone=config.work.timezone
                )

            worklogs = asyncio.run(do_sync_worklogs())
            worklog_count = WorklogCacheRepository(conn).replace_window(
                worklogs, start_date, end_date
            )

            if verbose:
                console.print(
                    f"[dim]Refreshed:[/dim] [cyan]{worklog_count}[/cyan] "
                    f"worklogs ({start_date} → {end_date})"
                )

        db.close()

        parts = [f"[green]Synced {len(issues)} issues[/green]"]
        if sync_worklogs:
            parts.append(f"[green]{worklog_count} worklogs[/green]")
        console.print(" + ".join(parts))

    except JiraAuthenticationError:
        console.print("[red]Authentication failed. Check JIRA_EMAIL and JIRA_API_TOKEN[/red]")
        raise typer.Exit(1)
    except JiraRateLimitError:
        console.print("[red]Rate limited by Jira. Wait and try again.[/red]")
        raise typer.Exit(1)
    except JiraTimeoutError:
        console.print("[red]Request timed out. Check network and try again.[/red]")
        raise typer.Exit(1)
    except JiraServerError as e:
        console.print(f"[red]Jira server error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        raise typer.Exit(1)


@cli.command()
def issues():
    """List assigned issues from cache."""
    config = load_config()
    base_url = config.jira.base_url.rstrip("/") if config.jira.base_url else ""

    db = get_db()
    conn = db.connect()

    cursor = conn.execute(
        "SELECT key, summary, issue_type, status FROM issues_cache ORDER BY key"
    )
    rows = cursor.fetchall()
    db.close()

    if not rows:
        console.print("[yellow]No issues in cache. Run: lazytrack sync[/yellow]")
        return

    console.print(issues_table(rows, base_url))


@cli.command()
def status(
    week: str = typer.Option(None, "--week", "-w", help="Week in YYYY-Www format"),
):
    """Show worklog status for a week."""
    config = load_config()

    if week:
        try:
            week_start = parse_iso_week(week)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
    else:
        today = today_in_timezone(config.work.timezone)
        week_start = today - timedelta(days=today.weekday())

    db = get_db()
    calendar_repo = CalendarRepository(db.connect())

    leaves = calendar_repo.get_all_leaves()
    holidays = calendar_repo.get_all_holidays()
    overtime = calendar_repo.get_all_overtime()

    calendar = create_calendar(config)
    for leave in leaves:
        calendar.add_leave(leave)
    for holiday in holidays:
        calendar.add_holiday(holiday)
    for entry in overtime:
        calendar.add_overtime(entry)

    conn = db.connect()
    cursor = conn.execute(
        """
        SELECT work_date, SUM(seconds) as total_seconds
        FROM worklog_cache
        WHERE author = 'current_user'
        GROUP BY work_date
        """
    )
    worklog_hours = {}
    for row in cursor.fetchall():
        worklog_hours[date.fromisoformat(row["work_date"])] = Decimal(str(row["total_seconds"])) / 3600
    db.close()

    console.print(week_status_renderable(week_start, calendar, worklog_hours))


@leave_cmd.command("add")
def leave_add(
    date_str: str = typer.Argument(..., help="Date in YYYY-MM-DD format"),
    hours: float = typer.Option(8.0, "--hours", "-h", help="Hours of leave"),
    description: str = typer.Option(None, "--description", "-d", help="Leave description"),
):
    """Add a leave entry."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        console.print(f"[red]Invalid date format: {date_str}. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)

    db = get_db()
    repo = CalendarRepository(db.connect())

    leave = LeaveEntry(date=d, hours=Decimal(str(hours)), description=description)
    try:
        repo.add_leave(leave)
        console.print(f"[green]Leave added for {date_str}: {hours}h[/green]")
    except Exception as e:
        console.print(f"[red]Error adding leave: {e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


@leave_cmd.command("remove")
def leave_remove(
    date_str: str = typer.Argument(..., help="Date in YYYY-MM-DD format"),
):
    """Remove a leave entry."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        console.print(f"[red]Invalid date format: {date_str}. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)

    db = get_db()
    repo = CalendarRepository(db.connect())

    if repo.remove_leave(d):
        console.print(f"[green]Leave removed for {date_str}[/green]")
    else:
        console.print(f"[yellow]No leave entry found for {date_str}[/yellow]")
    db.close()


@holiday_cmd.command("add")
def holiday_add(
    date_str: str = typer.Argument(..., help="Date in YYYY-MM-DD format"),
    description: str = typer.Argument("", help="Holiday description"),
):
    """Add a holiday entry."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        console.print(f"[red]Invalid date format: {date_str}. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)

    db = get_db()
    repo = CalendarRepository(db.connect())

    holiday = HolidayEntry(date=d, description=description or None)
    try:
        repo.add_holiday(holiday)
        desc = f" ({description})" if description else ""
        console.print(f"[green]Holiday added for {date_str}{desc}[/green]")
    except Exception as e:
        console.print(f"[red]Error adding holiday: {e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


@holiday_cmd.command("remove")
def holiday_remove(
    date_str: str = typer.Argument(..., help="Date in YYYY-MM-DD format"),
):
    """Remove a holiday entry."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        console.print(f"[red]Invalid date format: {date_str}. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)

    db = get_db()
    repo = CalendarRepository(db.connect())

    if repo.remove_holiday(d):
        console.print(f"[green]Holiday removed for {date_str}[/green]")
    else:
        console.print(f"[yellow]No holiday entry found for {date_str}[/yellow]")
    db.close()


@plan_cmd.command("list")
def plan_list():
    """List all plans."""
    db = get_db()
    conn = db.connect()
    cursor = conn.execute(
        "SELECT id, created_at, status, summary FROM plans ORDER BY created_at DESC"
    )
    rows = cursor.fetchall()
    db.close()

    if not rows:
        console.print("[yellow]No plans found.[/yellow]")
        return

    table = Table(title="Plans")
    table.add_column("ID", style="cyan")
    table.add_column("Created", style="white")
    table.add_column("Status", style="yellow")
    table.add_column("Summary", style="green")

    for row in rows:
        table.add_row(row["id"], row["created_at"], row["status"], row["summary"] or "")

    console.print(table)


@plan_cmd.command("show")
def plan_show(
    plan_id: str = typer.Argument(..., help="Plan ID"),
):
    """Show plan details."""
    from lazytrack.application import PlanExecutor

    config = load_config()
    db = get_db()

    client = JiraClient(
        base_url=config.jira.base_url,
        email=config.jira.email,
        api_token=config.jira.api_token,
    )

    executor = PlanExecutor(db, config, client)
    plan = executor.load_plan(plan_id)

    if not plan:
        console.print(f"[red]Plan {plan_id} not found.[/red]")
        db.close()
        raise typer.Exit(1)

    console.print(f"\n[bold]Plan:[/bold] {plan.id}")
    console.print(f"[bold]Status:[/bold] {plan.status}")
    console.print(f"[bold]Created:[/bold] {plan.created_at}")
    console.print(f"[bold]Summary:[/bold] {plan.summary}")

    table = Table(title="Operations")
    table.add_column("ID", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Issue", style="white")
    table.add_column("Date", style="green")
    table.add_column("Hours", style="magenta")

    for op in plan.operations:
        hours = Decimal(str(op.seconds)) / 3600
        table.add_row(
            op.id,
            op.operation_type.value,
            op.issue_key,
            op.work_date.isoformat(),
            f"{hours}h",
        )

    console.print(table)
    db.close()


@cli.command()
def apply(
    plan_id: str = typer.Argument(..., help="Plan ID to apply"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without applying"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Apply a validated plan to Jira."""
    from lazytrack.application import PlanExecutor

    if not dry_run and not _proceed_remote_write(
        yes=yes, message=f"Submit plan {plan_id} to Jira?"
    ):
        return

    config = load_config()
    db = get_db()

    if dry_run:
        console.print(f"[yellow]Dry run: {plan_id}[/yellow]")

    client = JiraClient(
        base_url=config.jira.base_url,
        email=config.jira.email,
        api_token=config.jira.api_token,
    )

    executor = PlanExecutor(db, config, client)
    result = executor.execute_plan(plan_id, dry_run=dry_run)

    if result.status == "not_found":
        console.print(f"[red]Plan {plan_id} not found.[/red]")
        db.close()
        raise typer.Exit(1)

    if result.status == "invalid_status":
        console.print(f"[red]Plan {plan_id} is not in validated status.[/red]")
        db.close()
        raise typer.Exit(1)

    if result.status == "blocked_by_safety_config":
        console.print("[red]Operation blocked by safety configuration.[/red]")
        db.close()
        raise typer.Exit(1)

    if result.status == "dry_run_success":
        console.print(f"[green]Dry run successful. {result.operations_completed} operations would be executed.[/green]")
        db.close()
        return

    if result.status == "success":
        console.print(f"[green]Plan {plan_id} applied successfully.")
        console.print(f"  Operations completed: {result.operations_completed}[/green]")
    else:
        console.print(f"[red]Plan {plan_id} failed: {result.status}")
        console.print(f"  Completed: {result.operations_completed}")
        console.print(f"  Failed: {result.operations_failed}[/red]")
        for failed in result.failed_operations:
            console.print(f"    - {failed}")

    db.close()


@cli.command()
def audit(
    limit: int = typer.Option(50, "--limit", "-n", help="Number of entries"),
):
    """Show audit log."""
    db = get_db()
    from lazytrack.storage import AuditRepository
    repo = AuditRepository(db.connect())
    entries = repo.get_recent(limit=limit)
    db.close()

    if not entries:
        console.print("[yellow]No audit entries found.[/yellow]")
        return

    table = Table(title="Audit Log")
    table.add_column("Timestamp", style="cyan")
    table.add_column("Action", style="yellow")
    table.add_column("Entity", style="white")
    table.add_column("Entity ID", style="green")

    for entry in entries:
        table.add_row(
            entry["timestamp"],
            entry["action"],
            entry["entity_type"],
            entry["entity_id"] or "",
        )

    console.print(table)


@cli.command()
def undo(
    plan_id: str = typer.Argument(..., help="Plan ID to undo"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Undo an applied plan."""
    from lazytrack.application import UndoExecutor, UndoConflictError

    if not _proceed_remote_write(yes=yes, message=f"Undo plan {plan_id} in Jira?"):
        return

    config = load_config()
    db = get_db()

    client = JiraClient(
        base_url=config.jira.base_url,
        email=config.jira.email,
        api_token=config.jira.api_token,
    )

    executor = UndoExecutor(db, config, client)

    try:
        result = executor.undo_plan(plan_id)
    except UndoConflictError as e:
        console.print(f"[red]Undo conflict:[/red]")
        console.print(str(e))
        db.close()
        raise typer.Exit(1)

    if result.status == "not_found":
        console.print(f"[red]Plan {plan_id} not found.[/red]")
        db.close()
        raise typer.Exit(1)

    if "cannot_undo" in result.status:
        console.print(f"[red]{result.status}[/red]")
        db.close()
        raise typer.Exit(1)

    if result.status == "success":
        console.print(f"[green]Plan {plan_id} undone successfully.")
        console.print(f"  Operations reversed: {result.operations_completed}[/green]")
    else:
        console.print(f"[yellow]Partial undo: {result.status}")
        console.print(f"  Completed: {result.operations_completed}")
        console.print(f"  Failed: {result.operations_failed}[/yellow]")
        for failed in result.failed_operations:
            console.print(f"    - {failed}")

    db.close()


def _create_ai_provider(config):
    provider_name = config.ai.provider
    if provider_name == "gemini":
        api_key = config.ai.providers.gemini.api_key
        return GeminiProvider(api_key, model=config.ai.model or "gemini-1.5-flash")
    elif provider_name == "deepseek":
        api_key = config.ai.providers.deepseek.api_key
        return DeepSeekProvider(api_key, model=config.ai.model or "deepseek-chat")
    elif provider_name == "minimax":
        api_key = config.ai.providers.minimax.api_key
        return MiniMaxProvider(api_key, model=config.ai.model or "MiniMax-M2.7")
    else:
        raise typer.Exit(f"Unknown AI provider: {provider_name}")

def _format_response(response: dict, orchestrator: ChatOrchestrator) -> object:
    rtype = response.get("type")
    msg = response.get("message", "")

    if rtype == "success":
        return f"[green]{msg}[/green]"
    elif rtype == "error":
        return f"[red]{msg}[/red]"
    elif rtype == "info":
        return f"[yellow]{msg}[/yellow]"
    elif rtype == "clarification":
        return f"[yellow]{msg}[/yellow]\nMissing: {', '.join(response.get('missing', []))}"
    elif rtype == "plan_preview":
        preview = response.get("preview", "")
        if isinstance(preview, str):
            return f"[bold]Proposed plan preview:[/bold]\n{preview}"
        lines = ["[bold]Proposed plan preview:[/bold]"]
        for op in preview.get("operations", []):
            lines.append(f"  - {op.get('date')} {op.get('issue_key')}: {op.get('hours')}h")
        return "\n".join(lines)
    elif rtype == "week_status":
        return week_status_renderable(
            response["week_start"],
            orchestrator.calendar,
            response["logged_by_date"],
        )
    elif rtype == "issues_list":
        issues = response.get("issues", [])
        if not issues:
            return "[yellow]No issues found.[/yellow]"
        config = load_config()
        base_url = config.jira.base_url.rstrip("/") if config.jira.base_url else ""
        return issues_table(issues, base_url)
    else:
        return msg or str(response)

@cli.command()
def chat(debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug logging")):
    """Interactive assistant for issues, hours, and worklogs.

    Ask in natural language, or use slash commands that skip the AI:

      /help     what chat can do
      /status [YYYY-Www]
                current or selected week's hours
      /issues   assigned issues from cache
      /clear    reset conversation history
      /exit     quit

    Example: allocate 8 hours on SP-8412 today with a gap 12:00-13:00 WITA
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(levelname)s: %(message)s" if not debug else "%(levelname)s: %(message)s"
    )
    config = load_config()

    try:
        ai_provider = _create_ai_provider(config)
    except Exception as e:
        console.print(f"[red]AI provider error: {e}[/red]")
        raise typer.Exit(1)

    db = get_db()
    conn = db.connect()

    cursor = conn.execute("SELECT key, summary, issue_type, status, project_key FROM issues_cache")
    issues = []
    for row in cursor.fetchall():
        from lazytrack.jira.models import IssueSummary
        issues.append(IssueSummary(
            key=row["key"], summary=row["summary"], issue_type=row["issue_type"],
            status=row["status"], project_key=row["project_key"],
            assignee_is_current_user=True,
        ))

    cursor = conn.execute("SELECT id, issue_key, work_date, seconds, author FROM worklog_cache WHERE author = 'current_user'")
    worklogs = []
    for row in cursor.fetchall():
        from lazytrack.jira.models import WorklogEntry
        worklogs.append(WorklogEntry(
            id=str(row["id"]), issue_key=row["issue_key"],
            work_date=date.fromisoformat(row["work_date"]), seconds=row["seconds"],
            author_is_current_user=True, managed_by_lazytrack=bool(row["author"]),
        ))

    from lazytrack.storage import CalendarRepository
    calendar_repo = CalendarRepository(conn)
    leaves = calendar_repo.get_all_leaves()
    holidays = calendar_repo.get_all_holidays()
    overtime = calendar_repo.get_all_overtime()
    calendar = create_calendar(config)
    for leave in leaves:
        calendar.add_leave(leave)
    for holiday in holidays:
        calendar.add_holiday(holiday)
    for entry in overtime:
        calendar.add_overtime(entry)

    db.close()

    from lazytrack.domain import Planner, PlannerContext
    planner = Planner(PlannerContext(
        calendar=calendar,
        assigned_issues=issues,
        user_worklogs=worklogs,
        managed_worklogs=[],
    ), config)

    orchestrator = ChatOrchestrator(config, calendar, issues, worklogs, planner)

    print_chat_banner(console)
    console.print("[dim]Ask about issues, worklogs, allocating time, leave, and holidays[/dim]")
    console.print()

    if not issues:
        console.print("[yellow]No issues in cache. Run: lazytrack sync[/yellow]")
        console.print()

    suggestion = orchestrator.get_proactive_suggestion()
    if suggestion:
        console.print(f"[yellow]Suggestion: {suggestion}[/yellow]")
        console.print()

    history: list[tuple[str, str]] = []
    state = ChatSessionState()
    tz_name = config.work.timezone
    today = today_in_timezone(tz_name)
    issue_keys = [i.key for i in issues]
    session = PromptSession(history=FileHistory(str(chat_history_path())))

    while True:
        try:
            user_input = session.prompt("> ")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye![/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        local = parse_chat_command(user_input)
        if local == "exit":
            console.print("[dim]Goodbye![/dim]")
            break
        if local == "help":
            console.print(CHAT_HELP)
            continue
        if local == "clear":
            history = []
            state.clear()
            console.print("[dim]History cleared.[/dim]")
            continue

        try:
            if local == "status":
                try:
                    selected_week = parse_status_week(user_input)
                except ValueError as exc:
                    console.print(f"[red]{exc}[/red]")
                    continue
                output = _format_response(
                    orchestrator.handle_intent(ShowWeekIntent(week=selected_week)),
                    orchestrator,
                )
                console.print(output)
                continue
            if local == "issues":
                output = _format_response(
                    orchestrator.handle_intent(ShowIssuesIntent()),
                    orchestrator,
                )
                console.print(output)
                continue

            if state.pending_plan is not None:
                decision = confirmation_reply(user_input)
                if decision is True:
                    plan = state.pending_plan
                    apply_out = _apply_chat_plan(plan, config)
                    console.print(apply_out)
                    if str(apply_out).startswith("[green]"):
                        orchestrator.record_applied_plan(plan)
                    state.clear()
                    continue
                if decision is False:
                    state.clear()
                    console.print("[dim]Plan cancelled. Nothing was written to Jira.[/dim]")
                    continue
                effective_input = (
                    f"{state.pending_request or ''}\nCorrection: {user_input}".strip()
                )
                state.pending_plan = None
            elif state.pending_request:
                effective_input = (
                    f"{state.pending_request}\nClarification: {user_input}"
                )
            else:
                effective_input = user_input

            today = today_in_timezone(tz_name)
            request_tz = timezone_from_text(effective_input, tz_name)
            w_start, _ = cache_window(today, config.safety.worklog_lookback_weeks)
            w_end = today + timedelta(days=config.safety.max_plan_span_days)
            prompt = build_intent_prompt(
                effective_input,
                today=today,
                timezone=request_tz,
                hours_per_day=config.work.hours_per_day,
                issue_keys=issue_keys,
                history=history,
                window_start=w_start,
                window_end=w_end,
                max_plan_span_days=config.safety.max_plan_span_days,
            )
            ctx = ParseContext(
                today=today,
                timezone=request_tz,
                hours_per_day=config.work.hours_per_day,
                issue_keys=issue_keys,
                user_message=effective_input,
                window_start=w_start,
                window_end=w_end,
            )

            intent = asyncio.run(generate_structured_intent(ai_provider, prompt, ctx))
            response = orchestrator.handle_intent(intent, timezone=request_tz)
            output = _format_response(response, orchestrator)
            console.print(output)
            history.append(("user", user_input))
            if isinstance(output, str):
                history_text = output[:500]
            else:
                n = len(response.get("issues") or [])
                history_text = f"{n} issues" if n else "Assigned Issues"
            history.append(("assistant", history_text))
            history = history[-8:]

            if response.get("type") == "clarification":
                state.pending_request = effective_input
            elif response.get("type") == "plan_preview":
                state.pending_request = effective_input
                state.pending_plan = response["plan"]
            else:
                state.pending_request = None
        except AIAuthenticationError as e:
            console.print(f"[red]Auth error: {e}[/red]")
            console.print("[dim]Check your AI API key in .env[/dim]")
        except AIRateLimitError as e:
            console.print(f"[red]Rate limited: {e}[/red]")
        except AITimeoutError:
            console.print("[red]Sorry, the AI timed out. Please try again.[/red]")
        except AIError as e:
            logger = logging.getLogger(__name__)
            logger.debug("AI error", exc_info=True)
            if debug:
                console.print(f"[red]AI error: {e}[/red]")
            else:
                console.print("[red]Sorry, I couldn't process that. Please try again or rephrase.[/red]")
            console.print("[dim]Run with --debug for details[/dim]")
        except Exception as e:
            logging.getLogger(__name__).debug("Chat error", exc_info=True)
            if debug:
                console.print(f"[red]Error: {e}[/red]")
            else:
                console.print("[red]Sorry, something went wrong. Please try again.[/red]")
            console.print("[dim]Run with --debug for details[/dim]")


def _apply_chat_plan(plan, config) -> str:
    from lazytrack.application import PlanExecutor

    db = get_db()
    client = JiraClient(
        base_url=config.jira.base_url,
        email=config.jira.email,
        api_token=config.jira.api_token,
    )
    executor = PlanExecutor(db, config, client)
    executor.save_plan(plan)
    result = executor.execute_plan(plan.id)
    display_tz = executor.writer.display_timezone()
    db.close()
    if result.status == "success":
        note = ""
        if display_tz != config.work.timezone:
            note = f" Times are written in {display_tz}, your Jira profile timezone, so they display as planned."
        return f"[green]Applied {plan.id}: {result.operations_completed} worklog(s) written.{note}[/green]"
    if result.status == "blocked_by_safety_config":
        return "[red]Sorry, I can't apply that: blocked by safety configuration.[/red]"
    failed = "; ".join(result.failed_operations) if result.failed_operations else result.status
    return f"[red]Sorry, apply failed: {failed}[/red]"

def main():
    cli()


if __name__ == "__main__":
    main()
