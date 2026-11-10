"""Configurable periodic asyncio scheduler with pause, resume, and clean cancellation."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional


class PeriodicScheduler:
    """Runs an async callback at a configurable interval with pause/resume support."""

    def __init__(
        self,
        interval_seconds: float,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        self.interval_seconds = max(1.0, interval_seconds)
        self._callback = callback
        self._task: Optional[asyncio.Task[None]] = None
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._stop_requested = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    def set_interval(self, interval_seconds: float) -> None:
        self.interval_seconds = max(1.0, interval_seconds)

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_requested = False
        self._resume_event.set()
        self._task = asyncio.create_task(self._run_loop())

    def pause(self) -> None:
        self._resume_event.clear()

    def resume(self) -> None:
        self._resume_event.set()

    async def stop(self) -> None:
        self._stop_requested = True
        self._resume_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        try:
            while not self._stop_requested:
                await self._resume_event.wait()
                if self._stop_requested:
                    break
                await self._callback()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            raise
