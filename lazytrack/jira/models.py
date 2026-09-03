from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class IssueSummary(BaseModel):
    key: str
    summary: str
    issue_type: str
    status: str
    project_key: str
    assignee_is_current_user: bool = True


class WorklogEntry(BaseModel):
    id: str
    issue_key: str
    work_date: date
    seconds: int
    author_is_current_user: bool
    managed_by_lazytrack: bool = False
    plan_id: Optional[str] = None

    @property
    def hours(self) -> Decimal:
        return Decimal(str(self.seconds)) / 3600
