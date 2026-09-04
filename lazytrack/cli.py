import asyncio
import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import typer
from rich.console import Console
from rich.table import Table

from lazytrack import __version__
from lazytrack.config import load_config, redact_secrets
from lazytrack.domain import LeaveEntry, HolidayEntry, OvertimeEntry
from lazytrack.domain.calendar import create_calendar
from lazytrack.jira import (
    JiraClient,
    RestrictiveJiraGateway,
    JiraAuthenticationError,
    JiraRateLimitError,
    JiraTimeoutError,
    JiraServerError,
)
from lazytrack.storage import get_db, CalendarRepository

cli = typer.Typer(help="AI-assisted Jira worklog management CLI.")
config_cmd = typer.Typer()
cli.add_typer(config_cmd, name="config")

leave_cmd = typer.Typer()
cli.add_typer(leave_cmd, name="leave")

holiday_cmd = typer.Typer()
cli.add_typer(holiday_cmd, name="holiday")

overtime_cmd = typer.Typer()
cli.add_typer(overtime_cmd, name="overtime")

plan_cmd = typer.Typer()
cli.add_typer(plan_cmd, name="plan")

console = Console()


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
            today = date.today()
            start_of_week = today - timedelta(days=today.weekday())
            start_date = start_of_week - timedelta(weeks=4)

            if verbose:
                console.print(f"[dim]Syncing worklogs from {start_date} to {today}...[/dim]")

            async def do_sync_worklogs():
                return await gateway.get_worklogs_for_user(start_date, today)

            worklogs = asyncio.run(do_sync_worklogs())

            for wl in worklogs:
                if wl.author_is_current_user:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO worklog_cache 
                        (id, issue_key, work_date, seconds, author, managed_by_lazytrack)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (wl.id, wl.issue_key, wl.work_date.isoformat(), wl.seconds, "current_user", 0),
                    )
                    worklog_count += 1

            conn.commit()

            if verbose:
                console.print(f"[dim]Synced:[/dim] [cyan]{worklog_count}[/cyan] worklogs")

        db.close()

        parts = [f"[green]Synced {len(issues)} issues[/green]"]
        if sync_worklogs and worklog_count > 0:
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

    table = Table(title="Assigned Issues")
    table.add_column("Key", style="cyan")
    table.add_column("Summary", style="white")
    table.add_column("Type", style="green")
    table.add_column("Status", style="yellow")

    for row in rows:
        table.add_row(row["key"], row["summary"], row["issue_type"], row["status"])

    console.print(table)
    console.print(f"\n{len(rows)} issues total")


@cli.command()
def status(
    week: str = typer.Option(None, "--week", "-w", help="Week in YYYY-Www format"),
):
    """Show worklog status for a week."""
    config = load_config()

    if week:
        try:
            year, week_num = week.split("-W")
            year, week_num = int(year), int(week_num)
            week_start = date.fromisocalendar(year, week_num, 1)
        except (ValueError, AttributeError):
            console.print(f"[red]Invalid week format: {week}. Use YYYY-Www[/red]")
            raise typer.Exit(1)
    else:
        today = date.today()
        week_start = today - timedelta(days=today.weekday())

    week_end = week_start + timedelta(days=6)

    db = get_db()
    calendar_repo = CalendarRepository(db.connect())

    leaves = calendar_repo.get_all_leaves()
    holidays = calendar_repo.get_all_holidays()

    calendar = create_calendar(config)
    for leave in leaves:
        calendar.add_leave(leave)
    for holiday in holidays:
        calendar.add_holiday(holiday)

    weekly = calendar.required_hours_for_week(week_start)

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

    total_logged = sum(
        hours for d, hours in worklog_hours.items()
        if week_start <= d <= week_end
    )

    table = Table(title=f"Week: {week_start} - {week_end}")
    table.add_column("Day", style="cyan")
    table.add_column("Date", style="white")
    table.add_column("Expected", style="yellow")
    table.add_column("Logged", style="green")

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i in range(7):
        d = week_start + timedelta(days=i)
        cap = calendar.capacity_for_date(d)
        logged = worklog_hours.get(d, Decimal("0"))
        table.add_row(
            day_names[i],
            d.isoformat(),
            f"{cap}h",
            f"{logged}h",
        )

    console.print(table)

    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Required:  {weekly.required_target}h")
    console.print(f"  Logged:    {total_logged}h")
    console.print(f"  Missing:   {max(Decimal('0'), weekly.required_target - total_logged)}h")


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


@overtime_cmd.command("add")
def overtime_add(
    date_str: str = typer.Argument(..., help="Date in YYYY-MM-DD format"),
    hours: float = typer.Argument(..., help="Overtime hours"),
    description: str = typer.Option(None, "--description", "-d", help="Overtime description"),
):
    """Add an overtime entry."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        console.print(f"[red]Invalid date format: {date_str}. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)

    if hours < 0:
        console.print("[red]Overtime hours cannot be negative[/red]")
        raise typer.Exit(1)

    db = get_db()
    repo = CalendarRepository(db.connect())

    ot = OvertimeEntry(date=d, hours=Decimal(str(hours)), description=description)
    try:
        repo.conn.execute(
            """
            INSERT INTO calendar_exceptions (date, exception_type, hours, description)
            VALUES (?, ?, ?, ?)
            """,
            (d.isoformat(), "overtime", float(ot.hours), ot.description),
        )
        repo.conn.commit()
        console.print(f"[green]Overtime added for {date_str}: {hours}h[/green]")
    except sqlite3.IntegrityError:
        repo.conn.execute(
            """
            UPDATE calendar_exceptions SET hours = ?, description = ?
            WHERE date = ? AND exception_type = 'overtime'
            """,
            (float(ot.hours), ot.description, d.isoformat()),
        )
        repo.conn.commit()
        console.print(f"[green]Overtime updated for {date_str}: {hours}h[/green]")
    except Exception as e:
        console.print(f"[red]Error adding overtime: {e}[/red]")
        raise typer.Exit(1)
    finally:
        db.close()


@overtime_cmd.command("remove")
def overtime_remove(
    date_str: str = typer.Argument(..., help="Date in YYYY-MM-DD format"),
):
    """Remove an overtime entry."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        console.print(f"[red]Invalid date format: {date_str}. Use YYYY-MM-DD.[/red]")
        raise typer.Exit(1)

    db = get_db()
    repo = CalendarRepository(db.connect())

    cursor = repo.conn.execute(
        "DELETE FROM calendar_exceptions WHERE date = ? AND exception_type = 'overtime'",
        (d.isoformat(),),
    )
    repo.conn.commit()

    if cursor.rowcount > 0:
        console.print(f"[green]Overtime removed for {date_str}[/green]")
    else:
        console.print(f"[yellow]No overtime entry found for {date_str}[/yellow]")
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
):
    """Apply a validated plan to Jira."""
    from lazytrack.application import PlanExecutor

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
):
    """Undo an applied plan."""
    from lazytrack.application import UndoExecutor, UndoConflictError

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


def main():
    cli()


if __name__ == "__main__":
    main()
