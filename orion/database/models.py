"""Database record models for SQLite persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class SessionRecord:
    """Persisted session record supporting session history and crash recovery."""

    session_id: str
    started_at: datetime
    account_id: Optional[str] = None
    account_tag: Optional[str] = None
    ended_at: Optional[datetime] = None
    active_quest_id: Optional[str] = None
    active_task_state: Optional[str] = None
    quests_discovered: int = 0
    quests_completed: int = 0


@dataclass
class ProgressSnapshotRecord:
    """Point-in-time progress snapshot for a tracked Quest."""

    session_id: str
    quest_id: str
    current_seconds: float
    target_seconds: float
    percentage: float
    task_state: str
    captured_at: datetime
    id: Optional[int] = None


@dataclass
class ErrorRecord:
    """Sanitized error entry stored in SQLite."""

    error_code: str
    message: str
    occurred_at: datetime
    id: Optional[int] = None
    session_id: Optional[str] = None
    route: Optional[str] = None
