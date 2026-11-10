"""Unit tests for Discord API rate-limit tracking, Retry-After handling, and request deduplication."""

from __future__ import annotations

import asyncio
import unittest

from orion.discord.rate_limiter import RateLimiter
from orion.utils.exceptions import RateLimitExceededError


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limiter_bucket_headers_and_wait(self) -> None:
        limiter = RateLimiter(
            max_retries=2, backoff_base_seconds=0.1, max_retry_wait_seconds=5.0
        )
        headers = {
            "X-RateLimit-Limit": "5",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset-After": "0.08",
            "X-RateLimit-Bucket": "bucket_quests_me",
        }
        state = limiter.update_from_headers("GET:/quests/@me", headers)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.bucket_id, "bucket_quests_me")
        self.assertEqual(state.remaining, 0)

        waited = await limiter.acquire("GET:/quests/@me")
        self.assertGreaterEqual(waited, 0.04)

    async def test_rate_limiter_429_and_bounded_wait(self) -> None:
        limiter = RateLimiter(
            max_retries=2, backoff_base_seconds=0.1, max_retry_wait_seconds=2.0
        )
        retry_after, is_global, bucket = limiter.record_rate_limit_response(
            "GET:/quests/@me",
            {"Retry-After": "0.25", "X-RateLimit-Bucket": "b1"},
            {"retry_after": 0.25, "global": False},
        )
        self.assertEqual(retry_after, 0.25)
        self.assertFalse(is_global)
        self.assertEqual(bucket, "b1")

        with self.assertRaises(RateLimitExceededError):
            limiter.record_rate_limit_response(
                "GET:/quests/@me",
                {"Retry-After": "15.0"},
                {"retry_after": 15.0, "global": True},
            )

    async def test_inflight_request_deduplication(self) -> None:
        limiter = RateLimiter()
        call_counter = 0

        async def slow_fetch() -> dict[str, int]:
            nonlocal call_counter
            call_counter += 1
            await asyncio.sleep(0.05)
            return {"count": call_counter}

        r1, r2, r3 = await asyncio.gather(
            limiter.deduplicated_call("GET:/quests/@me", slow_fetch),
            limiter.deduplicated_call("GET:/quests/@me", slow_fetch),
            limiter.deduplicated_call("GET:/quests/@me", slow_fetch),
        )
        self.assertEqual(call_counter, 1)
        self.assertEqual(r1, {"count": 1})
        self.assertEqual(r2, {"count": 1})
        self.assertEqual(r3, {"count": 1})


if __name__ == "__main__":
    unittest.main()
