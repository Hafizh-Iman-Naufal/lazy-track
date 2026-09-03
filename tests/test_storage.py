import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from lazytrack.domain import LeaveEntry, HolidayEntry, OvertimeEntry
from lazytrack.storage import (
    Database,
    CalendarRepository,
    PlanRepository,
    AuditRepository,
)


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path)
    db.initialize()
    yield db
    db.close()
    try:
        db_path.unlink(missing_ok=True)
    except PermissionError:
        pass


@pytest.fixture
def calendar_repo(temp_db):
    return CalendarRepository(temp_db.connect())


@pytest.fixture
def plan_repo(temp_db):
    return PlanRepository(temp_db.connect())


@pytest.fixture
def audit_repo(temp_db):
    return AuditRepository(temp_db.connect())


class TestDatabaseInitialization:
    def test_database_creates_tables(self, temp_db):
        conn = temp_db.connect()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row["name"] for row in cursor.fetchall()}
        assert "schema_version" in tables
        assert "calendar_exceptions" in tables
        assert "plans" in tables
        assert "audit_log" in tables

    def test_schema_version_recorded(self, temp_db):
        from lazytrack.storage.migrations import MIGRATIONS
        conn = temp_db.connect()
        cursor = conn.execute("SELECT MAX(version) as version FROM schema_version")
        row = cursor.fetchone()
        assert row["version"] == len(MIGRATIONS)


class TestCalendarRepository:
    def test_add_leave(self, calendar_repo):
        leave = LeaveEntry(
            date=date(2026, 9, 10),
            hours=Decimal("8"),
            description="Doctor appointment",
        )
        result = calendar_repo.add_leave(leave)
        assert result is True

        saved = calendar_repo.get_leave(date(2026, 9, 10))
        assert saved is not None
        assert saved.hours == Decimal("8")
        assert saved.description == "Doctor appointment"

    def test_add_duplicate_leave_updates(self, calendar_repo):
        leave1 = LeaveEntry(date=date(2026, 9, 10), hours=Decimal("8"))
        leave2 = LeaveEntry(date=date(2026, 9, 10), hours=Decimal("4"))

        calendar_repo.add_leave(leave1)
        calendar_repo.add_leave(leave2)

        saved = calendar_repo.get_leave(date(2026, 9, 10))
        assert saved.hours == Decimal("4")

    def test_remove_leave(self, calendar_repo):
        leave = LeaveEntry(date=date(2026, 9, 10), hours=Decimal("8"))
        calendar_repo.add_leave(leave)

        result = calendar_repo.remove_leave(date(2026, 9, 10))
        assert result is True

        saved = calendar_repo.get_leave(date(2026, 9, 10))
        assert saved is None

    def test_remove_nonexistent_leave(self, calendar_repo):
        result = calendar_repo.remove_leave(date(2026, 9, 10))
        assert result is False

    def test_add_holiday(self, calendar_repo):
        holiday = HolidayEntry(
            date=date(2026, 9, 17),
            description="Company Holiday",
        )
        result = calendar_repo.add_holiday(holiday)
        assert result is True

    def test_remove_holiday(self, calendar_repo):
        holiday = HolidayEntry(date=date(2026, 9, 17))
        calendar_repo.add_holiday(holiday)

        result = calendar_repo.remove_holiday(date(2026, 9, 17))
        assert result is True

    def test_get_all_leaves(self, calendar_repo):
        calendar_repo.add_leave(LeaveEntry(date=date(2026, 9, 1), hours=Decimal("8")))
        calendar_repo.add_leave(LeaveEntry(date=date(2026, 9, 10), hours=Decimal("4")))

        leaves = calendar_repo.get_all_leaves()
        assert len(leaves) == 2

    def test_get_all_holidays(self, calendar_repo):
        calendar_repo.add_holiday(HolidayEntry(date=date(2026, 9, 17)))
        calendar_repo.add_holiday(HolidayEntry(date=date(2026, 12, 25), description="Christmas"))

        holidays = calendar_repo.get_all_holidays()
        assert len(holidays) == 2


class TestPlanRepository:
    def test_save_and_get_plan(self, plan_repo):
        plan_repo.save_plan("PL-20260903-001", "draft", "Test plan")

        plan = plan_repo.get_plan("PL-20260903-001")
        assert plan is not None
        assert plan["status"] == "draft"
        assert plan["summary"] == "Test plan"

    def test_list_plans(self, plan_repo):
        plan_repo.save_plan("PL-001", "draft")
        plan_repo.save_plan("PL-002", "applied")
        plan_repo.save_plan("PL-003", "draft")

        all_plans = plan_repo.list_plans()
        assert len(all_plans) == 3

        draft_plans = plan_repo.list_plans(status="draft")
        assert len(draft_plans) == 2

    def test_update_plan_status(self, plan_repo):
        plan_repo.save_plan("PL-001", "draft")
        plan_repo.save_plan("PL-001", "applied", "Updated plan")

        plan = plan_repo.get_plan("PL-001")
        assert plan["status"] == "applied"
        assert plan["summary"] == "Updated plan"


class TestAuditRepository:
    def test_log_audit_entry(self, audit_repo):
        audit_repo.log(
            action="CREATE",
            entity_type="worklog",
            entity_id="12345",
            before=None,
            after={"issue_key": "ABC-123", "hours": 8},
            plan_id="PL-001",
        )

        entries = audit_repo.get_recent(limit=1)
        assert len(entries) == 1
        assert entries[0]["action"] == "CREATE"
        assert entries[0]["entity_type"] == "worklog"
        assert entries[0]["after_json"]["issue_key"] == "ABC-123"

    def test_get_recent_with_limit(self, audit_repo):
        for i in range(5):
            audit_repo.log(
                action=f"ACTION_{i}",
                entity_type="test",
            )

        entries = audit_repo.get_recent(limit=3)
        assert len(entries) == 3
