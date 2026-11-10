"""SQLite connection manager and versioned schema migration runner."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from orion.utils.exceptions import DatabaseError

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS quest_metadata (
            quest_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            application_id TEXT NOT NULL,
            application_name TEXT NOT NULL,
            publisher TEXT NOT NULL,
            category TEXT NOT NULL,
            support_status TEXT NOT NULL,
            target_seconds REAL NOT NULL,
            reward_name TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS quest_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quest_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            progress_seconds REAL NOT NULL,
            target_seconds REAL NOT NULL,
            completed_at TEXT,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS session_history (
            session_id TEXT PRIMARY KEY,
            account_id TEXT,
            account_tag TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            active_quest_id TEXT,
            active_task_state TEXT,
            quests_discovered INTEGER NOT NULL DEFAULT 0,
            quests_completed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS progress_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            quest_id TEXT NOT NULL,
            current_seconds REAL NOT NULL,
            target_seconds REAL NOT NULL,
            percentage REAL NOT NULL,
            task_state TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS error_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            error_code TEXT NOT NULL,
            route TEXT,
            message TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        );
        """,
    ),
    (
        2,
        """
        CREATE INDEX IF NOT EXISTS idx_progress_snapshots_quest
            ON progress_snapshots(quest_id, captured_at);
        CREATE INDEX IF NOT EXISTS idx_quest_history_quest
            ON quest_history(quest_id, recorded_at);
        """,
    ),
]


class DatabaseConnection:
    """Manages SQLite connections, WAL mode, transactions, and schema migrations."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            return conn
        except sqlite3.Error as exc:
            raise DatabaseError(f"Failed to open SQLite database at {self.db_path}: {exc}") from exc

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a transactional SQLite connection that commits on success and rolls back on error."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise DatabaseError(f"Database transaction failed: {exc}") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def apply_migrations(self) -> int:
        """Apply any pending schema migrations in order and return the current schema version."""
        with self.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            row = conn.execute("SELECT MAX(version) AS max_ver FROM schema_migrations").fetchone()
            current_version = int(row["max_ver"]) if row and row["max_ver"] is not None else 0

            for version, sql_script in MIGRATIONS:
                if version > current_version:
                    conn.executescript(sql_script)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                        (version,),
                    )
                    current_version = version

            return current_version
