"""Validated models, Orbs reward detection, and category classifications for Discord Quests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Optional

_ORBS_REGEX = re.compile(r"(\d+)\s*orb", re.IGNORECASE)


def _pick(d: Optional[dict[str, Any]], *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


class QuestCategory(str, Enum):
    """Supported and recognized Discord Quest task categories."""

    WATCH_VIDEO = "WATCH_VIDEO"
    WATCH_VIDEO_ON_MOBILE = "WATCH_VIDEO_ON_MOBILE"
    PLAY_ON_DESKTOP = "PLAY_ON_DESKTOP"
    STREAM_ON_DESKTOP = "STREAM_ON_DESKTOP"
    PLAY_ACTIVITY = "PLAY_ACTIVITY"
    ACHIEVEMENT_BASED = "ACHIEVEMENT_BASED"
    CONSOLE_BASED = "CONSOLE_BASED"
    UNKNOWN = "UNKNOWN"


class QuestSupportStatus(str, Enum):
    """Explicitly classifies whether a Quest can be tracked locally or is server-managed."""

    SUPPORTED_MONITOR = "SUPPORTED_MONITOR"
    MOBILE_CLIENT_REQUIRED = "MOBILE_CLIENT_REQUIRED"
    SERVER_MANAGED = "SERVER_MANAGED"
    UNSUPPORTED_TASK = "UNSUPPORTED_TASK"


class QuestLifecycleStatus(str, Enum):
    """High-level status of a Quest for the authenticated account."""

    AVAILABLE = "AVAILABLE"
    ENROLLED = "ENROLLED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CLAIMED = "CLAIMED"
    EXPIRED = "EXPIRED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class DiscordUser:
    """Authenticated Discord account metadata."""

    id: str
    username: str
    global_name: Optional[str] = None
    discriminator: str = "0"
    avatar: Optional[str] = None
    verified: bool = True

    @property
    def display_tag(self) -> str:
        if self.discriminator and self.discriminator != "0":
            return f"{self.username}#{self.discriminator}"
        return self.global_name or self.username

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> "DiscordUser":
        return cls(
            id=str(data.get("id", "0")),
            username=str(data.get("username", "unknown")),
            global_name=data.get("global_name"),
            discriminator=str(data.get("discriminator", "0")),
            avatar=data.get("avatar"),
            verified=bool(data.get("verified", True)),
        )


@dataclass
class QuestReward:
    """Reward specification for completing a Quest (distinguishes Orbs vs Decorations/Other)."""

    name: str
    reward_type: int = 3
    sku_id: Optional[str] = None
    name_with_article: Optional[str] = None
    orb_quantity: Optional[int] = None
    expires_at: Optional[datetime] = None

    @property
    def is_orbs(self) -> bool:
        if self.orb_quantity is not None and self.orb_quantity > 0:
            return True
        if self.reward_type == 4:
            return True
        return bool(_ORBS_REGEX.search(self.name) or "orb" in self.name.lower())

    @property
    def resolved_orb_amount(self) -> int:
        if self.orb_quantity is not None and self.orb_quantity > 0:
            return self.orb_quantity
        match = _ORBS_REGEX.search(self.name)
        if match:
            return int(match.group(1))
        return 200 if self.reward_type == 4 else 0


@dataclass
class QuestTaskRequirement:
    """Individual task requirement inside a Quest configuration."""

    raw_event_name: str
    category: QuestCategory
    target_seconds: float
    current_seconds: float = 0.0
    application_id: Optional[str] = None
    completed_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    support_status: QuestSupportStatus = QuestSupportStatus.SUPPORTED_MONITOR
    support_reason: str = ""


@dataclass
class QuestModel:
    """Normalized representation of a Discord Quest, its requirements, and user progress."""

    id: str
    title: str
    application_id: str
    application_name: str
    publisher: str
    starts_at: datetime
    expires_at: datetime
    reward: QuestReward
    primary_task: QuestTaskRequirement
    enrolled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    is_preview: bool = False
    metadata_sealed: Optional[str] = None
    traffic_metadata_sealed: Optional[str] = None
    all_tasks: list[QuestTaskRequirement] = field(default_factory=list)

    @property
    def is_orbs_quest(self) -> bool:
        return self.reward.is_orbs

    @property
    def orb_amount(self) -> int:
        return self.reward.resolved_orb_amount

    @property
    def is_video_quest(self) -> bool:
        return self.primary_task.category in {
            QuestCategory.WATCH_VIDEO,
            QuestCategory.WATCH_VIDEO_ON_MOBILE,
        }

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.primary_task.target_seconds - self.primary_task.current_seconds)

    @property
    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        exp = (
            self.expires_at
            if self.expires_at.tzinfo
            else self.expires_at.replace(tzinfo=timezone.utc)
        )
        return now >= exp

    @property
    def is_completed(self) -> bool:
        return (
            self.completed_at is not None
            or self.primary_task.completed_at is not None
            or (
                self.primary_task.target_seconds > 0
                and self.primary_task.current_seconds >= self.primary_task.target_seconds
            )
        )

    @property
    def is_actionable(self) -> bool:
        """True if the quest is neither preview, expired, completed, nor claimed."""
        return (
            not self.is_preview
            and not self.is_expired
            and not self.is_completed
            and self.claimed_at is None
        )

    @property
    def is_supported(self) -> bool:
        return self.primary_task.support_status in {
            QuestSupportStatus.SUPPORTED_MONITOR,
            QuestSupportStatus.MOBILE_CLIENT_REQUIRED,
        }

    @property
    def progress_percentage(self) -> float:
        if self.primary_task.target_seconds <= 0:
            return 100.0 if self.is_completed else 0.0
        ratio = (self.primary_task.current_seconds / self.primary_task.target_seconds) * 100.0
        return round(max(0.0, min(100.0, ratio)), 1)

    def compute_lifecycle_status(self, active_task_state: Optional[str] = None) -> QuestLifecycleStatus:
        if self.claimed_at is not None:
            return QuestLifecycleStatus.CLAIMED
        if self.is_completed:
            return QuestLifecycleStatus.COMPLETED
        if self.is_expired:
            return QuestLifecycleStatus.EXPIRED
        if self.primary_task.support_status == QuestSupportStatus.UNSUPPORTED_TASK:
            return QuestLifecycleStatus.UNSUPPORTED
        if active_task_state == "RUNNING":
            return QuestLifecycleStatus.ACTIVE
        if active_task_state == "PAUSED":
            return QuestLifecycleStatus.PAUSED
        if self.enrolled_at is not None or self.primary_task.current_seconds > 0:
            return QuestLifecycleStatus.ENROLLED
        return QuestLifecycleStatus.AVAILABLE

    @classmethod
    def classify_task_event(cls, event_name: str) -> tuple[QuestCategory, QuestSupportStatus, str]:
        normalized = event_name.strip().upper()
        if normalized == "WATCH_VIDEO":
            return (
                QuestCategory.WATCH_VIDEO,
                QuestSupportStatus.SUPPORTED_MONITOR,
                "Video quest progress reported via /video-progress.",
            )
        if normalized == "WATCH_VIDEO_ON_MOBILE":
            return (
                QuestCategory.WATCH_VIDEO_ON_MOBILE,
                QuestSupportStatus.MOBILE_CLIENT_REQUIRED,
                "Mobile video quest progress reported via /video-progress.",
            )
        if normalized in {"PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2"}:
            return (
                QuestCategory.PLAY_ON_DESKTOP,
                QuestSupportStatus.SUPPORTED_MONITOR,
                "Desktop play session progress reported via /heartbeat.",
            )
        if normalized == "STREAM_ON_DESKTOP":
            return (
                QuestCategory.STREAM_ON_DESKTOP,
                QuestSupportStatus.SUPPORTED_MONITOR,
                "Desktop stream progress reported via /heartbeat.",
            )
        if normalized == "PLAY_ACTIVITY":
            return (
                QuestCategory.PLAY_ACTIVITY,
                QuestSupportStatus.SUPPORTED_MONITOR,
                "Embedded Discord Activity progress reported via /heartbeat.",
            )
        if "VIDEO" in normalized:
            return (
                QuestCategory.WATCH_VIDEO,
                QuestSupportStatus.SUPPORTED_MONITOR,
                "Video quest progress reported via /video-progress.",
            )
        if "ACHIEVEMENT" in normalized or normalized in {"IN_GAME_EVENT", "COMPLETE_MATCH"}:
            return (
                QuestCategory.ACHIEVEMENT_BASED,
                QuestSupportStatus.SERVER_MANAGED,
                "Server-managed achievement verified by game publisher backend.",
            )
        if any(tag in normalized for tag in ("XBOX", "PLAYSTATION", "PS5", "CONSOLE", "SWITCH")):
            return (
                QuestCategory.CONSOLE_BASED,
                QuestSupportStatus.SERVER_MANAGED,
                "Console presence verified via linked console network bridge.",
            )
        return (
            QuestCategory.UNKNOWN,
            QuestSupportStatus.UNSUPPORTED_TASK,
            f"Unrecognized or server-restricted task type '{event_name}'.",
        )

    @classmethod
    def from_discord_payload(cls, payload: dict[str, Any]) -> "QuestModel":
        metadata_sealed = _pick(payload, "metadata_sealed", "metadataSealed")
        traffic_metadata_sealed = _pick(payload, "traffic_metadata_sealed", "trafficMetadataSealed")

        # Handle ad-decision wrappers where `quest` or `creative.creative_content` holds the quest
        if "quest" in payload and isinstance(payload["quest"], dict):
            inner = dict(payload["quest"])
            if metadata_sealed and "metadata_sealed" not in inner:
                inner["metadata_sealed"] = metadata_sealed
            if traffic_metadata_sealed and "traffic_metadata_sealed" not in inner:
                inner["traffic_metadata_sealed"] = traffic_metadata_sealed
            payload = inner
        elif "creative" in payload and isinstance(payload["creative"], dict):
            creative = payload["creative"]
            content = _pick(creative, "creative_content", "creativeContent")
            if isinstance(content, dict):
                inner = dict(content)
                if metadata_sealed:
                    inner["metadata_sealed"] = metadata_sealed
                if traffic_metadata_sealed:
                    inner["traffic_metadata_sealed"] = traffic_metadata_sealed
                payload = inner

        # Support both wrapped `{id, config, user_status}` and direct `QuestConfig` objects
        raw_config = payload.get("config")
        config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else payload
        quest_id = str(payload.get("id") or config.get("id") or "unknown")
        user_status = _pick(payload, "user_status", "userStatus") or _pick(config, "user_status", "userStatus") or {}
        if not isinstance(user_status, dict):
            user_status = {}
        is_preview = bool(payload.get("preview") or config.get("preview"))

        messages = config.get("messages") or {}
        app_info = config.get("application") or {}
        title = (
            _pick(messages, "quest_name", "questName")
            or _pick(messages, "game_title", "gameTitle")
            or app_info.get("name")
            or f"Quest {quest_id}"
        )
        legacy_app_id = str(app_info.get("id") or "0")
        app_name = (
            app_info.get("name")
            or _pick(messages, "game_title", "gameTitle")
            or "Unknown Application"
        )
        publisher = _pick(messages, "game_publisher", "gamePublisher") or "Discord Partner"

        starts_at = (
            _parse_iso_datetime(_pick(config, "starts_at", "startsAt"))
            or datetime.now(timezone.utc)
        )
        expires_at = (
            _parse_iso_datetime(_pick(config, "expires_at", "expiresAt"))
            or datetime.now(timezone.utc)
        )

        enrolled_at = _parse_iso_datetime(_pick(user_status, "enrolled_at", "enrolledAt"))
        completed_at = _parse_iso_datetime(_pick(user_status, "completed_at", "completedAt"))
        claimed_at = _parse_iso_datetime(_pick(user_status, "claimed_at", "claimedAt"))

        rewards_cfg = _pick(config, "rewards_config", "rewardsConfig") or {}
        rewards_list = rewards_cfg.get("rewards") or config.get("rewards") or []
        if not isinstance(rewards_list, list):
            rewards_list = []

        # Sum orb_quantity across all reward entries (matching nyxxbit/discord-quest-completer questRewards.ts)
        total_orb_qty = 0
        has_type_4 = False
        first_reward: dict[str, Any] = rewards_list[0] if rewards_list else {}
        best_reward: dict[str, Any] = first_reward

        for r_item in rewards_list:
            if not isinstance(r_item, dict):
                continue
            r_type = int(r_item.get("type", 3))
            r_orbs = _pick(r_item, "orb_quantity", "orbQuantity")
            if r_type == 4:
                has_type_4 = True
                best_reward = r_item
            if r_orbs is not None and int(r_orbs) > 0:
                total_orb_qty += int(r_orbs)
                best_reward = r_item

        reward_messages = best_reward.get("messages") or {}
        reward_type = 4 if (has_type_4 or total_orb_qty > 0) else int(best_reward.get("type", 3))
        reward_name = (
            _pick(reward_messages, "name", "name_with_article", "nameWithArticle")
            or best_reward.get("name")
            or ("Discord Orbs" if reward_type == 4 else "Quest Reward")
        )
        if total_orb_qty > 0 and "orb" not in str(reward_name).lower():
            reward_name = f"{total_orb_qty} Orbs"

        reward = QuestReward(
            reward_type=reward_type,
            sku_id=_pick(best_reward, "sku_id", "skuId"),
            name=str(reward_name),
            name_with_article=_pick(reward_messages, "name_with_article", "nameWithArticle"),
            orb_quantity=total_orb_qty if total_orb_qty > 0 else None,
            expires_at=_parse_iso_datetime(_pick(rewards_cfg, "rewards_expire_at", "rewardsExpireAt")),
        )

        # Combine task_config_v2 and task_config (both snake_case and camelCase)
        tasks_map: dict[str, dict[str, Any]] = {}
        for cfg_key in ("task_config", "taskConfig", "task_config_v2", "taskConfigV2"):
            raw_cfg = config.get(cfg_key)
            if not isinstance(raw_cfg, dict):
                continue
            raw_tasks = raw_cfg.get("tasks")
            if isinstance(raw_tasks, dict):
                for k, v in raw_tasks.items():
                    if isinstance(v, dict):
                        tasks_map[str(k)] = v
            elif isinstance(raw_tasks, list):
                for item in raw_tasks:
                    if isinstance(item, dict):
                        ev = str(item.get("type") or item.get("event_name") or item.get("eventName") or "UNKNOWN")
                        tasks_map[ev] = item

        progress_dict = user_status.get("progress") or {}
        if not isinstance(progress_dict, dict):
            progress_dict = {}

        # Priority order: Video tasks always win over Desktop/Console tasks if a quest offers multiple
        def _task_sort_key(k: str) -> int:
            ku = k.upper()
            if ku == "WATCH_VIDEO":
                return 0
            if ku == "WATCH_VIDEO_ON_MOBILE" or "VIDEO" in ku:
                return 1
            if ku == "PLAY_ACTIVITY":
                return 2
            if ku in {"PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2"}:
                return 3
            if ku == "STREAM_ON_DESKTOP":
                return 4
            if "ACHIEVEMENT" in ku:
                return 5
            if any(c in ku for c in ("XBOX", "PLAYSTATION", "PS5", "CONSOLE", "SWITCH")):
                return 6
            return 99

        sorted_keys = sorted(tasks_map.keys(), key=_task_sort_key)

        parsed_tasks: list[QuestTaskRequirement] = []
        for event_key in sorted_keys:
            task_spec = tasks_map[event_key]
            raw_event = str(
                _pick(task_spec, "event_name", "eventName", "type") or event_key
            )
            target_val = float(task_spec.get("target") or 0.0)

            # Extract per-task application_id if present in task_config_v2.tasks[key].applications[0].id
            task_app_id: Optional[str] = None
            apps_list = task_spec.get("applications")
            if isinstance(apps_list, list) and apps_list and isinstance(apps_list[0], dict):
                raw_aid = apps_list[0].get("id")
                if raw_aid:
                    task_app_id = str(raw_aid)

            prog_entry = (
                progress_dict.get(raw_event)
                or progress_dict.get(event_key)
                or {}
            )
            if not isinstance(prog_entry, dict):
                prog_entry = {}
            current_val = float(prog_entry.get("value") or 0.0)
            if raw_event == "STREAM_ON_DESKTOP" and current_val == 0.0:
                current_val = float(
                    _pick(user_status, "stream_progress_seconds", "streamProgressSeconds") or 0.0
                )

            task_completed_at = _parse_iso_datetime(
                _pick(prog_entry, "completed_at", "completedAt")
            )
            heartbeat_info = prog_entry.get("heartbeat") or {}
            last_beat = (
                _parse_iso_datetime(_pick(heartbeat_info, "last_beat_at", "lastBeatAt"))
                if isinstance(heartbeat_info, dict)
                else None
            )

            category, support_status, reason = cls.classify_task_event(raw_event)
            parsed_tasks.append(
                QuestTaskRequirement(
                    raw_event_name=raw_event,
                    category=category,
                    target_seconds=target_val,
                    current_seconds=min(current_val, target_val) if target_val > 0 else current_val,
                    application_id=task_app_id or (legacy_app_id if legacy_app_id != "0" else None),
                    completed_at=task_completed_at,
                    last_heartbeat_at=last_beat,
                    support_status=support_status,
                    support_reason=reason,
                )
            )

        if not parsed_tasks:
            cat, sup, rsn = cls.classify_task_event("UNKNOWN")
            parsed_tasks.append(
                QuestTaskRequirement(
                    raw_event_name="UNKNOWN",
                    category=cat,
                    target_seconds=900.0,
                    current_seconds=0.0,
                    application_id=legacy_app_id if legacy_app_id != "0" else None,
                    support_status=sup,
                    support_reason=rsn,
                )
            )

        primary = parsed_tasks[0]
        resolved_app_id = (
            primary.application_id
            or (legacy_app_id if legacy_app_id != "0" else None)
            or "0"
        )

        return cls(
            id=quest_id,
            title=str(title),
            application_id=resolved_app_id,
            application_name=str(app_name),
            publisher=str(publisher),
            starts_at=starts_at,
            expires_at=expires_at,
            enrolled_at=enrolled_at,
            completed_at=completed_at,
            claimed_at=claimed_at,
            is_preview=is_preview,
            metadata_sealed=str(metadata_sealed) if metadata_sealed else None,
            traffic_metadata_sealed=str(traffic_metadata_sealed) if traffic_metadata_sealed else None,
            reward=reward,
            primary_task=primary,
            all_tasks=parsed_tasks,
        )


def _parse_iso_datetime(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        cleaned = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
