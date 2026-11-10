"""Top-level OrionApplication orchestrator binding config, database, API client, discovery, and task manager."""

from __future__ import annotations

import time
import uuid
from typing import Optional

from orion.config import OrionConfig
from orion.core.event_manager import EventManager, LogCategory
from orion.core.task_manager import QuestTaskManager
from orion.database.connection import DatabaseConnection
from orion.database.repository import OrionRepository
from orion.discord.authentication import Authenticator
from orion.discord.client import DiscordAPIClient
from orion.discord.quest_discovery import QuestDiscoveryService
from orion.discord.quest_models import DiscordUser, QuestCategory, QuestModel
from orion.utils.exceptions import OrionError


class OrionApplication:
    """Central application controller for Project Orion."""

    def __init__(self, config: Optional[OrionConfig] = None) -> None:
        self.config = config or OrionConfig.from_env()
        self.session_id = f"orion-{uuid.uuid4().hex[:8]}"
        self.started_monotonic = time.monotonic()

        self.events = EventManager()
        self.db_connection = DatabaseConnection(self.config.database_path)
        self.repository = OrionRepository(self.db_connection)
        self.authenticator = Authenticator(self.config.discord_token)
        self.client = DiscordAPIClient(
            self.config,
            self.authenticator,
            on_api_log=self._handle_api_log,
        )
        self.discovery = QuestDiscoveryService(self.client)
        self.task_manager = QuestTaskManager(
            discovery=self.discovery,
            repository=self.repository,
            event_manager=self.events,
            session_id=self.session_id,
            poll_interval_seconds=self.config.refresh_interval_seconds,
        )
        self.selected_quest_id: Optional[str] = None

    async def _handle_api_log(self, msg: str) -> None:
        await self.events.emit(LogCategory.NETWORK, msg)

    @property
    def session_duration_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic)

    async def initialize(self) -> None:
        """Initialize database session and emit startup telemetry."""
        self.repository.start_session(self.session_id)
        await self.events.emit(
            LogCategory.SYSTEM,
            "OK -- Project Orion v2.7.1 initialized (SQLite WAL persistence active).",
        )

    async def authenticate(self, raw_token: Optional[str] = None) -> DiscordUser:
        """Configure token if provided and verify connection with Discord API."""
        if raw_token is not None and raw_token.strip():
            self.authenticator.set_token(raw_token.strip())
            self.config.demo_mode = False
        elif not self.authenticator.is_configured and not self.config.demo_mode:
            raise OrionError("Please provide a valid Discord authentication token or enable demo mode.")

        user = await self.client.verify_account()
        self.repository.update_session_state(
            self.session_id,
            account_id=user.id,
            account_tag=user.display_tag,
        )
        await self.events.emit(
            LogCategory.NETWORK,
            f"OK -- Authenticated with Discord API v9 as {user.display_tag}.",
        )
        return user

    async def discover_quests(self) -> list[QuestModel]:
        """Run quest discovery, persist metadata to SQLite, and log Orbs vs Others breakdown."""
        await self.events.emit(
            LogCategory.DISCOVERY,
            "SCANNING -- Fetching /api/v9/quests/@me + Quest Home decisions...",
        )
        quests = await self.discovery.discover_quests()
        completed_count = 0
        for quest in quests:
            self.repository.upsert_quest_metadata(quest)
            if quest.is_completed:
                completed_count += 1

        visible = self.discovery.visible_quests
        if visible and (
            self.selected_quest_id is None
            or not any(q.id == self.selected_quest_id for q in visible)
        ):
            self.selected_quest_id = visible[0].id

        metrics = self.discovery.compute_summary_metrics(
            self.task_manager.get_task_states_map()
        )
        self.repository.update_session_state(
            self.session_id,
            quests_discovered=metrics["total"],
            quests_completed=completed_count,
        )
        actionable_quests = [q for q in visible if q.is_actionable]
        video_count = sum(
            1
            for q in actionable_quests
            if q.primary_task.category
            in {QuestCategory.WATCH_VIDEO, QuestCategory.WATCH_VIDEO_ON_MOBILE}
        )
        play_count = len(actionable_quests) - video_count
        await self.events.emit(
            LogCategory.QUEST,
            f"OK -- Found {metrics['available']} available quests "
            f"({video_count} Video, {play_count} Play · {metrics['orbs_available']} Orbs quests [{metrics['total_orbs_pool']} Orbs] · "
            f"{metrics['others_available']} Other quests · {metrics['total']} total in history).",
        )
        return quests

    async def shutdown(self) -> None:
        """Gracefully stop monitoring tasks, mark session closed, and close HTTP session."""
        await self.task_manager.stop_active()
        self.repository.update_session_state(self.session_id, ended=True)
        await self.client.close()
        await self.events.emit(LogCategory.SYSTEM, "OK -- Session closed cleanly.")
