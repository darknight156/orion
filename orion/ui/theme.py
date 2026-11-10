"""Slug.lat-inspired minimal dark aesthetic palette and theme styles for Project Orion."""

from __future__ import annotations

from orion.core.event_manager import LogCategory

ORION_PALETTE = {
    "bg_primary": "#090A0F",
    "bg_surface": "#11131A",
    "border": "#1E2230",
    "text_primary": "#F1F5F9",
    "text_secondary": "#94A3B8",
    "text_muted": "#64748B",
    "accent": "#38BDF8",
    "success": "#10B981",
    "warning": "#F59E0B",
    "error": "#EF4444",
}

LOG_CATEGORY_STYLES: dict[LogCategory, str] = {
    LogCategory.SYSTEM: "bold #94A3B8",
    LogCategory.NETWORK: "bold #38BDF8",
    LogCategory.DISCOVERY: "bold #818CF8",
    LogCategory.QUEST: "bold #E2E8F0",
    LogCategory.TRACKER: "bold #2DD4BF",
    LogCategory.PROGRESS: "bold #38BDF8",
    LogCategory.SUCCESS: "bold #10B981",
    LogCategory.WARNING: "bold #F59E0B",
    LogCategory.ERROR: "bold #EF4444",
    LogCategory.DEBUG: "dim #64748B",
}

LOG_ANSI_COLORS: dict[LogCategory, str] = {
    LogCategory.SYSTEM: "\033[90m",
    LogCategory.NETWORK: "\033[96m",
    LogCategory.DISCOVERY: "\033[94m",
    LogCategory.QUEST: "\033[97m",
    LogCategory.TRACKER: "\033[36m",
    LogCategory.PROGRESS: "\033[96m",
    LogCategory.SUCCESS: "\033[92m",
    LogCategory.WARNING: "\033[93m",
    LogCategory.ERROR: "\033[91m",
    LogCategory.DEBUG: "\033[90m",
}
ANSI_RESET = "\033[0m"
