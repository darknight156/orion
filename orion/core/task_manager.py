"""Asynchronous Quest execution & Turbo Concurrent task manager with Mode 1 (Orbs) and Mode 2 (Others -> Orbs)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from orion.core.event_manager import EventManager, LogCategory
from orion.database.repository import OrionRepository
from orion.discord.quest_discovery import QuestDiscoveryService
from orion.discord.quest_models import QuestCategory, QuestModel, _parse_iso_datetime
from orion.utils.exceptions import OrionError
from orion.utils.formatting import calculate_eta_seconds, render_unicode_bar


class TaskState(str, Enum):
    """State of a tracked Quest monitoring task."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


@dataclass
class MonitoredQuestTask:
    """State container for a Quest being tracked by the TaskManager."""

    quest_id: str
    title: str
    reward_name: str = "Quest Reward"
    is_orbs: bool = False
    category: QuestCategory = QuestCategory.PLAY_ON_DESKTOP
    state: TaskState = TaskState.QUEUED
    initial_progress_seconds: float = 0.0
    current_progress_seconds: float = 0.0
    target_seconds: float = 900.0
    accumulated_elapsed_seconds: float = 0.0
    last_resumed_monotonic: Optional[float] = None
    eta_seconds: Optional[float] = None
    error_message: Optional[str] = None
    poll_count: int = 0
    _resume_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        self._resume_event.set()

    @property
    def total_elapsed_seconds(self) -> float:
        if self.state == TaskState.RUNNING and self.last_resumed_monotonic is not None:
            return self.accumulated_elapsed_seconds + max(
                0.0, time.monotonic() - self.last_resumed_monotonic
            )
        return self.accumulated_elapsed_seconds

    @property
    def progress_gained_in_session(self) -> float:
        return max(0.0, self.current_progress_seconds - self.initial_progress_seconds)

    @property
    def percentage(self) -> float:
        if self.target_seconds <= 0:
            return 100.0
        return round(
            max(0.0, min(100.0, (self.current_progress_seconds / self.target_seconds) * 100.0)),
            1,
        )


