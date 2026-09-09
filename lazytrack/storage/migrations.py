import sqlite3
from dataclasses import dataclass


@dataclass
class Migration:
    version: int
    upgrade: callable
    downgrade: callable


def get_current_version(conn: sqlite3.Connection) -> int:
    try:
        cursor = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return row["version"] if row else 0
    except sqlite3.OperationalError:
        return 0


def _migration_1(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migration_2(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calendar_exceptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            exception_type TEXT NOT NULL,
            hours REAL DEFAULT 0,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migration_3(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'draft',
            summary TEXT
        )
    """)


def _migration_4(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plan_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            issue_key TEXT NOT NULL,
            work_date TEXT NOT NULL,
            seconds INTEGER NOT NULL,
            existing_worklog_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        )
    """)


def _migration_5(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS managed_worklogs (
            jira_worklog_id TEXT PRIMARY KEY,
            issue_key TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            operation_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        )
    """)


def _migration_6(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS issues_cache (
            key TEXT PRIMARY KEY,
            summary TEXT,
            issue_type TEXT,
            status TEXT,
            project_key TEXT,
            assignee TEXT,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migration_7(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS worklog_cache (
            id TEXT PRIMARY KEY,
            issue_key TEXT NOT NULL,
            work_date TEXT NOT NULL,
            seconds INTEGER NOT NULL,
            author TEXT,
            managed_by_lazytrack INTEGER DEFAULT 0,
            plan_id TEXT,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migration_8(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _migration_9(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT,
            before_json TEXT,
            after_json TEXT,
            plan_id TEXT
        )
    """)


def _migration_10(conn: sqlite3.Connection):
    cols = [row[1] for row in conn.execute("PRAGMA table_info(plan_operations)").fetchall()]
    if "started_at" not in cols:
        conn.execute("ALTER TABLE plan_operations ADD COLUMN started_at TEXT")


def _migration_11(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plan_operations_new (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            issue_key TEXT NOT NULL,
            work_date TEXT NOT NULL,
            seconds INTEGER NOT NULL,
            existing_worklog_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT,
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        )
    """)
    conn.execute("""
        INSERT INTO plan_operations_new
        SELECT CAST(id AS TEXT), plan_id, operation_type, issue_key,
               work_date, seconds, existing_worklog_id, status, started_at
        FROM plan_operations
    """)
    conn.execute("DROP TABLE plan_operations")
    conn.execute("ALTER TABLE plan_operations_new RENAME TO plan_operations")


def _migration_12(conn: sqlite3.Connection):
    conn.execute("ALTER TABLE calendar_exceptions RENAME TO calendar_exceptions_old")
    conn.execute("""
        CREATE TABLE calendar_exceptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            exception_type TEXT NOT NULL,
            hours REAL DEFAULT 0,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, exception_type)
        )
    """)
    conn.execute("""
        INSERT INTO calendar_exceptions
            (id, date, exception_type, hours, description, created_at)
        SELECT id, date, exception_type, hours, description, created_at
        FROM calendar_exceptions_old
    """)
    conn.execute("DROP TABLE calendar_exceptions_old")
    conn.execute("""
        CREATE TABLE plan_overtime (
            plan_id TEXT NOT NULL,
            date TEXT NOT NULL,
            hours REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            PRIMARY KEY (plan_id, date),
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        )
    """)


MIGRATIONS: list[Migration] = [
    Migration(version=1, upgrade=_migration_1, downgrade=lambda c: None),
    Migration(version=2, upgrade=_migration_2, downgrade=lambda c: None),
    Migration(version=3, upgrade=_migration_3, downgrade=lambda c: None),
    Migration(version=4, upgrade=_migration_4, downgrade=lambda c: None),
    Migration(version=5, upgrade=_migration_5, downgrade=lambda c: None),
    Migration(version=6, upgrade=_migration_6, downgrade=lambda c: None),
    Migration(version=7, upgrade=_migration_7, downgrade=lambda c: None),
    Migration(version=8, upgrade=_migration_8, downgrade=lambda c: None),
    Migration(version=9, upgrade=_migration_9, downgrade=lambda c: None),
    Migration(version=10, upgrade=_migration_10, downgrade=lambda c: None),
    Migration(version=11, upgrade=_migration_11, downgrade=lambda c: None),
    Migration(version=12, upgrade=_migration_12, downgrade=lambda c: None),
]
