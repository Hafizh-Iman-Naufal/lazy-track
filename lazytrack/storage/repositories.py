import json
from datetime import date
from decimal import Decimal
from typing import Optional

import sqlite3

from lazytrack.domain import (
    HolidayEntry,
    LeaveEntry,
    OvertimeEntry,
)


class CalendarRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_leave(self, leave: LeaveEntry) -> bool:
        try:
            self.conn.execute(
                """
                INSERT INTO calendar_exceptions (date, exception_type, hours, description)
                VALUES (?, ?, ?, ?)
                """,
                (
                    leave.date.isoformat(),
                    "leave",
                    float(leave.hours),
                    leave.description,
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            self.conn.execute(
                """
                UPDATE calendar_exceptions 
                SET hours = ?, description = ?
                WHERE date = ? AND exception_type = 'leave'
                """,
                (float(leave.hours), leave.description, leave.date.isoformat()),
            )
            self.conn.commit()
            return True

    def remove_leave(self, d: date) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM calendar_exceptions WHERE date = ? AND exception_type = 'leave'",
            (d.isoformat(),),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_leave(self, d: date) -> Optional[LeaveEntry]:
        cursor = self.conn.execute(
            "SELECT date, hours, description FROM calendar_exceptions WHERE date = ? AND exception_type = 'leave'",
            (d.isoformat(),),
        )
        row = cursor.fetchone()
        if row:
            return LeaveEntry(
                date=date.fromisoformat(row["date"]),
                hours=Decimal(str(row["hours"])),
                description=row["description"],
            )
        return None

    def get_all_leaves(self) -> list[LeaveEntry]:
        cursor = self.conn.execute(
            "SELECT date, hours, description FROM calendar_exceptions WHERE exception_type = 'leave' ORDER BY date"
        )
        return [
            LeaveEntry(
                date=date.fromisoformat(row["date"]),
                hours=Decimal(str(row["hours"])),
                description=row["description"],
            )
            for row in cursor.fetchall()
        ]

    def add_holiday(self, holiday: HolidayEntry) -> bool:
        try:
            self.conn.execute(
                """
                INSERT INTO calendar_exceptions (date, exception_type, description)
                VALUES (?, ?, ?)
                """,
                (holiday.date.isoformat(), "holiday", holiday.description),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            self.conn.execute(
                """
                UPDATE calendar_exceptions SET description = ?
                WHERE date = ? AND exception_type = 'holiday'
                """,
                (holiday.description, holiday.date.isoformat()),
            )
            self.conn.commit()
            return True

    def remove_holiday(self, d: date) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM calendar_exceptions WHERE date = ? AND exception_type = 'holiday'",
            (d.isoformat(),),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_all_holidays(self) -> list[HolidayEntry]:
        cursor = self.conn.execute(
            "SELECT date, description FROM calendar_exceptions WHERE exception_type = 'holiday' ORDER BY date"
        )
        return [
            HolidayEntry(
                date=date.fromisoformat(row["date"]),
                description=row["description"],
            )
            for row in cursor.fetchall()
        ]


class PlanRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save_plan(self, plan_id: str, status: str, summary: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO plans (id, status, summary) VALUES (?, ?, ?)",
            (plan_id, status, summary),
        )
        self.conn.commit()

    def get_plan(self, plan_id: str) -> Optional[dict]:
        cursor = self.conn.execute(
            "SELECT id, created_at, status, summary FROM plans WHERE id = ?",
            (plan_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def list_plans(self, status: Optional[str] = None) -> list[dict]:
        if status:
            cursor = self.conn.execute(
                "SELECT id, created_at, status, summary FROM plans WHERE status = ? ORDER BY created_at DESC",
                (status,),
            )
        else:
            cursor = self.conn.execute(
                "SELECT id, created_at, status, summary FROM plans ORDER BY created_at DESC"
            )
        return [dict(row) for row in cursor.fetchall()]


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log(
        self,
        action: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        before: Optional[dict] = None,
        after: Optional[dict] = None,
        plan_id: Optional[str] = None,
    ):
        self.conn.execute(
            """
            INSERT INTO audit_log (action, entity_type, entity_id, before_json, after_json, plan_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                entity_type,
                entity_id,
                json.dumps(before) if before else None,
                json.dumps(after) if after else None,
                plan_id,
            ),
        )
        self.conn.commit()

    def get_recent(self, limit: int = 50) -> list[dict]:
        cursor = self.conn.execute(
            """
            SELECT id, timestamp, action, entity_type, entity_id, before_json, after_json, plan_id
            FROM audit_log ORDER BY timestamp DESC LIMIT ?
            """,
            (limit,),
        )
        rows = []
        for row in cursor.fetchall():
            r = dict(row)
            if r.get("before_json"):
                r["before_json"] = json.loads(r["before_json"])
            if r.get("after_json"):
                r["after_json"] = json.loads(r["after_json"])
            rows.append(r)
        return rows
