"""Configuration management and validation for Project Orion (Turbo Concurrent Execution enabled)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class SecretToken:
    """Safe wrapper preventing accidental printing or logging of raw authentication tokens."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretToken('**********')"

    def __str__(self) -> str:
        return "**********"


@dataclass
class OrionConfig:
    """Validated runtime configuration for Project Orion."""

    app_name: str = "Project Orion"
    app_version: str = "2.7.1"
    api_base_url: str = "https://discord.com/api/v9"
    database_path: Path = field(default_factory=lambda: Path("orion_state.db"))
    refresh_interval_seconds: float = 20.0
    video_step_interval_seconds: float = 1.0
    max_concurrent_quests: int = 8
    request_timeout_seconds: float = 15.0
    max_retries: int = 3
    backoff_base_seconds: float = 0.8
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) discord/1.0.9175 Chrome/132.0.6834.196 "
        "Electron/34.3.2 Safari/537.36"
    )
    discord_token: Optional[SecretToken] = field(default=None, repr=False)
    demo_mode: bool = False

    def __post_init__(self) -> None:
        cleaned_url = self.api_base_url.strip().rstrip("/")
        if not cleaned_url.startswith(("https://", "http://")):
            raise ValueError("api_base_url must start with http:// or https://")
        self.api_base_url = cleaned_url
        self.database_path = Path(self.database_path)
        if isinstance(self.discord_token, str):
            self.discord_token = SecretToken(self.discord_token)
        self.refresh_interval_seconds = max(0.05, min(300.0, float(self.refresh_interval_seconds)))
        self.video_step_interval_seconds = max(0.05, min(30.0, float(self.video_step_interval_seconds)))
        self.max_concurrent_quests = max(1, min(16, int(self.max_concurrent_quests)))
        self.request_timeout_seconds = max(1.0, min(60.0, float(self.request_timeout_seconds)))
        self.max_retries = max(0, min(10, int(self.max_retries)))

    @classmethod
    def from_env(cls) -> "OrionConfig":
        """Load configuration from environment variables with Discord v9 defaults."""
        raw_token = os.getenv("ORION_DISCORD_TOKEN", "").strip()
        demo_flag = os.getenv("ORION_DEMO_MODE", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return cls(
            api_base_url=os.getenv(
                "ORION_API_BASE_URL", "https://discord.com/api/v9"
            ),
            database_path=Path(os.getenv("ORION_DATABASE_PATH", "orion_state.db")),
            refresh_interval_seconds=float(
                os.getenv("ORION_REFRESH_INTERVAL_SECONDS", "20.0")
            ),
            video_step_interval_seconds=float(
                os.getenv("ORION_VIDEO_STEP_SECONDS", "1.0")
            ),
            max_concurrent_quests=int(
                os.getenv("ORION_MAX_CONCURRENT_QUESTS", "8")
            ),
            request_timeout_seconds=float(
                os.getenv("ORION_REQUEST_TIMEOUT_SECONDS", "15.0")
            ),
            max_retries=int(os.getenv("ORION_MAX_RETRIES", "3")),
            discord_token=SecretToken(raw_token) if raw_token else None,
            demo_mode=demo_flag,
        )
