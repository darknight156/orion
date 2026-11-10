"""Animated progress bar renderer with COMPLETING status and remaining ETA."""

from __future__ import annotations

from typing import Any

try:
    from rich import box
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from orion.core.task_manager import MonitoredQuestTask
from orion.utils.formatting import (
    format_compact_clock,
    render_unicode_bar,
)


def build_active_progress_panel_rich(
    task: MonitoredQuestTask | None,
    batch_mode: str = "IDLE",
    queue_len: int = 0,
) -> Any:
    if task is None:
        return Panel(
            Text.from_markup(
                "[#94A3B8]IDLE[/] ── Press [bold #C084FC][1][/] to start [bold #C084FC]All Orbs Quests[/] "
                "or [bold #2DD4BF][2][/] to start [bold #2DD4BF]Others -> Orbs Quests[/]."
            ),
            title="[bold #F8FAFC]Active Quest Runner[/]",
            border_style="#1E293B",
            box=box.ROUNDED,
            padding=(0, 1),
        )

    bar_visual = render_unicode_bar(task.current_progress_seconds, task.target_seconds, width=22)
    eta_label = (
        format_compact_clock(task.eta_seconds)
        if task.eta_seconds is not None
        else "Calculating..."
    )
    elapsed_label = format_compact_clock(task.total_elapsed_seconds)
    verb = "COMPLETING" if task.state.value == "RUNNING" else task.state.value

    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(ratio=3)
    grid.add_column(ratio=2, justify="right")

    grid.add_row(
        Text.from_markup(
            f"[bold #38BDF8]{verb}[/] ── [bold #F8FAFC]{task.title}[/] [bold #C084FC]({task.reward_name})[/]\n"
            f"[bold #10B981]{bar_visual}[/]  [#E2E8F0]{int(task.current_progress_seconds)}s / {int(task.target_seconds)}s[/]"
        ),
        Text.from_markup(
            f"[#94A3B8]Mode:[/] [bold #E2E8F0]{batch_mode}[/] · [#94A3B8]Queued:[/] [bold #38BDF8]{queue_len}[/]\n"
            f"[#94A3B8]Elapsed:[/] [#F8FAFC]{elapsed_label}[/] · [#94A3B8]ETA:[/] [#38BDF8]{eta_label}[/]"
        ),
    )

    return Panel(
        grid,
        title="[bold #38BDF8]Live Quest Execution Progress[/]",
        border_style="#38BDF8" if task.state.value == "RUNNING" else "#F59E0B",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def build_active_progress_panel(task: MonitoredQuestTask | None) -> str:
    if task is None:
        return (
            "┌─ Active Quest Runner ────────────────────────────────────────────────────────┐\n"
            "│ IDLE -- Press [1] for Orbs Quests or [2] for Others -> Orbs Quests.          │\n"
            "└──────────────────────────────────────────────────────────────────────────────┘"
        )

    bar_visual = render_unicode_bar(task.current_progress_seconds, task.target_seconds, width=18)
    eta_label = (
        format_compact_clock(task.eta_seconds)
        if task.eta_seconds is not None
        else "Calculating..."
    )
    elapsed_label = format_compact_clock(task.total_elapsed_seconds)
    line1 = f"COMPLETING -- {task.title} [{task.reward_name}] ({task.state.value})"
    line2 = (
        f"{bar_visual} ({int(task.current_progress_seconds)}s / {int(task.target_seconds)}s)  ·  "
        f"Elapsed: {elapsed_label}  ·  ETA: {eta_label}"
    )
    return (
        "┌─ Active Quest Runner ────────────────────────────────────────────────────────┐\n"
        f"│ {line1[:76]:<76} │\n"
        f"│ {line2[:76]:<76} │\n"
        "└──────────────────────────────────────────────────────────────────────────────┘"
    )
