from datetime import date
from decimal import Decimal

import pytest
from rich.console import Console

from lazytrack.ai.schemas import AllocateTimeIntent, ShowWeekIntent, WorklogItem
from lazytrack.cli import _format_response
from lazytrack.config import LazyTrackConfig
from lazytrack.domain import Planner, PlannerContext
from lazytrack.domain.calendar import WorkCalendar
from lazytrack.ui.chat import (
    ChatOrchestrator,
    ChatSessionState,
    confirmation_reply,
    parse_chat_command,
    parse_status_week,
)


def test_help_and_clear_are_local():
    assert parse_chat_command("/help") == "help"
    assert parse_chat_command("help") == "help"
    assert parse_chat_command("?") == "help"
    assert parse_chat_command("/clear") == "clear"
    assert parse_chat_command("clear") == "clear"


def test_exit_aliases():
    assert parse_chat_command("/exit") == "exit"
    assert parse_chat_command("quit") == "exit"
    assert parse_chat_command("q") == "exit"


def test_status_and_issues_are_local():
    assert parse_chat_command("/status") == "status"
    assert parse_chat_command("/status 2026-W36") == "status"
    assert parse_chat_command("/issues") == "issues"


def test_status_week_argument():
    assert parse_status_week("/status") is None
    assert parse_status_week("/status 2026-W36") == "2026-W36"
    with pytest.raises(ValueError, match="YYYY-Www"):
        parse_status_week("/status 2026-W99")
    with pytest.raises(ValueError, match="Usage"):
        parse_status_week("/status 2026-W36 extra")


def test_natural_language_is_not_a_slash_command():
    assert parse_chat_command("allocate 8 hours on SP-8412") is None
    assert parse_chat_command("help me allocate SP-8412") is None


def test_pending_plan_requires_unambiguous_confirmation():
    assert confirmation_reply("yes") is True
    assert confirmation_reply("apply") is True
    assert confirmation_reply("no") is False
    assert confirmation_reply("make it start at 11") is None


def test_over_capacity_request_previews_derived_overtime():
    from datetime import timedelta
    from lazytrack.jira.models import IssueSummary

    config = LazyTrackConfig()
    calendar = WorkCalendar(config=config.work)
    issue = IssueSummary(
        key="SP-1",
        summary="Story",
        issue_type="Story",
        status="In Progress",
        project_key="SP",
    )
    planner = Planner(PlannerContext(calendar, [issue], [], []), config)
    orch = ChatOrchestrator(config, calendar, [issue], [], planner)
    work_date = date.today()
    if work_date.weekday() >= 5:
        work_date -= timedelta(days=work_date.weekday() - 4)

    result = orch.handle_intent(
        AllocateTimeIntent(
            worklogs=[WorklogItem(date=work_date, issue_key="SP-1", hours=9)]
        )
    )

    assert result["type"] == "plan_preview"
    assert result["plan"].overtime[0].hours == Decimal("1")
    assert "Overtime to register" in result["preview"]


def test_chat_state_clear_discards_request_and_plan():
    state = ChatSessionState(pending_request="log time", pending_plan=object())
    state.clear()
    assert state.pending_request is None
    assert state.pending_plan is None


def test_slash_status_uses_show_week_intent():
    config = LazyTrackConfig()
    calendar = WorkCalendar(config=config.work)
    planner = Planner(
        PlannerContext(
            calendar=calendar,
            assigned_issues=[],
            user_worklogs=[],
            managed_worklogs=[],
        ),
        config,
    )
    orch = ChatOrchestrator(config, calendar, [], [], planner)
    assert parse_chat_command("/status") == "status"
    result = orch.handle_intent(ShowWeekIntent())
    assert result["type"] == "week_status"
    assert result["logged"] == Decimal("0")
    assert isinstance(result["week_start"], date)
    out = _format_response(result, orch)
    console = Console(record=True, width=100, color_system=None)
    console.print(out)
    text = console.export_text()
    assert f"Week {result['week_start'].isocalendar().year}-W{result['week_start'].isocalendar().week:02d}" in text
    assert "Required" in text
    assert "Logged" in text
    assert "Mon" in text
    assert "Sun" in text