class QuestTaskManager:
    """
    Coordinates Quest progress execution & monitoring with Turbo Concurrent Execution:
    - Completes Video Quests via `/quests/{id}/video-progress` respecting Discord's server window.
    - Runs multiple Play/Activity Quests concurrently in parallel with Gateway Presence + 20s heartbeats.
    """

    def __init__(
        self,
        *,
        discovery: QuestDiscoveryService,
        repository: OrionRepository,
        event_manager: EventManager,
        session_id: str,
        poll_interval_seconds: float = 20.0,
    ) -> None:
        self.discovery = discovery
        self.repository = repository
        self.events = event_manager
        self.session_id = session_id
        self.poll_interval_seconds = max(0.05, poll_interval_seconds)

        self._tasks: dict[str, MonitoredQuestTask] = {}
        self._queue: list[str] = []
        self._active_quest_id: Optional[str] = None
        self._worker_task: Optional[asyncio.Task[None]] = None
        self.active_batch_mode: str = "IDLE"

    @property
    def active_task(self) -> Optional[MonitoredQuestTask]:
        if self._active_quest_id and self._active_quest_id in self._tasks:
            t = self._tasks[self._active_quest_id]
            if t.state in {TaskState.RUNNING, TaskState.PAUSED}:
                return t
        for t in self._tasks.values():
            if t.state in {TaskState.RUNNING, TaskState.PAUSED}:
                return t
        if self._active_quest_id:
            return self._tasks.get(self._active_quest_id)
        return None

    @property
    def is_running_or_queued(self) -> bool:
        if self._worker_task is not None and not self._worker_task.done():
            return True
        return bool(self._queue) or any(
            t.state in {TaskState.RUNNING, TaskState.PAUSED} for t in self._tasks.values()
        )

    @property
    def queued_quest_ids(self) -> list[str]:
        return list(self._queue)

    def get_task(self, quest_id: str) -> Optional[MonitoredQuestTask]:
        return self._tasks.get(quest_id)

    def get_task_states_map(self) -> dict[str, str]:
        return {qid: t.state.value for qid, t in self._tasks.items()}

    async def wait_until_queue_complete(self) -> None:
        """Wait for the active queue worker task to finish processing all queued quests."""
        if self._worker_task is not None and not self._worker_task.done():
            await self._worker_task

    async def start_orbs_batch(self) -> list[MonitoredQuestTask]:
        """
        Option 1 (ORBS): Automatically queue and complete all actionable Orbs quests
        concurrently in Turbo Mode.
        """
        await self.stop_active()
        self.active_batch_mode = "MODE 1: ORBS (TURBO CONCURRENT)"
        orbs_quests = self.discovery.get_orbs_quests(only_actionable=True)
        if not orbs_quests:
            await self.events.emit(
                LogCategory.WARNING,
                "OK -- No actionable Orbs quests remaining on this account.",
            )
            return []

        total_orbs = sum(q.orb_amount for q in orbs_quests)
        max_conc = getattr(self.discovery.client.config, "max_concurrent_quests", 8)
        video_count = sum(
            1
            for q in orbs_quests
            if q.primary_task.category
            in {QuestCategory.WATCH_VIDEO, QuestCategory.WATCH_VIDEO_ON_MOBILE}
        )
        play_count = len(orbs_quests) - video_count
        await self.events.emit(
            LogCategory.QUEST,
            f"OK -- Mode [1] ORBS TURBO started: Running {len(orbs_quests)} Orbs quests ({video_count} Video, {play_count} Play · {total_orbs} Orbs) with {max_conc}x parallel workers.",
        )
        queued_tasks: list[MonitoredQuestTask] = []
        for q in orbs_quests:
            t = await self.enqueue_or_start(q)
            queued_tasks.append(t)
        return queued_tasks

    async def start_others_then_orbs_batch(self) -> list[MonitoredQuestTask]:
        """
        Option 2 (OTHERS -> ORBS): Queue all non-Orbs quests (Avatar Decorations, etc.) first,
        then all Orbs quests in Turbo Mode.
        """
        await self.stop_active()
        self.active_batch_mode = "MODE 2: OTHERS -> ORBS (TURBO)"
        ordered_quests = self.discovery.get_others_then_orbs_quests(only_actionable=True)
        if not ordered_quests:
            await self.events.emit(
                LogCategory.WARNING,
                "OK -- No actionable quests remaining to queue.",
            )
            return []

        others_count = sum(1 for q in ordered_quests if not q.is_orbs_quest)
        orbs_count = sum(1 for q in ordered_quests if q.is_orbs_quest)
        await self.events.emit(
            LogCategory.QUEST,
            f"OK -- Mode [2] OTHERS -> ORBS TURBO started: Queued {others_count} Other quest(s) + {orbs_count} Orbs quest(s).",
        )
        queued_tasks: list[MonitoredQuestTask] = []
        for q in ordered_quests:
            t = await self.enqueue_or_start(q)
            queued_tasks.append(t)
        return queued_tasks

    async def enqueue_or_start(self, quest: QuestModel) -> MonitoredQuestTask:
        """Start processing a Quest immediately or add it to the parallel worker queue."""
        existing = self._tasks.get(quest.id)
        if existing and existing.state in {TaskState.RUNNING, TaskState.PAUSED, TaskState.QUEUED}:
            return existing

        # In Live API mode, always trust Discord's authoritative server progress rather than stale local SQLite snapshots
        if self.discovery.client.config.demo_mode:
            snapshot = self.repository.get_latest_snapshot(quest.id)
            initial_progress = (
                max(quest.primary_task.current_seconds, snapshot.current_seconds)
                if snapshot and snapshot.current_seconds < quest.primary_task.target_seconds
                else quest.primary_task.current_seconds
            )
        else:
            initial_progress = quest.primary_task.current_seconds

        monitored = MonitoredQuestTask(
            quest_id=quest.id,
            title=quest.title,
            reward_name=quest.reward.name,
            is_orbs=quest.is_orbs_quest,
            category=quest.primary_task.category,
            state=TaskState.QUEUED,
            initial_progress_seconds=initial_progress,
            current_progress_seconds=initial_progress,
            target_seconds=quest.primary_task.target_seconds,
        )
        self._tasks[quest.id] = monitored
        if quest.id not in self._queue and self._active_quest_id != quest.id:
            self._queue.append(quest.id)

        tag = f"ORBS ({quest.orb_amount})" if quest.is_orbs_quest else f"OTHER ({quest.reward.name})"
        await self.events.emit(
            LogCategory.TRACKER,
            f"OK -- Queued [{tag}] {quest.title} ({quest.primary_task.category.value} · {int(initial_progress)}s/{int(quest.primary_task.target_seconds)}s)",
            task_id=quest.id,
        )
        self._ensure_worker()
        return monitored

    async def pause_active(self) -> Optional[MonitoredQuestTask]:
        task = self.active_task
        if task is None or task.state != TaskState.RUNNING:
            return None

        for t in self._tasks.values():
            if t.state == TaskState.RUNNING:
                if t.last_resumed_monotonic is not None:
                    t.accumulated_elapsed_seconds += max(
                        0.0, time.monotonic() - t.last_resumed_monotonic
                    )
                    t.last_resumed_monotonic = None
                t.state = TaskState.PAUSED
                t._resume_event.clear()

        self.repository.update_session_state(
            self.session_id,
            active_quest_id=task.quest_id,
            active_task_state=TaskState.PAUSED.value,
        )
        self.repository.record_progress_snapshot(
            session_id=self.session_id,
            quest_id=task.quest_id,
            current_seconds=task.current_progress_seconds,
            target_seconds=task.target_seconds,
            percentage=task.percentage,
            task_state=TaskState.PAUSED.value,
        )

        await self.events.emit(
            LogCategory.TRACKER,
            f"PAUSED -- {task.title} at {task.percentage:.0f}% (snapshot saved to SQLite)",
            task_id=task.quest_id,
        )
        return task

    async def resume_active(self) -> Optional[MonitoredQuestTask]:
        task = self.active_task
        if task is None or task.state != TaskState.PAUSED:
            return None

        for t in self._tasks.values():
            if t.state == TaskState.PAUSED:
                t.state = TaskState.RUNNING
                t.last_resumed_monotonic = time.monotonic()
                t._resume_event.set()

        self.repository.update_session_state(
            self.session_id,
            active_quest_id=task.quest_id,
            active_task_state=TaskState.RUNNING.value,
        )

        await self.events.emit(
            LogCategory.TRACKER,
            f"RESUMED -- {task.title} ({int(task.current_progress_seconds)}s / {int(task.target_seconds)}s)",
            task_id=task.quest_id,
        )
        return task

    async def stop_active(self) -> Optional[MonitoredQuestTask]:
        task = self.active_task
        self._queue.clear()
        self.active_batch_mode = "IDLE"
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        for t in self._tasks.values():
            if t.state in {TaskState.RUNNING, TaskState.PAUSED, TaskState.QUEUED}:
                if t.last_resumed_monotonic is not None:
                    t.accumulated_elapsed_seconds += max(
                        0.0, time.monotonic() - t.last_resumed_monotonic
                    )
                    t.last_resumed_monotonic = None
                t.state = TaskState.STOPPED
                t._resume_event.set()

        if task is not None:
            self.repository.update_session_state(
                self.session_id,
                active_quest_id=None,
                active_task_state=TaskState.STOPPED.value,
            )
            await self.events.emit(
                LogCategory.TRACKER,
                f"STOPPED -- Halted tracking for {task.title}",
                task_id=task.quest_id,
            )

        self._active_quest_id = None
        return task

    async def recover_previous_session(self) -> Optional[MonitoredQuestTask]:
        recoverable = self.repository.get_recoverable_session()
        if not recoverable or not recoverable.active_quest_id:
            return None
        quest = self.discovery.get_quest(recoverable.active_quest_id)
        if not quest or quest.is_completed or quest.is_expired:
            return None

        await self.events.emit(
            LogCategory.SYSTEM,
            f"OK -- Recovered interrupted session ({recoverable.session_id}) for {quest.title}",
            task_id=quest.id,
        )
        task = await self.enqueue_or_start(quest)
        if recoverable.active_task_state == TaskState.PAUSED.value:
            await asyncio.sleep(0.02)
            await self.pause_active()
        return task

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._process_queue())

    async def _run_task_entry(self, quest_id: str, stagger_delay: float = 0.0) -> None:
        if stagger_delay > 0:
            await asyncio.sleep(stagger_delay)
        task = self._tasks.get(quest_id)
        if task is None or task.state == TaskState.STOPPED:
            return

        if self._active_quest_id is None:
            self._active_quest_id = quest_id
        task.state = TaskState.RUNNING
        task.last_resumed_monotonic = time.monotonic()
        task._resume_event.set()

        self.repository.update_session_state(
            self.session_id,
            active_quest_id=task.quest_id,
            active_task_state=TaskState.RUNNING.value,
        )

        quest_obj = self.discovery.get_quest(task.quest_id)
        if quest_obj and quest_obj.enrolled_at is None and not quest_obj.is_expired:
            try:
                enroll_resp = await self.discovery.client.enroll_quest(
                    quest_obj.id,
                    metadata_sealed=quest_obj.metadata_sealed,
                    traffic_metadata_sealed=quest_obj.traffic_metadata_sealed,
                )
                if isinstance(enroll_resp, dict):
                    enrolled_raw = enroll_resp.get("enrolled_at") or enroll_resp.get("enrolledAt")
                    quest_obj.enrolled_at = _parse_iso_datetime(enrolled_raw) or datetime.now(
                        timezone.utc
                    )
                else:
                    quest_obj.enrolled_at = datetime.now(timezone.utc)
                await self.events.emit(
                    LogCategory.NETWORK,
                    f"OK -- Enrolled in '{quest_obj.title}' [{task.reward_name}]",
                    task_id=quest_obj.id,
                )
            except OrionError as exc:
                quest_obj.enrolled_at = datetime.now(timezone.utc)
                await self.events.emit(
                    LogCategory.WARNING,
                    f"OK -- Enroll check for '{quest_obj.title}': {exc.message}",
                    task_id=quest_obj.id,
                )

        await self.events.emit(
            LogCategory.TRACKER,
            f"COMPLETING -- Starting {task.title} [{task.reward_name}] ({task.category.value})",
            task_id=task.quest_id,
        )

        await self._monitor_single_task(task)
        if self._active_quest_id == quest_id:
            self._active_quest_id = None

    async def _process_queue(self) -> None:
        try:
            max_conc = getattr(self.discovery.client.config, "max_concurrent_quests", 8)
            semaphore = asyncio.Semaphore(max(1, max_conc))
            running_futures: list[asyncio.Task[None]] = []

            async def _bounded_runner(qid: str, delay: float) -> None:
                async with semaphore:
                    await self._run_task_entry(qid, stagger_delay=delay)

            idx = 0
            while self._queue or running_futures:
                while self._queue:
                    next_qid = self._queue.pop(0)
                    stagger = 0.0 if self.discovery.client.config.demo_mode else min(1.5, idx * 0.25)
                    idx += 1
                    fut = asyncio.create_task(_bounded_runner(next_qid, stagger))
                    running_futures.append(fut)

                if running_futures:
                    done, pending = await asyncio.wait(
                        running_futures, return_when=asyncio.FIRST_COMPLETED
                    )
                    running_futures = list(pending)
                    for d in done:
                        try:
                            await d
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass

            self.active_batch_mode = "COMPLETED"
            await self.events.emit(
                LogCategory.SUCCESS,
                "OK -- All queued quests have finished processing!",
            )
        except asyncio.CancelledError:
            for fut in running_futures:
                fut.cancel()
            raise

    @staticmethod
    def _extract_progress_from_response(
        resp: dict[str, Any], event_name: str, fallback: float
    ) -> tuple[float, bool]:
        """Extract updated progress seconds and completion flag from a heartbeat/video-progress response."""
        if not isinstance(resp, dict):
            return fallback, False
        user_status = resp.get("user_status") or resp.get("userStatus")
        if isinstance(user_status, dict):
            resp = user_status
        completed = bool(resp.get("completed_at") or resp.get("completedAt"))
        progress_map = resp.get("progress")
        if isinstance(progress_map, dict):
            entry = progress_map.get(event_name)
            if not isinstance(entry, dict) and progress_map:
                first_val = next(iter(progress_map.values()))
                if isinstance(first_val, dict):
                    entry = first_val
            if isinstance(entry, dict):
                raw_val = entry.get("value")
                val = float(raw_val) if raw_val is not None else fallback
                if entry.get("completed_at") or entry.get("completedAt"):
                    completed = True
                return val, completed
        stream_progress = resp.get("stream_progress_seconds") or resp.get("streamProgressSeconds")
        if stream_progress is not None:
            return float(stream_progress), completed
        return fallback, completed

    async def _monitor_single_task(self, task: MonitoredQuestTask) -> None:
        is_video = task.category in {
            QuestCategory.WATCH_VIDEO,
            QuestCategory.WATCH_VIDEO_ON_MOBILE,
        }
        quest_obj = self.discovery.get_quest(task.quest_id)
        app_id = quest_obj.application_id if quest_obj else "0"
        app_name = quest_obj.application_name if quest_obj else task.title
        enrolled_ts = (
            quest_obj.enrolled_at.timestamp()
            if (quest_obj and quest_obj.enrolled_at is not None)
            else time.time()
        )
        video_initialized = task.current_progress_seconds > 0

        if not is_video:
            await self.discovery.client.register_active_game(task.quest_id, app_id, app_name)

        try:
            while task.state in {TaskState.RUNNING, TaskState.PAUSED}:
                await task._resume_event.wait()
                if task.state not in {TaskState.RUNNING, TaskState.PAUSED}:
                    break

                try:
                    quest_obj = self.discovery.get_quest(task.quest_id)
                    event_name = (
                        quest_obj.primary_task.raw_event_name
                        if quest_obj is not None
                        else task.category.value
                    )
                    api_completed = False

                    if is_video:
                        if self.discovery.client.config.demo_mode or self.poll_interval_seconds < 0.5:
                            # Fast demo/test step (~45% per tick)
                            step = max(12.0, round(task.target_seconds * 0.45, 1))
                            next_ts = min(task.target_seconds, task.current_progress_seconds + step)
                            resp = await self.discovery.client.send_video_progress(
                                task.quest_id, next_ts
                            )
                            task.current_progress_seconds = next_ts
                            _, api_completed = self._extract_progress_from_response(
                                resp, event_name, next_ts
                            )
                            if next_ts >= task.target_seconds:
                                api_completed = True
                        else:
                            # Live Discord API Video Quest protocol:
                            # Discord allows `timestamp <= (now - enrolled_at) + 10s`
                            if not video_initialized:
                                await self.discovery.client.send_video_progress(task.quest_id, 0.0)
                                video_initialized = True

                            max_allowed = max(0.0, (time.time() - enrolled_ts) + 10.0)
                            if (
                                (max_allowed - task.current_progress_seconds) >= 7.0
                                or max_allowed >= task.target_seconds
                            ):
                                step = 7.0 + random.random()
                                next_ts = min(
                                    task.target_seconds,
                                    min(max_allowed, round(task.current_progress_seconds + step, 2)),
                                )
                                if max_allowed >= task.target_seconds and (
                                    task.target_seconds - task.current_progress_seconds
                                ) <= 9.0:
                                    next_ts = float(task.target_seconds)

                                if next_ts > task.current_progress_seconds:
                                    resp = await self.discovery.client.send_video_progress(
                                        task.quest_id, next_ts
                                    )
                                    extracted_val, api_completed = (
                                        self._extract_progress_from_response(
                                            resp, event_name, next_ts
                                        )
                                    )
                                    task.current_progress_seconds = max(next_ts, extracted_val)
                                    if task.current_progress_seconds >= task.target_seconds:
                                        final_resp = (
                                            await self.discovery.client.send_video_progress(
                                                task.quest_id, float(task.target_seconds)
                                            )
                                        )
                                        _, final_done = self._extract_progress_from_response(
                                            final_resp, event_name, float(task.target_seconds)
                                        )
                                        api_completed = True or final_done
                            else:
                                # Wait for Discord's server clock window to advance
                                await asyncio.sleep(1.0)
                                continue
                    else:
                        # Desktop / Activity / Stream Quest via POST /quests/{id}/heartbeat
                        # CRITICAL: Always send terminal=False while progress < target_seconds!
                        # Sending terminal=True before 900s closes `heartbeat.last_beat_at` on Discord's
                        # server and causes subsequent heartbeats to stay stuck at 899s/900s (99%).
                        stream_key = f"call:{task.quest_id}:1"
                        resp = await self.discovery.client.send_heartbeat(
                            task.quest_id,
                            stream_key,
                            application_id=app_id,
                            terminal=False,
                        )
                        extracted_val, api_completed = self._extract_progress_from_response(
                            resp, event_name, task.current_progress_seconds
                        )
                        if self.discovery.client.config.demo_mode:
                            if extracted_val > task.current_progress_seconds:
                                task.current_progress_seconds = min(
                                    task.target_seconds, extracted_val
                                )
                            else:
                                inc = min(225.0, max(30.0, task.target_seconds * 0.35))
                                task.current_progress_seconds = min(
                                    task.target_seconds, task.current_progress_seconds + inc
                                )
                                if task.current_progress_seconds >= task.target_seconds:
                                    api_completed = True
                        else:
                            # Live API mode: ONLY trust authoritative progress returned by Discord!
                            if extracted_val >= task.current_progress_seconds:
                                task.current_progress_seconds = min(
                                    task.target_seconds, extracted_val
                                )
                            if (
                                api_completed
                                or task.current_progress_seconds >= task.target_seconds
                            ):
                                try:
                                    await self.discovery.client.send_heartbeat(
                                        task.quest_id,
                                        stream_key,
                                        application_id=app_id,
                                        terminal=True,
                                    )
                                except Exception:
                                    pass
                                api_completed = True

                    task.poll_count += 1
                    task.eta_seconds = calculate_eta_seconds(
                        current_progress=task.current_progress_seconds,
                        required_progress=task.target_seconds,
                        elapsed_session_seconds=max(1.0, task.total_elapsed_seconds),
                        progress_gained_in_session=max(1.0, task.progress_gained_in_session),
                    )

                    self.repository.record_progress_snapshot(
                        session_id=self.session_id,
                        quest_id=task.quest_id,
                        current_seconds=task.current_progress_seconds,
                        target_seconds=task.target_seconds,
                        percentage=task.percentage,
                        task_state=task.state.value,
                    )

                    bar_str = render_unicode_bar(
                        task.current_progress_seconds, task.target_seconds, width=16
                    )
                    status_note = ""
                    if (
                        not is_video
                        and not self.discovery.client.config.demo_mode
                        and task.poll_count == 1
                        and not api_completed
                        and task.current_progress_seconds < task.target_seconds
                    ):
                        status_note = " (Heartbeat session opened · next server credit in 20s)"

                    await self.events.emit(
                        LogCategory.PROGRESS,
                        f"COMPLETING -- {bar_str} ({int(task.current_progress_seconds)}s/{int(task.target_seconds)}s) · {task.title} [{task.reward_name}]{status_note}",
                        task_id=task.quest_id,
                        payload={
                            "current": task.current_progress_seconds,
                            "target": task.target_seconds,
                            "percentage": task.percentage,
                            "eta_seconds": task.eta_seconds,
                        },
                    )

                    if api_completed or (
                        self.discovery.client.config.demo_mode
                        and task.current_progress_seconds >= task.target_seconds
                    ) or (
                        not self.discovery.client.config.demo_mode
                        and task.current_progress_seconds >= task.target_seconds
                    ):
                        task.current_progress_seconds = task.target_seconds
                        if task.last_resumed_monotonic is not None:
                            task.accumulated_elapsed_seconds += max(
                                0.0, time.monotonic() - task.last_resumed_monotonic
                            )
                            task.last_resumed_monotonic = None
                        task.state = TaskState.COMPLETED
                        task.eta_seconds = 0.0
                        if quest_obj is not None:
                            quest_obj.primary_task.current_seconds = task.target_seconds
                            self.repository.record_quest_history(
                                quest_obj, QuestStateLabel.COMPLETED
                            )
                        self.repository.update_session_state(
                            self.session_id,
                            active_quest_id=None,
                            active_task_state=TaskState.COMPLETED.value,
                        )
                        await self.events.emit(
                            LogCategory.SUCCESS,
                            f"OK -- COMPLETED '{task.title}' -> Reward ready: {task.reward_name}!",
                            task_id=task.quest_id,
                        )
                        break

                except OrionError as exc:
                    task.error_message = exc.message
                    self.repository.record_error(
                        session_id=self.session_id,
                        error_code=exc.code,
                        message=exc.message,
                    )
                    await self.events.emit(
                        LogCategory.ERROR,
                        f"ERR -- API response on '{task.title}': {exc.message}",
                        task_id=task.quest_id,
                    )
                    if exc.code in {"HTTP_401", "HTTP_403", "HTTP_404"}:
                        task.state = TaskState.ERROR
                        break

                if is_video:
                    wait_time = (
                        min(
                            self.poll_interval_seconds,
                            self.discovery.client.config.video_step_interval_seconds,
                        )
                        if self.discovery.client.config.demo_mode or self.poll_interval_seconds < 0.5
                        else 1.0
                    )
                else:
                    # Discord's /heartbeat endpoint requires ~20s spacing in Live API mode to credit progress
                    wait_time = (
                        self.poll_interval_seconds
                        if self.discovery.client.config.demo_mode or self.poll_interval_seconds < 1.0
                        else max(20.0, self.poll_interval_seconds)
                    )
                await asyncio.sleep(wait_time)
        finally:
            if not is_video:
                await self.discovery.client.unregister_active_game(task.quest_id)


class QuestStateLabel:
    COMPLETED = "COMPLETED"
