"""Asynchronous pub/sub event bus decoupling networking and task logic from UI rendering."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


class LogCategory(str, Enum):
    """Structured terminal log categories required by Project Orion."""

    SYSTEM = "SYSTEM"
    NETWORK = "NETWORK"
    DISCOVERY = "DISCOVERY"
    QUEST = "QUEST"
    TRACKER = "TRACKER"
    PROGRESS = "PROGRESS"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    DEBUG = "DEBUG"


@dataclass(frozen=True)
class OrionEvent:
    """Immutable event emitted across the application bus."""

    category: LogCategory
    message: str
    task_id: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


EventCallback = Callable[[OrionEvent], Awaitable[None] | None]


class EventManager:
    """Manages subscribers and dispatches structured OrionEvents without blocking callers."""

    def __init__(self, max_history: int = 500) -> None:
        self._subscribers: list[EventCallback] = []
        self._history: list[OrionEvent] = []
        self._max_history = max_history

    @property
    def history(self) -> list[OrionEvent]:
        return list(self._history)

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Register an event listener and return an unsubscribe callable."""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    async def emit(
        self,
        category: LogCategory,
        message: str,
        *,
        task_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> OrionEvent:
        """Emit a structured event to all registered listeners."""
        event = OrionEvent(
            category=category,
            message=message,
            task_id=task_id,
            payload=payload or {},
        )
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

        for subscriber in list(self._subscribers):
            try:
                result = subscriber(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                # Never allow a faulty UI subscriber to crash core networking
                continue
        return event
