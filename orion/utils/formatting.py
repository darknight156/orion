"""Formatting utilities for durations, timestamps, progress bars, and token redaction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def format_duration(seconds: Optional[float]) -> str:
    """Format seconds into a concise human-readable duration (e.g., '14m 30s' or '01h 05m 12s')."""
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"
    return f"{minutes:02d}m {secs:02d}s"


def format_compact_clock(seconds: Optional[float]) -> str:
    """Format seconds as MM:SS or HH:MM:SS for tabular terminal columns."""
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_relative_expiration(expires_at: Optional[datetime], now: Optional[datetime] = None) -> str:
    """Return human-readable expiration status relative to `now` (UTC)."""
    if expires_at is None:
        return "No expiration"
    ref = now or datetime.now(timezone.utc)
    target = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    delta = (target - ref).total_seconds()
    if delta <= 0:
        return "Expired"
    days = int(delta // 86400)
    hours = int((delta % 86400) // 3600)
    minutes = int((delta % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h remaining"
    if hours > 0:
        return f"{hours}h {minutes}m remaining"
    return f"{minutes}m remaining"


def render_unicode_bar(current: float, required: float, width: int = 10) -> str:
    """Render a crisp block progress bar like [████████░░] 80% (never shows 100% while current < required)."""
    if required <= 0 or current >= required:
        pct = 100
        filled = width
    else:
        ratio = max(0.0, min(1.0, current / required))
        pct = min(99, int(ratio * 100))
        filled = min(width - 1 if width > 1 else width, int(round(ratio * width)))
    bar = "█" * filled + "░" * max(0, width - filled)
    return f"[{bar}] {pct:>3d}%"


def calculate_eta_seconds(
    current_progress: float,
    required_progress: float,
    elapsed_session_seconds: float,
    progress_gained_in_session: float,
) -> Optional[float]:
    """
    Calculate remaining time in seconds ONLY when reliably calculable.
    Returns None if no progress has been observed in the current session or if already complete.
    """
    remaining_units = max(0.0, required_progress - current_progress)
    if remaining_units == 0:
        return 0.0
    if elapsed_session_seconds <= 0 or progress_gained_in_session <= 0:
        return None
    rate_per_second = progress_gained_in_session / elapsed_session_seconds
    if rate_per_second <= 1e-6:
        return None
    estimated = remaining_units / rate_per_second
    # Cap unreasonable ETA above 48 hours as unreliable
    if estimated > 172800:
        return None
    return estimated


def redact_sensitive_text(text: str, token: Optional[str] = None) -> str:
    """Ensure authentication tokens or Authorization headers never leak into logs."""
    sanitized = text
    if token and len(token.strip()) >= 8:
        clean_token = token.strip()
        sanitized = sanitized.replace(clean_token, "[REDACTED_TOKEN]")
    return sanitized
