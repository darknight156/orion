"""Tests for Quest models, category classification, unsupported detection, authentication, and log redaction."""

from __future__ import annotations

import unittest

from orion.core.event_manager import LogCategory, OrionEvent
from orion.core.task_manager import QuestTaskManager
from orion.discord.authentication import Authenticator
from orion.discord.quest_models import (
    QuestCategory,
    QuestModel,
    QuestSupportStatus,
)
from orion.ui.logger import OrionTerminalLogger
from orion.utils.exceptions import AuthenticationError
from orion.utils.formatting import (
    calculate_eta_seconds,
    render_unicode_bar,
)


class TestModelsAndAuth(unittest.TestCase):
    def test_classify_all_quest_categories(self) -> None:
        cat, sup, _ = QuestModel.classify_task_event("WATCH_VIDEO")
        self.assertEqual(cat, QuestCategory.WATCH_VIDEO)
        self.assertEqual(sup, QuestSupportStatus.SUPPORTED_MONITOR)

        cat_m, sup_m, _ = QuestModel.classify_task_event("WATCH_VIDEO_ON_MOBILE")
        self.assertEqual(cat_m, QuestCategory.WATCH_VIDEO_ON_MOBILE)
        self.assertEqual(sup_m, QuestSupportStatus.MOBILE_CLIENT_REQUIRED)

        cat_d, sup_d, _ = QuestModel.classify_task_event("PLAY_ON_DESKTOP")
        self.assertEqual(cat_d, QuestCategory.PLAY_ON_DESKTOP)
        self.assertEqual(sup_d, QuestSupportStatus.SUPPORTED_MONITOR)

        cat_s, sup_s, _ = QuestModel.classify_task_event("STREAM_ON_DESKTOP")
        self.assertEqual(cat_s, QuestCategory.STREAM_ON_DESKTOP)
        self.assertEqual(sup_s, QuestSupportStatus.SUPPORTED_MONITOR)

        cat_a, sup_a, _ = QuestModel.classify_task_event("PLAY_ACTIVITY")
        self.assertEqual(cat_a, QuestCategory.PLAY_ACTIVITY)
        self.assertEqual(sup_a, QuestSupportStatus.SUPPORTED_MONITOR)

        cat_ach, sup_ach, _ = QuestModel.classify_task_event("ACHIEVEMENT_IN_GAME")
        self.assertEqual(cat_ach, QuestCategory.ACHIEVEMENT_BASED)
        self.assertEqual(sup_ach, QuestSupportStatus.SERVER_MANAGED)

        cat_con, sup_con, _ = QuestModel.classify_task_event("PLAY_ON_PLAYSTATION")
        self.assertEqual(cat_con, QuestCategory.CONSOLE_BASED)
        self.assertEqual(sup_con, QuestSupportStatus.SERVER_MANAGED)

        cat_unk, sup_unk, _ = QuestModel.classify_task_event("PROPRIETARY_SERVER_EVENT")
        self.assertEqual(cat_unk, QuestCategory.UNKNOWN)
        self.assertEqual(sup_unk, QuestSupportStatus.UNSUPPORTED_TASK)

    def test_ad_decision_creative_payload_and_heartbeat_extraction(self) -> None:
        ad_decision_payload = {
            "metadata_sealed": "sealed-meta-token",
            "traffic_metadata_sealed": "sealed-traffic-token",
            "creative": {
                "creative_content": {
                    "id": "148999000111222333",
                    "config": {
                        "id": "148999000111222333",
                        "starts_at": "2026-01-01T00:00:00Z",
                        "expires_at": "2027-12-31T00:00:00Z",
                        "messages": {
                            "quest_name": "FINAL FANTASY XIV: EVERCOLD QUEST",
                            "game_title": "FINAL FANTASY XIV",
                            "game_publisher": "SQUARE ENIX",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "WATCH_VIDEO_ON_MOBILE": {"type": "WATCH_VIDEO_ON_MOBILE", "target": 28},
                                "WATCH_VIDEO": {"type": "WATCH_VIDEO", "target": 28},
                            }
                        },
                        "rewards_config": {
                            "rewards": [
                                {"type": 1, "messages": {"name": "Bonus Code"}},
                                {"type": 4, "orb_quantity": 200, "messages": {"name": "200 Orbs"}},
                            ]
                        },
                    },
                    "user_status": None,
                }
            },
        }
        quest = QuestModel.from_discord_payload(ad_decision_payload)
        self.assertEqual(quest.id, "148999000111222333")
        self.assertEqual(quest.title, "FINAL FANTASY XIV: EVERCOLD QUEST")
        self.assertTrue(quest.is_orbs_quest)
        self.assertEqual(quest.orb_amount, 200)
        self.assertTrue(quest.is_video_quest)
        self.assertEqual(quest.primary_task.category, QuestCategory.WATCH_VIDEO)
        self.assertEqual(quest.primary_task.target_seconds, 28.0)
        self.assertEqual(quest.metadata_sealed, "sealed-meta-token")
        self.assertEqual(quest.traffic_metadata_sealed, "sealed-traffic-token")

        # Verify Beat #1 with value: 0 returns 0.0 and False
        beat1_resp = {
            "user_id": "123",
            "quest_id": "1560184130673840250",
            "enrolled_at": "2026-11-09T13:14:00Z",
            "completed_at": None,
            "progress": {
                "PLAY_ON_DESKTOP": {
                    "event_name": "PLAY_ON_DESKTOP",
                    "value": 0,
                    "completed_at": None,
                    "heartbeat": {"last_beat_at": "2026-11-09T13:14:05Z"},
                }
            },
        }
        val, done = QuestTaskManager._extract_progress_from_response(
            beat1_resp, "PLAY_ON_DESKTOP", 0.0
        )
        self.assertEqual(val, 0.0)
        self.assertFalse(done)

    def test_authenticator_validation_and_masking(self) -> None:
        with self.assertRaises(AuthenticationError):
            Authenticator("short")

        sample_token = "MTI0NTA4OTE4MjczNjQ1MTIz.G8xKpA.9zQvR2mX8pL4kW1nJ7vB5cT3yH6fD0sA2eU"
        auth = Authenticator(sample_token)
        self.assertTrue(auth.is_configured)
        self.assertTrue(auth.is_plausible_discord_token(sample_token))
        headers = auth.build_auth_headers("OrionTest/1.0")
        self.assertEqual(headers["Authorization"], sample_token)
        masked = auth.masked_fingerprint()
        self.assertNotIn(sample_token, masked)
        self.assertTrue(masked.startswith("MTI0"))

    def test_logger_never_leaks_credentials(self) -> None:
        secret = "MTI0NTA4OTE4MjczNjQ1MTIz.G8xKpA.9zQvR2mX8pL4kW1nJ7vB5cT3yH6fD0sA2eU"
        logger = OrionTerminalLogger()
        logger.set_redaction_token(secret)
        ev = OrionEvent(
            category=LogCategory.NETWORK,
            message=f"Connected with token {secret} to gateway",
        )
        rendered = logger.format_event_plain(ev)
        self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED_TOKEN]", rendered)

    def test_progress_bar_and_eta_calculation(self) -> None:
        bar = render_unicode_bar(80, 100, width=10)
        self.assertEqual(bar, "[████████░░]  80%")

        self.assertIsNone(calculate_eta_seconds(100, 900, 30.0, 0.0))

        eta = calculate_eta_seconds(100, 900, 100.0, 100.0)
        self.assertIsNotNone(eta)
        assert eta is not None
        self.assertAlmostEqual(eta, 800.0, places=2)


if __name__ == "__main__":
    unittest.main()
