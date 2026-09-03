import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


class Database:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".lazytrack" / "lazytrack.db"
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path)
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def close(self):
        if self._connection:
            self._connection.close()
            self._connection = None

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def initialize(self):
        from lazytrack.storage.migrations import MIGRATIONS, get_current_version
        conn = self.connect()
        current = get_current_version(conn)
        for migration in MIGRATIONS:
            if migration.version > current:
                migration.upgrade(conn)
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (migration.version,)
                )
                conn.commit()


def get_db(db_path: Path | None = None) -> Database:
    db = Database(db_path)
    db.initialize()
    return db
