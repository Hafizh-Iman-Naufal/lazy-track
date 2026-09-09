from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from lazytrack.ai.normalize import parse_followup_date
from lazytrack.ai.schemas import (
    AllocationItem,
    AddHolidayIntent,
    AddLeaveIntent,
    AllocateTimeIntent,
    ClarificationRequired,
    RemoveAllocationIntent,
    RemoveHolidayIntent,
    RemoveLeaveIntent,
    ReallocateTimeIntent,
    ShowIssuesIntent,
    ShowWeekIntent,
)
from lazytrack.config import LazyTrackConfig
from lazytrack.domain import (
    Allocation,
    AllocationRequest,
    Planner,
    PlannerContext,
    WorklogAllocation,
    ValidationError,
    LeaveEntry,
    HolidayEntry,
    OvertimeEntry,
)
from lazytrack.domain.calendar import WorkCalendar
from lazytrack.domain.planner import parse_hhmm
from lazytrack.domain.tz import now_in_zone
from lazytrack.jira.models import IssueSummary, WorklogEntry
from lazytrack.storage import CalendarRepository
from lazytrack.ui.status import iso_week_id, parse_iso_week

CHAT_HELP = """\
[bold]LazyTrack Chat[/bold]
Allocate time (gaps and timezones), show issues or this week, and add leave or holidays.
Plans preview first. Reply yes to apply, no to cancel, or describe a correction.
Leave can cover several dates in one request.

[bold]Slash commands[/bold] (no AI):
  /help     this message
  /status [YYYY-Www]
            current or selected week's hours
  /issues   assigned issues from cache
  /clear    reset conversation history
  /exit     quit

Up/down recalls previous lines (saved in ~/.lazytrack).
Also: help, ?, exit, quit, q
Sync issues with [cyan]lazytrack sync[/cyan] in another terminal, then restart chat.\
"""


def chat_history_path() -> Path:
    path = Path.home() / ".lazytrack" / "chat_history"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_SLASH = {
    "help": "help",
    "?": "help",
    "status": "status",
    "issues": "issues",
    "clear": "clear",
    "exit": "exit",
    "quit": "exit",
    "q": "exit",
}


def parse_chat_command(line: str) -> Optional[str]:
    """Return a local command name, or None to send the line to the AI."""
    raw = line.strip()
    if not raw:
        return None
    low = raw.lower()
    if low.startswith("/"):
        token = low[1:].split()[0] if low[1:] else ""
        return _SLASH.get(token)
    if " " in low:
        return None
    return _SLASH.get(low)


def _parse_gap(start: str, end: str) -> tuple[time, time]:
    return parse_hhmm(start), parse_hhmm(end)


@dataclass
class ChatSessionState:
    pending_request: Optional[str] = None
    pending_plan: object = None

    def clear(self) -> None:
        self.pending_request = None
        self.pending_plan = None


def bind_pending_from_response(
    state: ChatSessionState, response: dict, effective_input: str
) -> None:
    if response.get("type") == "plan_preview":
        state.pending_request = effective_input
        state.pending_plan = response["plan"]
        return
    if response.get("type") == "clarification" and response.get("missing"):
        state.pending_request = effective_input
        return
    if state.pending_plan is None:
        state.pending_request = None


def merge_date_followup(pending: str, user_input: str) -> str:
    return f"{pending}\nClarification: {user_input}"


def effective_user_input(
    state: ChatSessionState, user_input: str, *, today: date
) -> str:
    if state.pending_plan is not None:
        return user_input
    if state.pending_request and parse_followup_date(user_input, today):
        return merge_date_followup(state.pending_request, user_input)
    return user_input


def confirmation_reply(text: str) -> Optional[bool]:
    value = text.strip().lower()
    if value in {"y", "yes", "confirm", "apply"}:
        return True
    if value in {"n", "no", "cancel"}:
        return False
    return None


def parse_status_week(line: str) -> Optional[str]:
    parts = line.strip().split()
    if not parts or parts[0].lower() != "/status":
        return None
    if len(parts) == 1:
        return None
    if len(parts) != 2:
        raise ValueError("Usage: /status [YYYY-Www]")
    parse_iso_week(parts[1])
    return parts[1]


def logged_hours_by_date(
    worklogs: list[WorklogEntry], week_start: date
) -> dict[date, Decimal]:
    totals = {week_start + timedelta(days=i): Decimal("0") for i in range(7)}
    for worklog in worklogs:
        if worklog.work_date in totals:
            totals[worklog.work_date] += Decimal(str(worklog.seconds)) / 3600
    return totals


