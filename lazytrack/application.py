from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from lazytrack.config import LazyTrackConfig
from lazytrack.domain import (
    OperationStatus,
    OperationType,
    Plan,
    PlanOperation,
    OvertimeEntry,
)
from lazytrack.domain.tz import now_in_zone
from lazytrack.domain.window import write_date_ok
from lazytrack.jira import JiraClient, WorklogWriter
from lazytrack.jira.models import WorklogEntry
from lazytrack.storage import (
    AuditRepository,
    CalendarRepository,
    Database,
    PlanRepository,
    WorklogCacheRepository,
)


def _parse_created_at(value) -> date:
    if not value:
        return date.today()
    try:
        return date.fromisoformat(str(value).split()[0][:10])
    except ValueError:
        return date.today()


def _parse_started_at(row) -> Optional[datetime]:
    try:
        value = row["started_at"]
    except (KeyError, IndexError):
        return None
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class ExecutionResult:
    plan_id: str
    status: str
    operations_completed: int
    operations_failed: int
    failed_operations: list[str] = field(default_factory=list)


class UndoConflictError(Exception):
    pass


def _block_write_ops(config: LazyTrackConfig, operations: list, undo: bool = False) -> Optional[str]:
    safety = config.safety
    today = now_in_zone(config.work.timezone).date()
    if undo:
        delete_count = sum(
            1 for op in operations if op.operation_type == OperationType.CREATE_WORKLOG
        )
        if delete_count and not safety.allow_worklog_delete:
            return "blocked_by_safety_config"
        if delete_count > safety.max_delete_ops_per_plan:
            return "blocked_too_many_deletes"
    else:
        types = {op.operation_type for op in operations}
        if OperationType.CREATE_WORKLOG in types and not safety.allow_worklog_create:
            return "blocked_by_safety_config"
        if OperationType.DELETE_WORKLOG in types and not safety.allow_worklog_delete:
            return "blocked_by_safety_config"
        if (
            {OperationType.UPDATE_WORKLOG, OperationType.MOVE_WORKLOG} & types
            and not safety.allow_worklog_update
        ):
            return "blocked_by_safety_config"
        delete_count = sum(
            1 for op in operations if op.operation_type == OperationType.DELETE_WORKLOG
        )
        if delete_count > safety.max_delete_ops_per_plan:
            return "blocked_too_many_deletes"

    for op in operations:
        if not write_date_ok(
            op.work_date,
            today,
            safety.worklog_lookback_weeks,
            safety.max_plan_span_days,
        ):
            return "blocked_outside_worklog_window"
    return None


class UndoExecutor:
    def __init__(
        self,
        db: Database,
        config: LazyTrackConfig,
        jira_client: JiraClient,
    ):
        self.db = db
        self.config = config
        self.plan_repo = PlanRepository(db.connect())
        self.audit_repo = AuditRepository(db.connect())
        self.writer = WorklogWriter(
            jira_client,
            timezone=config.work.timezone,
            day_start=config.work.day_start,
        )

    def check_for_conflicts(self, plan_id: str) -> list[str]:
        conflicts = []
        conn = self.db.connect()
        
        cursor = conn.execute(
            "SELECT * FROM plan_operations WHERE plan_id = ?",
            (plan_id,)
        )
        for row in cursor.fetchall():
            worklog_id = row["existing_worklog_id"]
            if not worklog_id:
                continue
            
            managed = conn.execute(
                "SELECT * FROM managed_worklogs WHERE jira_worklog_id = ?",
                (worklog_id,)
            ).fetchone()
            
            if not managed:
                conflicts.append(
                    f"Worklog {worklog_id} on {row['issue_key']} was modified externally"
                )
        
        return conflicts

    def undo_plan(self, plan_id: str) -> ExecutionResult:
        plan_data = self.plan_repo.get_plan(plan_id)
        if not plan_data:
            return ExecutionResult(
                plan_id=plan_id,
                status="not_found",
                operations_completed=0,
                operations_failed=0,
            )

        if plan_data["status"] != "applied":
            return ExecutionResult(
                plan_id=plan_id,
                status=f"cannot_undo: status is {plan_data['status']}",
                operations_completed=0,
                operations_failed=0,
            )

        conflicts = self.check_for_conflicts(plan_id)
        if conflicts:
            raise UndoConflictError(
                f"Cannot safely undo. Conflicts detected:\n" + "\n".join(f"  - {c}" for c in conflicts)
            )

        conn = self.db.connect()
        cursor = conn.execute(
            "SELECT * FROM plan_operations WHERE plan_id = ?",
            (plan_id,)
        )
        operations = []
        for row in cursor.fetchall():
            op = PlanOperation(
                id=str(row["id"]),
                plan_id=row["plan_id"],
                operation_type=OperationType(row["operation_type"]),
                issue_key=row["issue_key"],
                work_date=date.fromisoformat(row["work_date"]),
                seconds=row["seconds"],
                existing_worklog_id=row["existing_worklog_id"],
                status=OperationStatus(row["status"]),
                started_at=_parse_started_at(row),
            )
            operations.append(op)

        blocked = _block_write_ops(self.config, operations, undo=True)
        if blocked:
            return ExecutionResult(
                plan_id=plan_id,
                status=blocked,
                operations_completed=0,
                operations_failed=0,
            )

        self.plan_repo.save_plan(plan_id, "undoing")

        completed = 0
        failed = 0
        failed_ops = []
        cache = WorklogCacheRepository(conn)

        for op in reversed(operations):
            if op.operation_type == OperationType.CREATE_WORKLOG:
                managed = conn.execute(
                    "SELECT * FROM managed_worklogs WHERE operation_id = ?",
                    (op.id,)
                ).fetchone()
                
                if managed:
                    try:
                        jira_id = managed["jira_worklog_id"]
                        self.writer.delete_worklog(
                            worklog_id=jira_id,
                            issue_key=op.issue_key,
                        )
                        conn.execute(
                            "DELETE FROM managed_worklogs WHERE jira_worklog_id = ?",
                            (jira_id,)
                        )
                        cache.delete_id(jira_id)
                        conn.commit()
                        completed += 1
                    except Exception as e:
                        failed += 1
                        failed_ops.append(f"DELETE {op.issue_key}: {e}")

        self.plan_repo.save_plan(plan_id, "undone")

        self.audit_repo.log(
            action="UNDO_PLAN",
            entity_type="plan",
            entity_id=plan_id,
            after={"status": "undone", "operations_undone": completed},
            plan_id=plan_id,
        )

        return ExecutionResult(
            plan_id=plan_id,
            status="success" if failed == 0 else "partial_failure",
            operations_completed=completed,
            operations_failed=failed,
            failed_operations=failed_ops,
        )


