"""Quest table and detailed inspector views using Rich (with aligned ANSI/plain fallback)."""

from __future__ import annotations

from typing import Any, Optional

try:
    from rich import box
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from orion.discord.quest_models import (
    QuestLifecycleStatus,
    QuestModel,
)
from orion.utils.formatting import (
    format_relative_expiration,
    render_unicode_bar,
)


def build_quest_table_rich(
    quests: list[QuestModel],
    *,
    selected_quest_id: Optional[str] = None,
    active_states: Optional[dict[str, str]] = None,
) -> Any:
    """Build a Rich Table with Orbs badges, task categories, and progress bars."""
    states = active_states or {}
    table = Table(
        expand=True,
        box=box.ROUNDED,
        border_style="#1E293B",
        header_style="bold #94A3B8",
        padding=(0, 1),
    )
    table.add_column("#", style="#64748B", width=4, justify="right")
    table.add_column("Reward Type", width=13)
    table.add_column("Quest Title", style="bold #F8FAFC", ratio=3)
    table.add_column("Application", style="#94A3B8", ratio=2)
    table.add_column("Task", style="#38BDF8", width=18)
    table.add_column("Progress", width=20)
    table.add_column("Status", width=11)
    table.add_column("Expires", style="#64748B", width=14)

    for idx, quest in enumerate(quests, start=1):
        is_selected = quest.id == selected_quest_id
        marker = f"▸{idx}" if is_selected else str(idx)
        lifecycle = quest.compute_lifecycle_status(states.get(quest.id))
        bar = render_unicode_bar(
            quest.primary_task.current_seconds,
            quest.primary_task.target_seconds,
            width=10,
        )

        if quest.is_orbs_quest:
            reward_badge = Text(f"✦ {quest.orb_amount} ORBS", style="bold #C084FC")
        else:
            reward_badge = Text(f"◆ {quest.reward.name[:11]}", style="bold #2DD4BF")

        status_style = "#94A3B8"
        if lifecycle in {QuestLifecycleStatus.COMPLETED, QuestLifecycleStatus.CLAIMED}:
            status_style = "bold #10B981"
        elif lifecycle == QuestLifecycleStatus.ACTIVE:
            status_style = "bold #38BDF8"
        elif lifecycle == QuestLifecycleStatus.PAUSED:
            status_style = "bold #F59E0B"
        elif lifecycle == QuestLifecycleStatus.EXPIRED:
            status_style = "dim #64748B"

        bar_style = (
            "bold #10B981"
            if lifecycle in {QuestLifecycleStatus.COMPLETED, QuestLifecycleStatus.CLAIMED}
            else ("bold #38BDF8" if lifecycle == QuestLifecycleStatus.ACTIVE else "#94A3B8")
        )

        table.add_row(
            Text(marker, style="bold #38BDF8" if is_selected else "#64748B"),
            reward_badge,
            quest.title,
            quest.application_name,
            quest.primary_task.category.value,
            Text(bar, style=bar_style),
            Text(lifecycle.value, style=status_style),
            format_relative_expiration(quest.expires_at),
        )

    return table


def build_quest_table(
    quests: list[QuestModel],
    *,
    selected_quest_id: Optional[str] = None,
    active_states: Optional[dict[str, str]] = None,
) -> str:
    """Build an aligned unicode box-drawing table (handles 1-99+ rows without shifting columns)."""
    states = active_states or {}
    lines = [
        "┌─────┬──────────────┬────────────────────────────┬──────────────────┬───────────────────┬──────────────────┬───────────┐",
        "│  #  │ Reward       │ Quest Title                │ Application      │ Task Category     │ Progress         │ Status    │",
        "├─────┼──────────────┼────────────────────────────┼──────────────────┼───────────────────┼──────────────────┼───────────┤",
    ]

    for idx, quest in enumerate(quests, start=1):
        is_selected = quest.id == selected_quest_id
        marker = f">{idx}" if is_selected else f" {idx}"
        lifecycle = quest.compute_lifecycle_status(states.get(quest.id))
        bar = render_unicode_bar(
            quest.primary_task.current_seconds,
            quest.primary_task.target_seconds,
            width=8,
        )
        reward_tag = f"{quest.orb_amount} ORBS" if quest.is_orbs_quest else "OTHER/DECO"
        lines.append(
            f"│ {marker:>3} │ {reward_tag:<12} │ {quest.title[:26]:<26} │ {quest.application_name[:16]:<16} │ "
            f"{quest.primary_task.category.value[:17]:<17} │ {bar:<16} │ {lifecycle.value[:9]:<9} │"
        )

    lines.append(
        "└─────┴──────────────┴────────────────────────────┴──────────────────┴───────────────────┴──────────────────┴───────────┘"
    )
    return "\n".join(lines)


def build_quest_detail_panel_rich(quest: Optional[QuestModel]) -> Any:
    if quest is None:
        return Panel(
            Text("No quest selected.", style="#64748B"),
            title="[bold #F8FAFC]Quest Inspector[/]",
            border_style="#1E293B",
            box=box.ROUNDED,
        )
    grid = Table.grid(expand=True, padding=(0, 3))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    reward_str = (
        f"[bold #C084FC]{quest.orb_amount} Discord Orbs[/] ({quest.reward.name})"
        if quest.is_orbs_quest
        else f"[bold #2DD4BF]{quest.reward.name}[/]"
    )
    grid.add_row(
        Text.from_markup(
            f"[#64748B]Quest:[/] [bold #F8FAFC]{quest.title}[/] [dim]({quest.id})[/]\n"
            f"[#64748B]App:[/]   [#E2E8F0]{quest.application_name}[/] · [#94A3B8]{quest.publisher}[/]"
        ),
        Text.from_markup(
            f"[#64748B]Reward:[/] {reward_str}\n"
            f"[#64748B]Target:[/] [#38BDF8]{quest.primary_task.category.value}[/] · "
            f"[bold #F8FAFC]{int(quest.primary_task.current_seconds)}s / {int(quest.primary_task.target_seconds)}s[/] "
            f"({quest.progress_percentage:.0f}%) · {format_relative_expiration(quest.expires_at)}"
        ),
    )
    return Panel(
        grid,
        title="[bold #94A3B8]Selected Quest Inspector[/]",
        border_style="#1E293B",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def build_quest_detail_panel(quest: Optional[QuestModel]) -> str:
    if quest is None:
        return "┌─ Quest Inspector ── No quest selected ───────────────────────────────────────┐"
    r_type = f"ORBS ({quest.orb_amount})" if quest.is_orbs_quest else f"OTHER ({quest.reward.name})"
    return "\n".join(
        [
            f"┌─ Quest Inspector · {quest.title[:56]:<56} ─┐",
            f"│ ID: {quest.id:<22} App: {quest.application_name} ({quest.publisher})",
            f"│ Category: {quest.primary_task.category.value:<16} Reward: {r_type}",
            f"│ Progress: {int(quest.primary_task.current_seconds)}s / {int(quest.primary_task.target_seconds)}s ({quest.progress_percentage:.1f}%) · Expires: {format_relative_expiration(quest.expires_at)}",
            "└──────────────────────────────────────────────────────────────────────────────┘",
        ]
    )
