from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from lazytrack.config import LazyTrackConfig
from lazytrack.domain import (
    OperationStatus,
    OperationType,
    Plan,
    PlanOperation,
)
from lazytrack.jira import JiraClient, WorklogWriter
from lazytrack.jira.models import WorklogEntry
from lazytrack.storage import Database, AuditRepository, PlanRepository


@dataclass
class ExecutionResult:
    plan_id: str
    status: str
    operations_completed: int
    operations_failed: int
    failed_operations: list[str] = field(default_factory=list)


class UndoConflictError(Exception):
    pass


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
        self.writer = WorklogWriter(jira_client)

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
            )
            operations.append(op)

        self.plan_repo.save_plan(plan_id, "undoing")

        completed = 0
        failed = 0
        failed_ops = []

        for op in reversed(operations):
            if op.operation_type == OperationType.CREATE_WORKLOG:
                managed = conn.execute(
                    "SELECT * FROM managed_worklogs WHERE operation_id = ?",
                    (op.id,)
                ).fetchone()
                
                if managed:
                    try:
                        self.writer.delete_worklog(
                            worklog_id=managed["jira_worklog_id"],
                            issue_key=op.issue_key,
                        )
                        conn.execute(
                            "DELETE FROM managed_worklogs WHERE id = ?",
                            (managed["id"],)
                        )
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
        self.writer = WorklogWriter(jira_client)

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

        if not self._check_safety_config():
            return ExecutionResult(
                plan_id=plan_id,
                status="blocked_by_safety_config",
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
            )
            operations.append(op)

        self.plan_repo.save_plan(plan_id, "applying")

        completed = 0
        failed = 0
        failed_ops = []

        if dry_run:
            for op in operations:
                if op.status == OperationStatus.PENDING:
                    completed += 1
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
            )
            conn.execute(
                """
                INSERT INTO managed_worklogs (jira_worklog_id, issue_key, plan_id, operation_id)
                VALUES (?, ?, ?, ?)
                """,
                (worklog.id, op.issue_key, op.plan_id, op.id),
            )
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
            conn.commit()

    def _check_safety_config(self) -> bool:
        safety = self.config.safety
        if not safety.allow_worklog_create:
            return False
        return True

    def save_plan(self, plan: Plan) -> None:
        self.plan_repo.save_plan(plan.id, plan.status, plan.summary)

        conn = self.db.connect()
        for op in plan.operations:
            conn.execute(
                """
                INSERT OR REPLACE INTO plan_operations 
                (id, plan_id, operation_type, issue_key, work_date, seconds, existing_worklog_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
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
            )
            operations.append(op)

        return Plan(
            id=plan_id,
            created_at=date.fromisoformat(plan_data["created_at"]),
            operations=operations,
            status=plan_data["status"],
            summary=plan_data["summary"] or "",
        )
