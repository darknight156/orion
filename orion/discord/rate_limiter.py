"""Discord API rate-limit tracker with global lock, per-route buckets, and request deduplication."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional

from orion.utils.exceptions import RateLimitExceededError


@dataclass
class BucketState:
    """State of a single Discord rate-limit bucket."""

    bucket_id: str
    limit: int = 1
    remaining: int = 1
    reset_at: float = 0.0

    def seconds_until_available(self, now: Optional[float] = None) -> float:
        current = now if now is not None else time.monotonic()
        if self.remaining > 0:
            return 0.0
        return max(0.0, self.reset_at - current)


class RateLimiter:
    """
    Respects Discord API rate limits across global and route-specific buckets.
    Also provides in-flight GET request deduplication and exponential backoff calculation.
    """

    def __init__(
        self,
        *,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        max_retry_wait_seconds: float = 60.0,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.max_retry_wait_seconds = max_retry_wait_seconds

        self._route_to_bucket: dict[str, str] = {}
        self._buckets: dict[str, BucketState] = {}
        self._bucket_locks: dict[str, asyncio.Lock] = {}
        self._global_reset_at: float = 0.0
        self._inflight_gets: dict[str, asyncio.Future[Any]] = {}

    def get_lock_for_route(self, route_key: str) -> asyncio.Lock:
        bucket_id = self._route_to_bucket.get(route_key, route_key)
        if bucket_id not in self._bucket_locks:
            self._bucket_locks[bucket_id] = asyncio.Lock()
        return self._bucket_locks[bucket_id]

    async def acquire(self, route_key: str) -> float:
        """
        Wait if global rate limit or route-specific bucket is exhausted.
        Returns total seconds waited.
        """
        total_waited = 0.0
        now = time.monotonic()
        if self._global_reset_at > now:
            wait_global = self._global_reset_at - now
            await asyncio.sleep(wait_global)
            total_waited += wait_global

        bucket_id = self._route_to_bucket.get(route_key, route_key)
        bucket = self._buckets.get(bucket_id)
        if bucket is not None:
            wait_bucket = bucket.seconds_until_available(time.monotonic())
            if wait_bucket > 0:
                await asyncio.sleep(wait_bucket)
                total_waited += wait_bucket
        return total_waited

    def update_from_headers(self, route_key: str, headers: Mapping[str, str]) -> Optional[BucketState]:
        """Parse Discord X-RateLimit-* headers and update bucket state."""
        lower_headers = {k.lower(): v for k, v in headers.items()}
        bucket_id = lower_headers.get("x-ratelimit-bucket") or self._route_to_bucket.get(
            route_key, route_key
        )
        self._route_to_bucket[route_key] = bucket_id

        limit_raw = lower_headers.get("x-ratelimit-limit")
        remaining_raw = lower_headers.get("x-ratelimit-remaining")
        reset_after_raw = lower_headers.get("x-ratelimit-reset-after")
        reset_epoch_raw = lower_headers.get("x-ratelimit-reset")

        if limit_raw is None and remaining_raw is None and reset_after_raw is None:
            return self._buckets.get(bucket_id)

        limit = int(limit_raw) if limit_raw and limit_raw.isdigit() else 1
        remaining = int(remaining_raw) if remaining_raw and remaining_raw.isdigit() else 1

        now_mono = time.monotonic()
        reset_at = now_mono
        if reset_after_raw is not None:
            try:
                reset_at = now_mono + max(0.0, float(reset_after_raw))
            except ValueError:
                reset_at = now_mono + 1.0
        elif reset_epoch_raw is not None:
            try:
                delta = max(0.0, float(reset_epoch_raw) - time.time())
                reset_at = now_mono + delta
            except ValueError:
                reset_at = now_mono + 1.0

        state = BucketState(
            bucket_id=bucket_id,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
        )
        self._buckets[bucket_id] = state
        return state

    def record_rate_limit_response(
        self,
        route_key: str,
        headers: Mapping[str, str],
        body: Optional[dict[str, Any]] = None,
    ) -> tuple[float, bool, Optional[str]]:
        """
        Process an HTTP 429 response and return (retry_after_seconds, is_global, bucket_id).
        Raises RateLimitExceededError if retry_after exceeds max_retry_wait_seconds.
        """
        lower_headers = {k.lower(): v for k, v in headers.items()}
        body = body or {}

        retry_after = 1.0
        if "retry_after" in body:
            try:
                retry_after = float(body["retry_after"])
            except (TypeError, ValueError):
                retry_after = 1.0
        elif "retry-after" in lower_headers:
            try:
                retry_after = float(lower_headers["retry-after"])
            except ValueError:
                retry_after = 1.0

        retry_after = max(0.1, retry_after)
        is_global = bool(
            body.get("global")
            or lower_headers.get("x-ratelimit-global", "").lower() == "true"
        )
        bucket_id = lower_headers.get("x-ratelimit-bucket") or self._route_to_bucket.get(
            route_key, route_key
        )

        now_mono = time.monotonic()
        if is_global:
            self._global_reset_at = max(self._global_reset_at, now_mono + retry_after)
        else:
            self._buckets[bucket_id] = BucketState(
                bucket_id=bucket_id,
                limit=1,
                remaining=0,
                reset_at=now_mono + retry_after,
            )

        if retry_after > self.max_retry_wait_seconds:
            raise RateLimitExceededError(
                f"Rate limit retry_after ({retry_after:.2f}s) exceeds maximum allowed wait ({self.max_retry_wait_seconds:.1f}s).",
                retry_after=retry_after,
                is_global=is_global,
                bucket=bucket_id,
                route=route_key,
            )

        return retry_after, is_global, bucket_id

    def compute_backoff(self, attempt: int) -> float:
        """Compute bounded exponential backoff with jitter for transient 5xx/network retries."""
        exp = min(attempt, 6)
        base_delay = self.backoff_base_seconds * (2**exp)
        jitter = random.uniform(0.0, 0.35 * self.backoff_base_seconds)
        return min(self.max_retry_wait_seconds, base_delay + jitter)

    async def deduplicated_call(
        self, dedup_key: str, coro_factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        """
        Deduplicate identical concurrent read requests so simultaneous UI refreshes
        share a single underlying HTTP call.
        """
        existing = self._inflight_gets.get(dedup_key)
        if existing is not None and not existing.done():
            return await asyncio.shield(existing)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._inflight_gets[dedup_key] = future
        try:
            result = await coro_factory()
            if not future.done():
                future.set_result(result)
            return result
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._inflight_gets.pop(dedup_key, None)
