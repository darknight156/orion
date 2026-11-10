"""Tests for SQLite persistence migrations, Orbs vs Others batch queues, and QuestTaskManager lifecycle."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from orion.config import OrionConfig
from orion.core.application import OrionApplication
from orion.core.task_manager import TaskState


class TestDatabaseAndTasks(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_persistence_and_orbs_batch_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = Path(tmp_dir) / "test_orion.db"
            config = OrionConfig(
                database_path=db_file,
                demo_mode=True,
                refresh_interval_seconds=0.05,
            )
            app = OrionApplication(config)
            app.task_manager.poll_interval_seconds = 0.05

            await app.initialize()
            user = await app.authenticate()
            self.assertEqual(user.username, "orion.operator")

            quests = await app.discover_quests()
            self.assertEqual(len(quests), 8)

            # Verify Orbs vs Others filtering
            orbs_quests = app.discovery.get_orbs_quests()
            self.assertEqual(len(orbs_quests), 7)
            others_then_orbs = app.discovery.get_others_then_orbs_quests()
            self.assertEqual(len(others_then_orbs), 8)
            self.assertFalse(others_then_orbs[0].is_orbs_quest)

            # Start Mode 1: Orbs Batch
            queued = await app.task_manager.start_orbs_batch()
            self.assertEqual(len(queued), 7)

            first_task = queued[0]
            await asyncio.sleep(0.08)
            self.assertIn(first_task.state, {TaskState.RUNNING, TaskState.COMPLETED})

            # Wait until first video Orbs quest completes (29s target)
            for _ in range(15):
                if first_task.state == TaskState.COMPLETED:
                    break
                await asyncio.sleep(0.05)

            self.assertEqual(first_task.state, TaskState.COMPLETED)
            self.assertEqual(first_task.percentage, 100.0)

            await app.shutdown()


if __name__ == "__main__":
    unittest.main()
