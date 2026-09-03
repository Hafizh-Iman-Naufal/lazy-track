from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from lazytrack.ai.schemas import (
    AllocationItem,
    AddHolidayIntent,
    AddLeaveIntent,
    AddOvertimeIntent,
    AllocateTimeIntent,
    ClarificationRequired,
    RemoveAllocationIntent,
    RemoveHolidayIntent,
    RemoveLeaveIntent,
    RemoveOvertimeIntent,
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
    LeaveEntry,
    HolidayEntry,
    OvertimeEntry,
)
from lazytrack.domain.calendar import WorkCalendar
from lazytrack.jira.models import IssueSummary, WorklogEntry


def format_week_status(week_start: date, weekly, logged: Decimal) -> str:
    lines = [
        f"Week: {week_start} - {week_start + timedelta(days=4)}",
        "",
        f"Required:   {weekly.required_target}h",
        f"Logged:     {logged}h",
        f"Unfilled:   {weekly.missing_hours}h",
    ]
    return "\n".join(lines)


def suggest_missing_hours(week_start: date, calendar: WorkCalendar) -> Optional[str]:
    missing = calendar.required_hours_for_week(week_start)

    if missing.missing_hours <= 0:
        return None

    missing_dates = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        cap = calendar.capacity_for_date(d)
        if cap > 0:
            missing_dates.append(d.strftime("%A"))

    if missing_dates:
        date_list = ", ".join(missing_dates)
        return (
            f"You still have {missing.missing_hours}h unallocated this week. "
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
    ):
        self.config = config
        self.calendar = calendar
        self.issues = issues
        self.worklogs = worklogs
        self.planner = planner

    def handle_intent(self, intent) -> dict:
        if isinstance(intent, AllocateTimeIntent):
            return self._handle_allocate(intent)
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
        elif isinstance(intent, AddOvertimeIntent):
            return self._handle_add_overtime(intent)
        elif isinstance(intent, RemoveOvertimeIntent):
            return self._handle_remove_overtime(intent)
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
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        return suggest_missing_hours(week_start, self.calendar)

    def _handle_allocate(self, intent: AllocateTimeIntent) -> dict:
        allocations = [
            Allocation(
                issue_key=a.issue_key,
                hours_per_day=Decimal(str(a.hours_per_day)),
            )
            for a in intent.allocations
        ]

        request = AllocationRequest(
            start_date=intent.start_date,
            end_date=intent.end_date,
            allocations=allocations,
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
        except Exception as e:
            return {
                "type": "error",
                "message": str(e),
            }

    def _handle_show_week(self, intent: ShowWeekIntent) -> dict:
        if intent.week:
            try:
                year, week_num = intent.week.split("-W")
                week_start = date.fromisocalendar(int(year), int(week_num), 1)
            except (ValueError, AttributeError):
                return {
                    "type": "error",
                    "message": f"Invalid week format: {intent.week}. Use YYYY-Www",
                }
        else:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())

        weekly = self.calendar.required_hours_for_week(week_start)
        logged = sum(
            Decimal(str(w.seconds)) / 3600
            for w in self.worklogs
            if week_start <= w.work_date <= week_start + timedelta(days=6)
        )

        return {
            "type": "week_status",
            "week_start": week_start,
            "weekly": weekly,
            "logged": logged,
            "missing": weekly.missing_hours,
        }

    def _handle_show_issues(self, intent: ShowIssuesIntent) -> dict:
        return {
            "type": "issues_list",
            "issues": self.issues,
        }

    def _handle_add_leave(self, intent: AddLeaveIntent) -> dict:
        hours = Decimal(str(intent.hours)) if intent.hours else Decimal("8")
        leave = LeaveEntry(date=intent.date, hours=hours)
        self.calendar.add_leave(leave)
        return {
            "type": "success",
            "message": f"Leave added for {intent.date}: {hours}h",
        }

    def _handle_remove_leave(self, intent: RemoveLeaveIntent) -> dict:
        removed = self.calendar.remove_leave(intent.date)
        if removed:
            return {
                "type": "success",
                "message": f"Leave removed for {intent.date}",
            }
        return {
            "type": "info",
            "message": f"No leave entry found for {intent.date}",
        }

    def _handle_add_holiday(self, intent: AddHolidayIntent) -> dict:
        holiday = HolidayEntry(date=intent.date, description=intent.description)
        self.calendar.add_holiday(holiday)
        desc = f" ({intent.description})" if intent.description else ""
        return {
            "type": "success",
            "message": f"Holiday added for {intent.date}{desc}",
        }

    def _handle_remove_holiday(self, intent: RemoveHolidayIntent) -> dict:
        removed = self.calendar.remove_holiday(intent.date)
        if removed:
            return {
                "type": "success",
                "message": f"Holiday removed for {intent.date}",
            }
        return {
            "type": "info",
            "message": f"No holiday entry found for {intent.date}",
        }

    def _handle_add_overtime(self, intent: AddOvertimeIntent) -> dict:
        ot = OvertimeEntry(date=intent.date, hours=Decimal(str(intent.hours)))
        self.calendar.add_overtime(ot)
        return {
            "type": "success",
            "message": f"Overtime added for {intent.date}: {intent.hours}h",
        }

    def _handle_remove_overtime(self, intent: RemoveOvertimeIntent) -> dict:
        removed = self.calendar.remove_overtime(intent.date)
        if removed:
            return {
                "type": "success",
                "message": f"Overtime removed for {intent.date}",
            }
        return {
            "type": "info",
            "message": f"No overtime entry found for {intent.date}",
        }
