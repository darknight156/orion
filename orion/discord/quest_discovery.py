"""Quest discovery, Orbs vs Others queue filtering, and fast-first ordering service."""

from __future__ import annotations

from typing import Any, Optional

from orion.discord.client import DiscordAPIClient
from orion.discord.quest_models import (
    QuestCategory,
    QuestLifecycleStatus,
    QuestModel,
)


def _quest_execution_priority(q: QuestModel) -> tuple[int, int, float, str]:
    """
    Order quests so fast Video Quests (28-30s) complete first, then Activity quests,
    then Desktop Play quests, prioritizing quests with less remaining time.
    """
    cat_rank = {
        QuestCategory.WATCH_VIDEO: 0,
        QuestCategory.WATCH_VIDEO_ON_MOBILE: 1,
        QuestCategory.PLAY_ACTIVITY: 2,
        QuestCategory.PLAY_ON_DESKTOP: 3,
        QuestCategory.STREAM_ON_DESKTOP: 4,
    }.get(q.primary_task.category, 9)
    actionable_rank = 0 if q.is_actionable else (1 if q.is_completed and not q.claimed_at else 2)
    return (actionable_rank, cat_rank, q.remaining_seconds, q.title.lower())


class QuestDiscoveryService:
    """
    Fetches, parses, filters, and caches Discord Quests for the authenticated session.
    Queries both `/quests/@me` and `/quests/get-decisions` / `/quests/decision` placements so
    un-enrolled Video Quests (e.g., 200 Orbs Video Quests on Quest Home) are always discovered.
    """

    def __init__(self, client: DiscordAPIClient) -> None:
        self.client = client
        self._quests_by_id: dict[str, QuestModel] = {}
        self.hide_expired_and_claimed: bool = True

    @property
    def cached_quests(self) -> list[QuestModel]:
        all_items = [q for q in self._quests_by_id.values() if not q.is_preview]
        all_items.sort(key=_quest_execution_priority)
        return all_items

    @property
    def visible_quests(self) -> list[QuestModel]:
        """Return quests filtered by current display policy (hides expired/claimed/completed clutter)."""
        items = self.cached_quests
        if not self.hide_expired_and_claimed:
            return items
        actionable = [q for q in items if q.is_actionable]
        return actionable if actionable else items

    def _ingest_raw_quest_items(self, raw_quests: list[Any]) -> None:
        for raw_item in raw_quests:
            if not isinstance(raw_item, dict):
                continue
            quest = QuestModel.from_discord_payload(raw_item)
            if quest.id == "unknown" or quest.is_preview:
                continue
            existing = self._quests_by_id.get(quest.id)
            if existing is None:
                self._quests_by_id[quest.id] = quest
            else:
                # Preserve more complete user_status / progress if already present
                if quest.enrolled_at is not None and existing.enrolled_at is None:
                    self._quests_by_id[quest.id] = quest
                elif quest.primary_task.current_seconds > existing.primary_task.current_seconds:
                    self._quests_by_id[quest.id] = quest
                elif quest.metadata_sealed and not existing.metadata_sealed:
                    existing.metadata_sealed = quest.metadata_sealed
                    existing.traffic_metadata_sealed = quest.traffic_metadata_sealed

    async def discover_quests(self, *, probe_decisions: bool = True) -> list[QuestModel]:
        """Fetch quests from Discord API (`/quests/@me` + Quest Home ad decisions) and normalize."""
        if self.client.config.demo_mode:
            payload = self.client.build_demo_quests_payload()
            raw_quests = payload.get("quests") or []
            self._quests_by_id.clear()
            self._ingest_raw_quest_items(raw_quests)
            return self.cached_quests

        payload = await self.client.request("GET", "/quests/@me")
        if isinstance(payload, dict):
            raw_quests = payload.get("quests") or payload.get("data") or []
        elif isinstance(payload, list):
            raw_quests = payload
        else:
            raw_quests = []

        self._quests_by_id.clear()
        self._ingest_raw_quest_items(raw_quests)

        # Also query Quest Home ad-decision placements so un-enrolled Video Quests (Watch 28s / 30s+) are discovered
        if probe_decisions:
            decision_items = await self.client.fetch_quest_decisions()
            if decision_items:
                self._ingest_raw_quest_items(decision_items)

        return self.cached_quests

    async def refresh_quest(self, quest_id: str) -> Optional[QuestModel]:
        """Refresh progress for a specific quest via `/quests/@me` without re-probing ad placements."""
        await self.discover_quests(probe_decisions=False)
        return self._quests_by_id.get(quest_id)

    def get_quest(self, quest_id: str) -> Optional[QuestModel]:
        return self._quests_by_id.get(quest_id)

    def get_orbs_quests(self, *, only_actionable: bool = True) -> list[QuestModel]:
        """Return all Orbs quests sorted fastest-first (Option 1: Orbs Queue)."""
        items = [
            q
            for q in self.cached_quests
            if q.is_orbs_quest and (not only_actionable or q.is_actionable)
        ]
        items.sort(key=_quest_execution_priority)
        return items

    def get_others_then_orbs_quests(self, *, only_actionable: bool = True) -> list[QuestModel]:
        """Return all non-Orbs quests first, followed by Orbs quests (Option 2: Others -> Orbs)."""
        candidates = [
            q for q in self.cached_quests if (not only_actionable or q.is_actionable)
        ]
        others = [q for q in candidates if not q.is_orbs_quest]
        orbs = [q for q in candidates if q.is_orbs_quest]
        others.sort(key=_quest_execution_priority)
        orbs.sort(key=_quest_execution_priority)
        return others + orbs

    def filter_quests(
        self,
        *,
        category: Optional[QuestCategory] = None,
        only_available: bool = False,
        only_completed: bool = False,
        only_supported: bool = False,
        only_orbs: bool = False,
        include_expired: bool = True,
    ) -> list[QuestModel]:
        """Filter cached quests by category, Orbs status, lifecycle state, or support."""
        results: list[QuestModel] = []
        for quest in self.cached_quests:
            if not include_expired and quest.is_expired:
                continue
            if category is not None and quest.primary_task.category != category:
                continue
            if only_orbs and not quest.is_orbs_quest:
                continue
            if only_completed and not quest.is_completed:
                continue
            if only_available and not quest.is_actionable:
                continue
            if only_supported and not quest.is_supported:
                continue
            results.append(quest)
        return results

    def compute_summary_metrics(
        self, active_task_states: Optional[dict[str, str]] = None
    ) -> dict[str, int]:
        """Calculate summary counters for actionable Orbs vs Others quests."""
        states = active_task_states or {}
        total = len(self._quests_by_id)
        available = 0
        active = 0
        completed = 0
        orbs_available = 0
        others_available = 0
        total_orbs_pool = 0
        video_available = 0
        play_available = 0

        for q in self.cached_quests:
            status = q.compute_lifecycle_status(states.get(q.id))
            if status in {QuestLifecycleStatus.COMPLETED, QuestLifecycleStatus.CLAIMED}:
                completed += 1
            elif status == QuestLifecycleStatus.ACTIVE:
                active += 1
                available += 1
                if q.is_video_quest:
                    video_available += 1
                else:
                    play_available += 1
                if q.is_orbs_quest:
                    orbs_available += 1
                    total_orbs_pool += q.orb_amount
                else:
                    others_available += 1
            elif status in {
                QuestLifecycleStatus.AVAILABLE,
                QuestLifecycleStatus.ENROLLED,
                QuestLifecycleStatus.PAUSED,
            }:
                available += 1
                if q.is_video_quest:
                    video_available += 1
                else:
                    play_available += 1
                if q.is_orbs_quest:
                    orbs_available += 1
                    total_orbs_pool += q.orb_amount
                else:
                    others_available += 1

        return {
            "total": total,
            "available": available,
            "active": active,
            "completed": completed,
            "orbs_available": orbs_available,
            "others_available": others_available,
            "total_orbs_pool": total_orbs_pool,
            "video_available": video_available,
            "play_available": play_available,
        }
