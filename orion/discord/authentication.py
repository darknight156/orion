"""Authentication validation and Desktop Client header preparation for Project Orion."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional
import uuid

from orion.config import SecretToken
from orion.utils.exceptions import AuthenticationError

_TOKEN_PART_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$")

_CLIENT_HEARTBEAT_SESSION_ID = str(uuid.uuid4())
_CLIENT_AD_SESSION_ID = str(uuid.uuid4())
_CLIENT_LAUNCH_ID = str(uuid.uuid4())


def build_desktop_super_properties_dict(
    user_agent: Optional[str] = None,
    heartbeat_session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return the raw Discord Desktop Client properties dictionary used for REST & Gateway."""
    ua = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) discord/1.0.9175 Chrome/132.0.6834.196 "
        "Electron/34.3.2 Safari/537.36"
    )
    return {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": "1.0.9175",
        "os_version": "10.0.22631",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "has_client_mods": False,
        "client_launch_id": _CLIENT_LAUNCH_ID,
        "browser_user_agent": ua,
        "browser_version": "34.3.2",
        "os_sdk_version": "22631",
        "client_build_number": 561532,
        "native_build_number": 59498,
        "client_event_source": None,
        "client_heartbeat_session_id": heartbeat_session_id or _CLIENT_HEARTBEAT_SESSION_ID,
    }


class Authenticator:
    """
    Manages Discord session authentication token validation and Desktop Client header generation.
    Includes X-Super-Properties and persistent session UUIDs so Discord's /quests/@me and
    /quests/get-decisions endpoints return all desktop & video quests.
    """

    def __init__(self, token: Optional[SecretToken | str] = None) -> None:
        self._token: Optional[SecretToken] = None
        self.client_heartbeat_session_id: str = _CLIENT_HEARTBEAT_SESSION_ID
        self.client_ad_session_id: str = _CLIENT_AD_SESSION_ID
        if token is not None:
            self.set_token(token)

    @property
    def is_configured(self) -> bool:
        return self._token is not None and bool(self._token.get_secret_value().strip())

    def set_token(self, token: SecretToken | str) -> None:
        raw = token.get_secret_value() if isinstance(token, SecretToken) else str(token)
        cleaned = raw.strip().strip('"').strip("'")
        if cleaned.lower().startswith("bearer "):
            cleaned = cleaned[7:].strip()
        if not cleaned or len(cleaned) < 16 or any(ch.isspace() for ch in cleaned):
            raise AuthenticationError(
                "Invalid authentication token format. Token must be at least 16 non-whitespace characters."
            )
        self._token = SecretToken(cleaned)

    def clear_token(self) -> None:
        self._token = None

    def get_raw_token(self) -> str:
        if not self._token:
            raise AuthenticationError("No authentication token has been configured.")
        return self._token.get_secret_value()

    def get_super_properties_dict(self, user_agent: Optional[str] = None) -> dict[str, Any]:
        return build_desktop_super_properties_dict(
            user_agent=user_agent,
            heartbeat_session_id=self.client_heartbeat_session_id,
        )

    def build_auth_headers(self, user_agent: str) -> dict[str, str]:
        """Construct Discord Desktop Client HTTP headers for Quest API requests."""
        token_value = self.get_raw_token()
        super_props = base64.b64encode(
            json.dumps(self.get_super_properties_dict(user_agent), separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return {
            "Authorization": token_value,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
            "X-Super-Properties": super_props,
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "UTC",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/quest-home",
        }

    def masked_fingerprint(self) -> str:
        """Return a safe masked identifier (e.g., 'MTI0••••x9Qp') for UI status display."""
        if not self._token:
            return "Unauthenticated"
        raw = self._token.get_secret_value()
        if len(raw) <= 10:
            return "••••••••"
        return f"{raw[:4]}••••{raw[-4:]}"

    @staticmethod
    def is_plausible_discord_token(raw_token: str) -> bool:
        """Check whether a token matches standard three-segment Discord token structure."""
        cleaned = raw_token.strip().strip('"').strip("'")
        if cleaned.lower().startswith("bearer "):
            cleaned = cleaned[7:].strip()
        return bool(_TOKEN_PART_PATTERN.match(cleaned)) and len(cleaned) >= 24