class PlanExecutor:
    def __init__(
        self,
        db: Database,
        config: LazyTrackConfig,
        jira_client: JiraClient,
    ):
        self.db = db
        self.config = config
        self.plan_repo = PlanRepository(db.connect())
        self.audit_repo = AuditRepository(db.connect())
        self.writer = WorklogWriter(
            jira_client,
            timezone=config.work.timezone,
            day_start=config.work.day_start,
        )

    def execute_plan(self, plan_id: str, dry_run: bool = False) -> ExecutionResult:
        plan_data = self.plan_repo.get_plan(plan_id)
        if not plan_data:
            return ExecutionResult(
                plan_id=plan_id,
                status="not_found",
                operations_completed=0,
                operations_failed=0,
            )

        if plan_data["status"] != "validated":
            return ExecutionResult(
                plan_id=plan_id,
                status=f"invalid_status: {plan_data['status']}",
                operations_completed=0,
                operations_failed=0,
            )

        conn = self.db.connect()
        cursor = conn.execute(
            "SELECT * FROM plan_operations WHERE plan_id = ?",
            (plan_id,)
        )
        operations = []
        for row in cursor.fetchall():
            op = PlanOperation(
                id=str(row["id"]),
                plan_id=row["plan_id"],
                operation_type=OperationType(row["operation_type"]),
                issue_key=row["issue_key"],
                work_date=date.fromisoformat(row["work_date"]),
                seconds=row["seconds"],
                existing_worklog_id=row["existing_worklog_id"],
                status=OperationStatus(row["status"]),
                started_at=_parse_started_at(row),
            )
            operations.append(op)

        blocked = _block_write_ops(self.config, operations)
        if blocked:
            return ExecutionResult(
                plan_id=plan_id,
                status=blocked,
                operations_completed=0,
                operations_failed=0,
            )

        self.plan_repo.save_plan(plan_id, "applying")

        completed = 0
        failed = 0
        failed_ops = []

        if dry_run:
            for op in operations:
                if op.status == OperationStatus.PENDING:
                    completed += 1
            self.plan_repo.save_plan(plan_id, "validated", plan_data["summary"] or "")
            return ExecutionResult(
                plan_id=plan_id,
                status="dry_run_success",
                operations_completed=completed,
                operations_failed=0,
            )

        for op in operations:
            if op.status != OperationStatus.PENDING:
                continue

            try:
                self._execute_operation(conn, op)
                op.status = OperationStatus.SUCCESS
                completed += 1
            except Exception as e:
                op.status = OperationStatus.FAILED
                op.error_message = str(e)
                failed += 1
                failed_ops.append(f"{op.operation_type.value} {op.issue_key}: {e}")

                self.plan_repo.save_plan(plan_id, "failed")
                return ExecutionResult(
                    plan_id=plan_id,
                    status="partial_failure",
                    operations_completed=completed,
                    operations_failed=failed,
                    failed_operations=failed_ops,
                )

        try:
            calendar_repo = CalendarRepository(conn)
            existing_overtime = {
                item.date: item for item in calendar_repo.get_all_overtime()
            }
            for overtime in self._load_overtime(plan_id):
                existing = existing_overtime.get(overtime.date)
                calendar_repo.add_overtime(
                    OvertimeEntry(
                        date=overtime.date,
                        hours=overtime.hours + (existing.hours if existing else Decimal("0")),
                    )
                )
                conn.execute(
                    "UPDATE plan_overtime SET status = 'success' WHERE plan_id = ? AND date = ?",
                    (plan_id, overtime.date.isoformat()),
                )
            conn.commit()
        except Exception as exc:
            self.plan_repo.save_plan(plan_id, "failed")
            return ExecutionResult(
                plan_id=plan_id,
                status="partial_failure",
                operations_completed=completed,
                operations_failed=1,
                failed_operations=[f"REGISTER_OVERTIME: {exc}"],
            )

        self.plan_repo.save_plan(plan_id, "applied")

        self.audit_repo.log(
            action="APPLY_PLAN",
            entity_type="plan",
            entity_id=plan_id,
            after={"status": "applied", "operations": completed},
            plan_id=plan_id,
        )

        return ExecutionResult(
            plan_id=plan_id,
            status="success",
            operations_completed=completed,
            operations_failed=failed,
        )

    def _execute_operation(self, conn, op: PlanOperation):
        if op.operation_type == OperationType.CREATE_WORKLOG:
            worklog = self.writer.create_worklog(
                issue_key=op.issue_key,
                work_date=op.work_date,
                seconds=op.seconds,
                plan_id=op.plan_id,
                started_at=op.started_at,
            )
            conn.execute(
                """
                INSERT INTO managed_worklogs (jira_worklog_id, issue_key, plan_id, operation_id)
                VALUES (?, ?, ?, ?)
                """,
                (worklog.id, op.issue_key, op.plan_id, op.id),
            )
            WorklogCacheRepository(conn).upsert(worklog)
            conn.commit()

        elif op.operation_type == OperationType.DELETE_WORKLOG:
            self.writer.delete_worklog(
                worklog_id=op.existing_worklog_id,
                issue_key=op.issue_key,
            )
            conn.execute(
                "DELETE FROM managed_worklogs WHERE jira_worklog_id = ?",
                (op.existing_worklog_id,)
            )
            WorklogCacheRepository(conn).delete_id(op.existing_worklog_id)
            conn.commit()

    def _check_safety_config(self) -> bool:
        return self.config.safety.allow_worklog_create

    def save_plan(self, plan: Plan) -> None:
        self.plan_repo.save_plan(plan.id, plan.status, plan.summary)

        conn = self.db.connect()
        for op in plan.operations:
            conn.execute(
                """
                INSERT OR REPLACE INTO plan_operations 
                (id, plan_id, operation_type, issue_key, work_date, seconds, existing_worklog_id, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    op.id,
                    op.plan_id,
                    op.operation_type.value,
                    op.issue_key,
                    op.work_date.isoformat(),
                    op.seconds,
                    op.existing_worklog_id,
                    op.status.value,
                    op.started_at.isoformat() if op.started_at else None,
                ),
            )
        for overtime in plan.overtime:
            conn.execute(
                """
                INSERT OR REPLACE INTO plan_overtime (plan_id, date, hours, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (plan.id, overtime.date.isoformat(), float(overtime.hours)),
            )
        conn.commit()

    def load_plan(self, plan_id: str) -> Optional[Plan]:
        plan_data = self.plan_repo.get_plan(plan_id)
        if not plan_data:
            return None

        conn = self.db.connect()
        cursor = conn.execute(
            "SELECT * FROM plan_operations WHERE plan_id = ?",
            (plan_id,)
        )
        operations = []
        for row in cursor.fetchall():
            op = PlanOperation(
                id=str(row["id"]),
                plan_id=row["plan_id"],
                operation_type=OperationType(row["operation_type"]),
                issue_key=row["issue_key"],
                work_date=date.fromisoformat(row["work_date"]),
                seconds=row["seconds"],
                existing_worklog_id=row["existing_worklog_id"],
                status=OperationStatus(row["status"]),
                started_at=_parse_started_at(row),
            )
            operations.append(op)
        overtime = self._load_overtime(plan_id)

        return Plan(
            id=plan_id,
            created_at=_parse_created_at(plan_data["created_at"]),
            operations=operations,
            status=plan_data["status"],
            summary=plan_data["summary"] or "",
            overtime=overtime,
        )

    def _load_overtime(self, plan_id: str) -> list[OvertimeEntry]:
        rows = self.db.connect().execute(
            "SELECT date, hours FROM plan_overtime WHERE plan_id = ? ORDER BY date",
            (plan_id,),
        ).fetchall()
        return [
            OvertimeEntry(
                date=date.fromisoformat(row["date"]),
                hours=Decimal(str(row["hours"])),
            )
            for row in rows
        ]
