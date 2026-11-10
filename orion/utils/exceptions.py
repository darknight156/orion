"""Structured hierarchy of exceptions for Project Orion."""

from __future__ import annotations

from typing import Any, Optional


class OrionError(Exception):
    """Base exception for all Project Orion operational errors."""

    def __init__(self, message: str, *, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or "ORION_ERROR"


class ConfigurationError(OrionError):
    """Raised when configuration values or environment settings are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONFIG_ERROR")


class DiscordAPIError(OrionError):
    """Structured error representing an HTTP failure from the Discord API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        discord_code: Optional[int] = None,
        route: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, code=f"HTTP_{status_code}")
        self.status_code = status_code
        self.discord_code = discord_code
        self.route = route
        self.details = details or {}


class AuthenticationError(DiscordAPIError):
    """Raised on HTTP 401 or invalid token structure."""

    def __init__(self, message: str = "Authentication failed or token rejected.", **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 401)
        super().__init__(message, **kwargs)


class AuthorizationError(DiscordAPIError):
    """Raised on HTTP 403 when an endpoint or resource is forbidden."""

    def __init__(self, message: str = "Access to the requested Discord resource is forbidden.", **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 403)
        super().__init__(message, **kwargs)


class EndpointNotFoundError(DiscordAPIError):
    """Raised on HTTP 404 when a Quest or route does not exist."""

    def __init__(self, message: str = "Requested Discord endpoint or Quest was not found.", **kwargs: Any) -> None:
        kwargs.setdefault("status_code", 404)
        super().__init__(message, **kwargs)


class RateLimitExceededError(DiscordAPIError):
    """Raised when HTTP 429 retries are exhausted or retry_after exceeds bounds."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float,
        is_global: bool = False,
        bucket: Optional[str] = None,
        route: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=429, route=route)
        self.retry_after = retry_after
        self.is_global = is_global
        self.bucket = bucket


class DiscordServerError(DiscordAPIError):
    """Raised on HTTP 5xx responses from Discord upstream servers."""


class UnsupportedQuestTaskError(OrionError):
    """Raised when attempting to actively execute a server-managed or unsupported quest type."""

    def __init__(self, quest_id: str, task_type: str, reason: str) -> None:
        super().__init__(
            f"Quest '{quest_id}' uses task '{task_type}' which cannot be executed locally: {reason}",
            code="UNSUPPORTED_TASK",
        )
        self.quest_id = quest_id
        self.task_type = task_type
        self.reason = reason


class DatabaseError(OrionError):
    """Raised when a local SQLite persistence operation fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="DATABASE_ERROR")
