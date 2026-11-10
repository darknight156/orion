"""Repository pattern for reading and writing Project Orion state in SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from orion.database.connection import DatabaseConnection
from orion.database.models import ErrorRecord, ProgressSnapshotRecord, SessionRecord
from orion.discord.quest_models import QuestModel


class OrionRepository:
    """Provides transactional persistence for settings, quests, sessions, progress snapshots, and errors."""

    def __init__(self, connection: DatabaseConnection) -> None:
        self.connection = connection
        self.connection.apply_migrations()

    def set_setting(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection.transaction() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self.connection.transaction() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else default

    def upsert_quest_metadata(self, quest: QuestModel) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection.transaction() as conn:
            conn.execute(
                """
                INSERT INTO quest_metadata (
                    quest_id, title, application_id, application_name, publisher,
                    category, support_status, target_seconds, reward_name, expires_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(quest_id) DO UPDATE SET
                    title = excluded.title,
                    application_name = excluded.application_name,
                    category = excluded.category,
                    support_status = excluded.support_status,
                    target_seconds = excluded.target_seconds,
                    reward_name = excluded.reward_name,
                    expires_at = excluded.expires_at,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    quest.id,
                    quest.title,
                    quest.application_id,
                    quest.application_name,
                    quest.publisher,
                    quest.primary_task.category.value,
                    quest.primary_task.support_status.value,
                    quest.primary_task.target_seconds,
                    quest.reward.name,
                    quest.expires_at.isoformat(),
                    now,
                ),
            )

    def record_quest_history(self, quest: QuestModel, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        completed_iso = quest.completed_at.isoformat() if quest.completed_at else None
        with self.connection.transaction() as conn:
            conn.execute(
                """
                INSERT INTO quest_history (
                    quest_id, title, status, progress_seconds, target_seconds, completed_at, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quest.id,
                    quest.title,
                    status,
                    quest.primary_task.current_seconds,
                    quest.primary_task.target_seconds,
                    completed_iso,
                    now,
                ),
            )

    def start_session(
        self,
        session_id: str,
        account_id: Optional[str] = None,
        account_tag: Optional[str] = None,
    ) -> SessionRecord:
        now_dt = datetime.now(timezone.utc)
        with self.connection.transaction() as conn:
            conn.execute(
                """
                INSERT INTO session_history (
                    session_id, account_id, account_tag, started_at, quests_discovered, quests_completed
                )
                VALUES (?, ?, ?, ?, 0, 0)
                """,
                (session_id, account_id, account_tag, now_dt.isoformat()),
            )
        return SessionRecord(
            session_id=session_id,
            account_id=account_id,
            account_tag=account_tag,
            started_at=now_dt,
        )

    def update_session_state(
        self,
        session_id: str,
        *,
        account_id: Optional[str] = None,
        account_tag: Optional[str] = None,
        active_quest_id: Optional[str] = None,
        active_task_state: Optional[str] = None,
        quests_discovered: Optional[int] = None,
        quests_completed: Optional[int] = None,
        ended: bool = False,
    ) -> None:
        ended_iso = datetime.now(timezone.utc).isoformat() if ended else None
        with self.connection.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM session_history WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE session_history
                SET account_id = COALESCE(?, account_id),
                    account_tag = COALESCE(?, account_tag),
                    active_quest_id = ?,
                    active_task_state = ?,
                    quests_discovered = COALESCE(?, quests_discovered),
                    quests_completed = COALESCE(?, quests_completed),
                    ended_at = COALESCE(?, ended_at)
                WHERE session_id = ?
                """,
                (
                    account_id,
                    account_tag,
                    active_quest_id,
                    active_task_state,
                    quests_discovered,
                    quests_completed,
                    ended_iso,
                    session_id,
                ),
            )

    def get_recoverable_session(self) -> Optional[SessionRecord]:
        """Return the most recent unclosed session that had an active or paused task."""
        with self.connection.transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM session_history
                WHERE ended_at IS NULL AND active_quest_id IS NOT NULL
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            return SessionRecord(
                session_id=row["session_id"],
                account_id=row["account_id"],
                account_tag=row["account_tag"],
                started_at=datetime.fromisoformat(row["started_at"]),
                ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
                active_quest_id=row["active_quest_id"],
                active_task_state=row["active_task_state"],
                quests_discovered=int(row["quests_discovered"]),
                quests_completed=int(row["quests_completed"]),
            )

    def list_sessions(self, limit: int = 20) -> list[SessionRecord]:
        with self.connection.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM session_history ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                SessionRecord(
                    session_id=r["session_id"],
                    account_id=r["account_id"],
                    account_tag=r["account_tag"],
                    started_at=datetime.fromisoformat(r["started_at"]),
                    ended_at=datetime.fromisoformat(r["ended_at"]) if r["ended_at"] else None,
                    active_quest_id=r["active_quest_id"],
                    active_task_state=r["active_task_state"],
                    quests_discovered=int(r["quests_discovered"]),
                    quests_completed=int(r["quests_completed"]),
                )
                for r in rows
            ]

    def record_progress_snapshot(
        self,
        *,
        session_id: str,
        quest_id: str,
        current_seconds: float,
        target_seconds: float,
        percentage: float,
        task_state: str,
    ) -> ProgressSnapshotRecord:
        now_dt = datetime.now(timezone.utc)
        with self.connection.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO progress_snapshots (
                    session_id, quest_id, current_seconds, target_seconds, percentage, task_state, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    quest_id,
                    current_seconds,
                    target_seconds,
                    percentage,
                    task_state,
                    now_dt.isoformat(),
                ),
            )
            row_id = cursor.lastrowid
        return ProgressSnapshotRecord(
            id=row_id,
            session_id=session_id,
            quest_id=quest_id,
            current_seconds=current_seconds,
            target_seconds=target_seconds,
            percentage=percentage,
            task_state=task_state,
            captured_at=now_dt,
        )

    def get_latest_snapshot(self, quest_id: str) -> Optional[ProgressSnapshotRecord]:
        with self.connection.transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM progress_snapshots
                WHERE quest_id = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """,
                (quest_id,),
            ).fetchone()
            if not row:
                return None
            return ProgressSnapshotRecord(
                id=int(row["id"]),
                session_id=row["session_id"],
                quest_id=row["quest_id"],
                current_seconds=float(row["current_seconds"]),
                target_seconds=float(row["target_seconds"]),
                percentage=float(row["percentage"]),
                task_state=row["task_state"],
                captured_at=datetime.fromisoformat(row["captured_at"]),
            )

    def record_error(
        self,
        *,
        session_id: Optional[str],
        error_code: str,
        message: str,
        route: Optional[str] = None,
    ) -> ErrorRecord:
        now_dt = datetime.now(timezone.utc)
        with self.connection.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO error_history (session_id, error_code, route, message, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, error_code, route, message, now_dt.isoformat()),
            )
            row_id = cursor.lastrowid
        return ErrorRecord(
            id=row_id,
            session_id=session_id,
            error_code=error_code,
            route=route,
            message=message,
            occurred_at=now_dt,
        )

    def list_errors(self, limit: int = 25) -> list[dict[str, Any]]:
        with self.connection.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM error_history ORDER BY occurred_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
