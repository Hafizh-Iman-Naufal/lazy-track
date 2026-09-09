import json
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from lazytrack.application import PlanExecutor
from lazytrack.config import LazyTrackConfig
from lazytrack.domain import (
    LeaveEntry,
    HolidayEntry,
    OvertimeEntry,
    OperationType,
    Plan,
    PlanOperation,
)
from lazytrack.domain.planner import generate_operation_id
from lazytrack.domain.tz import zone_for
from lazytrack.storage import (
    Database,
    CalendarRepository,
    PlanRepository,
    AuditRepository,
    WorklogCacheRepository,
)
from lazytrack.jira.models import WorklogEntry


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

    def test_overtime_can_coexist_with_leave_on_same_date(self, calendar_repo):
        work_date = date(2026, 9, 10)
        calendar_repo.add_leave(LeaveEntry(date=work_date, hours=Decimal("4")))
        calendar_repo.add_overtime(OvertimeEntry(date=work_date, hours=Decimal("2")))

        assert calendar_repo.get_leave(work_date).hours == Decimal("4")
        assert calendar_repo.get_all_overtime()[0].hours == Decimal("2")


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


class TestPlanExecutorPersistence:
    def _plan(self):
        return Plan(
            id="PL-20260904145727",
            created_at=date(2026, 9, 4),
            status="validated",
            summary="Friday allocation",
            operations=[
                PlanOperation(
                    id=generate_operation_id(),
                    plan_id="PL-20260904145727",
                    operation_type=OperationType.CREATE_WORKLOG,
                    issue_key="SP-8412",
                    work_date=date(2026, 9, 4),
                    seconds=14400,
                    started_at=datetime(2026, 9, 4, 8, 0, tzinfo=zone_for("Asia/Makassar")),
                ),
                PlanOperation(
                    id=generate_operation_id(),
                    plan_id="PL-20260904145727",
                    operation_type=OperationType.CREATE_WORKLOG,
                    issue_key="SP-8412",
                    work_date=date(2026, 9, 4),
                    seconds=14400,
                    started_at=datetime(2026, 9, 4, 13, 0, tzinfo=zone_for("Asia/Makassar")),
                ),
            ],
        )

    def test_save_and_load_plan_with_uuid_operation_ids(self, temp_db):
        executor = PlanExecutor(temp_db, LazyTrackConfig(), None)
        plan = self._plan()

        executor.save_plan(plan)
        loaded = executor.load_plan(plan.id)

        assert loaded is not None
        assert len(loaded.operations) == 2
        assert {op.id for op in loaded.operations} == {op.id for op in plan.operations}
        assert sum(op.seconds for op in loaded.operations) == 28800
        starts = sorted(op.started_at.strftime("%H:%M") for op in loaded.operations)
        assert starts == ["08:00", "13:00"]

    def test_load_plan_parses_sqlite_created_at(self, temp_db):
        executor = PlanExecutor(temp_db, LazyTrackConfig(), None)
        plan = self._plan()
        executor.save_plan(plan)

        loaded = executor.load_plan(plan.id)

        assert isinstance(loaded.created_at, date)

    def test_save_and_load_plan_overtime(self, temp_db):
        executor = PlanExecutor(temp_db, LazyTrackConfig(), None)
        plan = self._plan()
        plan.overtime = [
            OvertimeEntry(date=date(2026, 9, 4), hours=Decimal("2"))
        ]
        executor.save_plan(plan)

        loaded = executor.load_plan(plan.id)

        assert loaded.overtime == plan.overtime


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


class TestWorklogCacheReplace:
    def test_stale_friday_cleared_old_row_kept(self, temp_db):
        conn = temp_db.connect()
        conn.execute(
            """
            INSERT INTO worklog_cache (id, issue_key, work_date, seconds, author)
            VALUES ('old', 'SP-1', '2026-06-01', 28800, 'current_user'),
                   ('fri', 'SP-8412', '2026-09-04', 28800, 'current_user')
            """
        )
        conn.commit()
        repo = WorklogCacheRepository(conn)
        n = repo.replace_window([], date(2026, 8, 3), date(2026, 9, 6))
        assert n == 0
        rows = conn.execute(
            "SELECT id, work_date FROM worklog_cache ORDER BY work_date"
        ).fetchall()
        assert [r["id"] for r in rows] == ["old"]

    def test_replace_inserts_fetched_rows(self, temp_db):
        conn = temp_db.connect()
        conn.execute(
            """
            INSERT INTO worklog_cache (id, issue_key, work_date, seconds, author)
            VALUES ('fri', 'SP-8412', '2026-09-04', 28800, 'current_user')
            """
        )
        conn.commit()
        repo = WorklogCacheRepository(conn)
        wl = WorklogEntry(
            id="new",
            issue_key="SP-1",
            work_date=date(2026, 9, 3),
            seconds=3600,
            author_is_current_user=True,
        )
        repo.replace_window([wl], date(2026, 8, 3), date(2026, 9, 6))
        rows = {r["id"]: r["seconds"] for r in conn.execute("SELECT id, seconds FROM worklog_cache")}
        assert rows == {"new": 3600}

    def test_rejects_oversized_window(self, temp_db):
        repo = WorklogCacheRepository(temp_db.connect())
        with pytest.raises(ValueError):
            repo.replace_window([], date(2020, 1, 1), date(2026, 9, 6))

