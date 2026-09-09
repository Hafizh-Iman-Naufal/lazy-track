from datetime import date

import pytest
import typer
from rich.console import Console

from lazytrack import cli
from lazytrack.config import LazyTrackConfig
from lazytrack.storage import Database
from lazytrack.ui.status import iso_week_id, parse_iso_week


@pytest.fixture
def status_db(tmp_path):
    db = Database(tmp_path / "status.db")
    db.initialize()
    conn = db.connect()
    conn.execute(
        """
        INSERT INTO worklog_cache
        (id, issue_key, work_date, seconds, author)
        VALUES ('1', 'SP-1', '2026-09-01', 30600, 'current_user')
        """
    )
    conn.commit()
    yield db
    db.close()


def _recorded_status(monkeypatch, status_db, week):
    output = Console(record=True, width=100, color_system=None)
    config = LazyTrackConfig()
    config.work.timezone = "Asia/Makassar"
    monkeypatch.setattr(cli, "console", output)
    monkeypatch.setattr(cli, "get_db", lambda: status_db)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    cli.status(week=week)
    return output.export_text()


def test_cli_status_selected_week(monkeypatch, status_db):
    text = _recorded_status(monkeypatch, status_db, "2026-W36")
    assert "Week 2026-W36: 2026-08-31 - 2026-09-06" in text
    assert "8.5h" in text
    assert "Logged:    8.5h" in text


def test_cli_status_default_week_uses_configured_timezone(monkeypatch, status_db):
    monkeypatch.setattr(cli, "today_in_timezone", lambda timezone: date(2026, 9, 6))
    text = _recorded_status(monkeypatch, status_db, None)
    assert "Week 2026-W36: 2026-08-31 - 2026-09-06" in text


def test_cli_status_rejects_invalid_week(monkeypatch, status_db):
    with pytest.raises(typer.Exit):
        _recorded_status(monkeypatch, status_db, "2026-W99")


def test_iso_week_year_boundary():
    week_start = parse_iso_week("2020-W01")
    assert week_start == date(2019, 12, 30)
    assert iso_week_id(week_start) == "2020-W01"