def suggest_missing_hours(
    week_start: date,
    calendar: WorkCalendar,
    logged_by_date: dict[date, Decimal],
) -> Optional[str]:
    weekly = calendar.required_hours_for_week(week_start)
    total_logged = sum(
        (logged_by_date.get(week_start + timedelta(days=i), Decimal("0")) for i in range(7)),
        Decimal("0"),
    )
    missing = weekly.required_target + weekly.total_overtime - total_logged

    if missing <= 0:
        return None

    missing_dates = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        if calendar.capacity_for_date(d) > logged_by_date.get(d, Decimal("0")):
            missing_dates.append(d.strftime("%A"))

    if missing_dates:
        date_list = ", ".join(missing_dates)
        return (
            f"You still have {missing}h unallocated in {iso_week_id(week_start)}. "
            f"{date_list} need hours. "
            f"Would you like to allocate them?"
        )
    return None


class ChatOrchestrator:
    def __init__(
        self,
        config: LazyTrackConfig,
        calendar: WorkCalendar,
        issues: list[IssueSummary],
        worklogs: list[WorklogEntry],
        planner: Planner,
        calendar_repo: Optional[CalendarRepository] = None,
    ):
        self.config = config
        self.calendar = calendar
        self.issues = issues
        self.worklogs = worklogs
        self.planner = planner
        self.calendar_repo = calendar_repo

    def handle_intent(self, intent, timezone: Optional[str] = None) -> dict:
        if isinstance(intent, AllocateTimeIntent):
            return self._handle_allocate(intent, timezone)
        elif isinstance(intent, ShowWeekIntent):
            return self._handle_show_week(intent)
        elif isinstance(intent, ShowIssuesIntent):
            return self._handle_show_issues(intent)
        elif isinstance(intent, AddLeaveIntent):
            return self._handle_add_leave(intent)
        elif isinstance(intent, RemoveLeaveIntent):
            return self._handle_remove_leave(intent)
        elif isinstance(intent, AddHolidayIntent):
            return self._handle_add_holiday(intent)
        elif isinstance(intent, RemoveHolidayIntent):
            return self._handle_remove_holiday(intent)
        elif isinstance(intent, ClarificationRequired):
            return {
                "type": "clarification",
                "message": intent.reason,
                "missing": intent.missing_fields,
            }
        else:
            return {
                "type": "error",
                "message": f"Unsupported intent type: {type(intent)}",
            }

    def get_proactive_suggestion(self) -> Optional[str]:
        today = now_in_zone(self.config.work.timezone).date()
        week_start = today - timedelta(days=today.weekday())
        return suggest_missing_hours(
            week_start,
            self.calendar,
            logged_hours_by_date(self.worklogs, week_start),
        )

    def _handle_allocate(self, intent: AllocateTimeIntent, timezone: Optional[str] = None) -> dict:
        allocations = [
            Allocation(
                issue_key=a.issue_key,
                hours_per_day=Decimal(str(a.hours_per_day)),
            )
            for a in intent.allocations
        ]

        worklogs = [
            WorklogAllocation(
                work_date=item.date,
                issue_key=item.issue_key,
                hours=Decimal(str(item.hours)),
                start_time=parse_hhmm(item.start_time) if item.start_time else None,
            )
            for item in intent.worklogs
        ]
        dates = [item.work_date for item in worklogs]
        request = AllocationRequest(
            start_date=min(dates) if dates else intent.start_date,
            end_date=max(dates) if dates else intent.end_date,
            allocations=allocations,
            gaps=[_parse_gap(g.start, g.end) for g in intent.gaps],
            timezone=timezone or self.config.work.timezone,
            worklogs=worklogs,
        )

        try:
            plan = self.planner.create_plan(request)
            preview = self.planner.preview_plan(plan)
            return {
                "type": "plan_preview",
                "plan": plan,
                "preview": preview,
                "message": "Proposed allocation",
            }
        except ValidationError as e:
            return {
                "type": "clarification",
                "message": f"{e}. What should I use instead?",
                "missing": [],
            }
        except Exception as e:
            return {
                "type": "error",
                "message": str(e),
            }

    def record_applied_plan(self, plan) -> None:
        for op in plan.operations:
            worklog = WorklogEntry(
                id=f"{plan.id}:{op.id}",
                issue_key=op.issue_key,
                work_date=op.work_date,
                seconds=op.seconds,
                author_is_current_user=True,
                managed_by_lazytrack=True,
                plan_id=plan.id,
            )
            self.worklogs.append(worklog)
            if self.planner.context.user_worklogs is not self.worklogs:
                self.planner.context.user_worklogs.append(worklog)
        for overtime in plan.overtime:
            current = self.calendar.overtime.get(overtime.date)
            hours = overtime.hours + (current.hours if current else Decimal("0"))
            self.calendar.add_overtime(OvertimeEntry(date=overtime.date, hours=hours))

    def _handle_show_week(self, intent: ShowWeekIntent) -> dict:
        week_ids = list(intent.weeks)
        if intent.week and intent.week not in week_ids:
            week_ids.insert(0, intent.week)
        if not week_ids:
            today = now_in_zone(self.config.work.timezone).date()
            week_start = today - timedelta(days=today.weekday())
            week_ids = [iso_week_id(week_start)]

        week_starts = []
        logged_by_weeks = []
        for week_id in week_ids:
            try:
                week_start = parse_iso_week(week_id)
            except ValueError:
                return {
                    "type": "error",
                    "message": f"Invalid week format: {week_id}. Use YYYY-Www",
                }
            week_starts.append(week_start)
            logged_by_weeks.append(logged_hours_by_date(self.worklogs, week_start))

        week_start = week_starts[0]
        logged_by_date = logged_by_weeks[0]
        weekly = self.calendar.required_hours_for_week(week_start)
        logged = sum(logged_by_date.values(), Decimal("0"))
        return {
            "type": "week_status",
            "week_start": week_start,
            "week_starts": week_starts,
            "weekly": weekly,
            "logged_by_date": logged_by_date,
            "logged_by_weeks": logged_by_weeks,
            "logged": logged,
            "missing": max(
                Decimal("0"),
                weekly.required_target + weekly.total_overtime - logged,
            ),
        }

    def _handle_show_issues(self, intent: ShowIssuesIntent) -> dict:
        return {
            "type": "issues_list",
            "issues": self.issues,
        }

    def _handle_add_leave(self, intent: AddLeaveIntent) -> dict:
        hours = (
            Decimal(str(intent.hours))
            if intent.hours is not None
            else Decimal(str(self.config.work.hours_per_day))
        )
        dates = list(intent.dates)
        for work_date in dates:
            leave = LeaveEntry(date=work_date, hours=hours)
            self.calendar.add_leave(leave)
            if self.calendar_repo:
                self.calendar_repo.add_leave(leave)
        listed = ", ".join(d.isoformat() for d in dates)
        return {
            "type": "success",
            "message": f"Leave added for {listed}: {hours}h",
        }

    def _handle_remove_leave(self, intent: RemoveLeaveIntent) -> dict:
        days = list(intent.dates)
        removed_days = []
        for work_date in days:
            removed = self.calendar.remove_leave(work_date)
            if self.calendar_repo:
                removed = self.calendar_repo.remove_leave(work_date) or removed
            if removed:
                removed_days.append(work_date)
        listed = ", ".join(d.isoformat() for d in (removed_days or days))
        if removed_days:
            return {
                "type": "success",
                "message": f"Leave removed for {listed}",
            }
        return {
            "type": "info",
            "message": f"No leave entry found for {listed}",
        }

    def _handle_add_holiday(self, intent: AddHolidayIntent) -> dict:
        days = list(intent.dates)
        for work_date in days:
            holiday = HolidayEntry(date=work_date, description=intent.description)
            self.calendar.add_holiday(holiday)
            if self.calendar_repo:
                self.calendar_repo.add_holiday(holiday)
        listed = ", ".join(d.isoformat() for d in days)
        desc = f" ({intent.description})" if intent.description else ""
        return {
            "type": "success",
            "message": f"Holiday added for {listed}{desc}",
        }

    def _handle_remove_holiday(self, intent: RemoveHolidayIntent) -> dict:
        days = list(intent.dates)
        removed_days = []
        for work_date in days:
            removed = self.calendar.remove_holiday(work_date)
            if self.calendar_repo:
                removed = self.calendar_repo.remove_holiday(work_date) or removed
            if removed:
                removed_days.append(work_date)
        listed = ", ".join(d.isoformat() for d in (removed_days or days))
        if removed_days:
            return {
                "type": "success",
                "message": f"Holiday removed for {listed}",
            }
        return {
            "type": "info",
            "message": f"No holiday entry found for {listed}",
        }

