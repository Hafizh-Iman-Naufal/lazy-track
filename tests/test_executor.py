import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lazytrack.application import PlanExecutor
from lazytrack.config import LazyTrackConfig
from lazytrack.domain import OperationType, OvertimeEntry, Plan, PlanOperation
from lazytrack.domain.planner import generate_operation_id
from lazytrack.storage import Database


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


def _workday() -> date:
    d = date.today()
    if d.weekday() >= 5:
        d = d - timedelta(days=d.weekday() - 4)
    return d


def _delete_plan(n: int, work_date: date) -> Plan:
    plan_id = "PL-TEST"
    ops = [
        PlanOperation(
            id=generate_operation_id(),
            plan_id=plan_id,
            operation_type=OperationType.DELETE_WORKLOG,
            issue_key="ABC-123",
            work_date=work_date,
            seconds=3600,
            existing_worklog_id=str(i),
        )
        for i in range(n)
    ]
    return Plan(id=plan_id, created_at=work_date, operations=ops, status="validated")


class TestExecutorGuards:
    def test_delete_blocked_when_disabled(self, temp_db):
        config = LazyTrackConfig()
        config.safety.allow_worklog_delete = False
        executor = PlanExecutor(temp_db, config, MagicMock())
        plan = _delete_plan(1, _workday())
        executor.save_plan(plan)
        result = executor.execute_plan(plan.id)
        assert result.status == "blocked_by_safety_config"
        assert result.operations_completed == 0

    def test_too_many_deletes_blocked(self, temp_db):
        config = LazyTrackConfig()
        config.safety.max_delete_ops_per_plan = 2
        executor = PlanExecutor(temp_db, config, MagicMock())
        plan = _delete_plan(3, _workday())
        executor.save_plan(plan)
        result = executor.execute_plan(plan.id)
        assert result.status == "blocked_too_many_deletes"

    def test_outside_window_blocked(self, temp_db):
        config = LazyTrackConfig()
        executor = PlanExecutor(temp_db, config, MagicMock())
        plan = _delete_plan(1, date(2025, 1, 6))
        executor.save_plan(plan)
        result = executor.execute_plan(plan.id)
        assert result.status == "blocked_outside_worklog_window"


class FakeJira:
    def __init__(self):
        self.posts = []

    def get(self, path):
        return {"timeZone": "UTC"}

    def post(self, path, **kwargs):
        self.posts.append((path, kwargs["json"]))
        return {"id": str(len(self.posts))}


def test_apply_registers_overtime_once_and_rejects_repeat(temp_db):
    work_date = _workday()
    plan = Plan(
        id="PL-OT",
        created_at=work_date,
        status="validated",
        operations=[
            PlanOperation(
                id=generate_operation_id(),
                plan_id="PL-OT",
                operation_type=OperationType.CREATE_WORKLOG,
                issue_key="ABC-123",
                work_date=work_date,
                seconds=3600,
            )
        ],
        overtime=[OvertimeEntry(date=work_date, hours=Decimal("1"))],
    )
    client = FakeJira()
    executor = PlanExecutor(temp_db, LazyTrackConfig(), client)
    executor.save_plan(plan)

    first = executor.execute_plan(plan.id)
    second = executor.execute_plan(plan.id)

    assert first.status == "success"
    assert second.status == "invalid_status: applied"
    assert len(client.posts) == 1
    row = temp_db.connect().execute(
        "SELECT hours FROM calendar_exceptions WHERE date = ? AND exception_type = 'overtime'",
        (work_date.isoformat(),),
    ).fetchone()
    assert Decimal(str(row["hours"])) == Decimal("1")
