"""Custom structured terminal logger with token redaction and category styling."""

from __future__ import annotations

from typing import Optional

from orion.core.event_manager import OrionEvent
from orion.ui.theme import ANSI_RESET, LOG_ANSI_COLORS
from orion.utils.formatting import redact_sensitive_text


class OrionTerminalLogger:
    """
    Formats OrionEvents into aligned, color-styled terminal log lines.
    Example output:
    [17:42:01] SYSTEM    Application initialized successfully.
    [17:42:05] TRACKER   Monitoring quest: Example Quest
    [17:42:06] PROGRESS  [████████░░] 80%
    [17:42:07] SUCCESS   Quest completion confirmed by Discord.
    """

    def __init__(self, max_entries: int = 200) -> None:
        self.max_entries = max_entries
        self._entries: list[OrionEvent] = []
        self._secret_token: Optional[str] = None

    def set_redaction_token(self, token: Optional[str]) -> None:
        self._secret_token = token

    def record(self, event: OrionEvent) -> str:
        self._entries.append(event)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]
        return self.format_event_plain(event)

    def format_event_plain(self, event: OrionEvent) -> str:
        """Render a plain-text log line for snapshot testing and log exports."""
        ts_str = event.timestamp.strftime("%H:%M:%S")
        cat_str = f"{event.category.value:<9}"
        safe_msg = redact_sensitive_text(event.message, self._secret_token)
        if event.task_id:
            return f"[{ts_str}] {cat_str} [{event.task_id}] {safe_msg}"
        return f"[{ts_str}] {cat_str} {safe_msg}"

    def format_event_ansi(self, event: OrionEvent) -> str:
        """Render an ANSI-styled log line for terminal output."""
        ts_str = event.timestamp.strftime("%H:%M:%S")
        cat_str = f"{event.category.value:<9}"
        color = LOG_ANSI_COLORS.get(event.category, "")
        safe_msg = redact_sensitive_text(event.message, self._secret_token)
        task_part = f"[{event.task_id}] " if event.task_id else ""
        return f"\033[90m[{ts_str}]{ANSI_RESET} {color}{cat_str}{ANSI_RESET} {task_part}{safe_msg}"

    def get_recent_lines(self, count: int = 12) -> list[str]:
        return [self.format_event_plain(ev) for ev in self._entries[-count:]]