def test_selected_week_status_includes_all_dates_and_daily_logged():
    from lazytrack.jira.models import WorklogEntry

    config = LazyTrackConfig()
    calendar = WorkCalendar(config=config.work)
    worklogs = [
        WorklogEntry(
            id="1",
            issue_key="SP-1",
            work_date=date(2026, 9, 1),
            seconds=30600,
            author_is_current_user=True,
        )
    ]
    planner = Planner(
        PlannerContext(
            calendar=calendar,
            assigned_issues=[],
            user_worklogs=worklogs,
            managed_worklogs=[],
        ),
        config,
    )
    orch = ChatOrchestrator(config, calendar, [], worklogs, planner)

    result = orch.handle_intent(ShowWeekIntent(week="2026-W36"))
    console = Console(record=True, width=100, color_system=None)
    console.print(_format_response(result, orch))
    text = console.export_text()

    assert "Week 2026-W36: 2026-08-31 - 2026-09-06" in text
    assert "2026-08-31" in text
    assert "2026-09-06" in text
    assert "8.5h" in text


def test_print_chat_banner_includes_chat_and_version():
    from rich.console import Console

    from lazytrack.ui.banner import print_chat_banner

    console = Console(record=True, width=80, color_system=None)
    print_chat_banner(console)
    text = console.export_text()
    assert "chat" in text
    assert "0.1.0-dev" in text


def test_confirm_apply_to_jira_defaults_false(monkeypatch):
    from lazytrack import cli

    captured = {}

    class FakePrompt:
        def execute(self):
            return False

    def fake_confirm(*, message, default):
        captured["message"] = message
        captured["default"] = default
        return FakePrompt()

    monkeypatch.setattr(cli.inquirer, "confirm", fake_confirm)
    assert cli._confirm_apply_to_jira() is False
    assert captured["default"] is False
    assert captured["message"] == "Apply this to Jira?"


def test_proceed_remote_write_yes_skips_inquirer(monkeypatch):
    from lazytrack import cli

    def boom(**kwargs):
        raise AssertionError("confirm should not run when --yes")

    monkeypatch.setattr(cli.inquirer, "confirm", boom)
    assert cli._proceed_remote_write(yes=True, message="Submit plan X to Jira?") is True


def test_proceed_remote_write_non_tty_exits(monkeypatch):
    import typer
    from lazytrack import cli

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    def boom(**kwargs):
        raise AssertionError("confirm should not run without a TTY")

    monkeypatch.setattr(cli.inquirer, "confirm", boom)
    with pytest.raises(typer.Exit) as exc:
        cli._proceed_remote_write(yes=False, message="Submit plan X to Jira?")
    assert exc.value.exit_code == 1


def test_proceed_remote_write_tty_cancel(monkeypatch):
    from lazytrack import cli

    class FakePrompt:
        def execute(self):
            return False

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.inquirer, "confirm", lambda **kwargs: FakePrompt())
    assert cli._proceed_remote_write(yes=False, message="Undo plan X in Jira?") is False


def test_chat_uses_prompt_session_history():
    import inspect
    from pathlib import Path

    from lazytrack.cli import chat
    from lazytrack.ui.chat import chat_history_path

    source = inspect.getsource(chat)
    assert "PromptSession" in source
    assert "FileHistory" in source
    assert "inquirer.text" not in source
    assert chat_history_path() == Path.home() / ".lazytrack" / "chat_history"


def test_issues_table_sorts_all_rows_and_links():
    from rich.console import Console
    from rich.table import Table

    from lazytrack.jira.models import IssueSummary
    from lazytrack.ui.issues import issues_table

    issues = [
        IssueSummary(
            key=f"SP-{i:04d}",
            summary=f"summary {i}",
            issue_type="Task",
            status="Done",
            project_key="SP",
        )
        for i in range(21, 0, -1)
    ]
    table = issues_table(issues, "https://example.atlassian.net")
    assert isinstance(table, Table)
    assert table.row_count == 21
    assert table.caption == "21 issues total"

    console = Console(record=True, force_terminal=True, width=120)
    console.print(table)
    html = console.export_html()
    assert "browse/SP-0001" in html
    assert "... and" not in console.export_text()


def test_format_response_issues_list_is_full_table(monkeypatch):
    from rich.table import Table

    from lazytrack.cli import _format_response
    from lazytrack.config import LazyTrackConfig
    from lazytrack.jira.models import IssueSummary

    cfg = LazyTrackConfig()
    cfg.jira.base_url = "https://example.atlassian.net"
    monkeypatch.setattr("lazytrack.cli.load_config", lambda: cfg)

    issues = [
        IssueSummary(
            key=f"SP-{i}",
            summary="x",
            issue_type="Story",
            status="To Do",
            project_key="SP",
        )
        for i in range(21)
    ]
    out = _format_response({"type": "issues_list", "issues": issues}, None)
    assert isinstance(out, Table)
    assert out.row_count == 21
    assert out.caption == "21 issues total"
