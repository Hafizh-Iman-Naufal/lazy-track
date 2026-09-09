from typing import Any, Iterable

from rich.table import Table
from rich.text import Text


def _val(item: Any, name: str) -> str:
    try:
        return item[name]
    except (KeyError, TypeError, IndexError):
        return getattr(item, name)


def issues_table(issues: Iterable[Any], base_url: str = "") -> Table:
    rows = sorted(issues, key=lambda item: _val(item, "key"))
    table = Table(title="Assigned Issues")
    table.add_column("Key", style="cyan")
    table.add_column("Summary", style="white")
    table.add_column("Type", style="green")
    table.add_column("Status", style="yellow")
    for row in rows:
        key = _val(row, "key")
        if base_url:
            key_text = Text.from_markup(
                f"[link={base_url}/browse/{key}][cyan]{key}[/cyan][/link]"
            )
        else:
            key_text = Text(key, style="cyan")
        table.add_row(
            key_text,
            _val(row, "summary"),
            _val(row, "issue_type"),
            _val(row, "status"),
        )
    table.caption = f"{len(rows)} issues total"
    return table
