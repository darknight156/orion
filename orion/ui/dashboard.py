"""Minimalist Discord Tool CLI & Live Streaming Logger for Project Orion."""

from __future__ import annotations

from typing import Any

try:
    from rich import box
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from orion.core.application import OrionApplication
from orion.ui.logger import OrionTerminalLogger
from orion.ui.progress import (
    build_active_progress_panel,
    build_active_progress_panel_rich,
)


CONTROL_MENU_ITEMS: list[tuple[str, str, str]] = [
    ("1", "Orbs (Start completing all Orbs quests)", "o"),
    ("2", "Others (Start Other quests first, then Orbs quests)", "b"),
    ("q", "Exit", "q"),
]


class OrionDashboard:
    """
    Renders a clean, focused 2-option Discord Quest Tool menu and streams
    `COMPLETING ── [████████░░]` and `OK ──` logs live to the terminal as quests run.
    """

    def __init__(self, app: OrionApplication) -> None:
        self.app = app
        self.logger = OrionTerminalLogger()
        self.console = Console() if HAS_RICH else None
        self.stream_live_events: bool = False
        self.app.events.subscribe(self._on_event)

    def _on_event(self, event: Any) -> None:
        if self.app.authenticator.is_configured:
            self.logger.set_redaction_token(self.app.authenticator.get_raw_token())
        formatted_line = self.logger.record(event)
        if self.stream_live_events:
            self.print_log_line(formatted_line)

    def print_log_line(self, ln: str) -> None:
        """Immediately print a single formatted log line (`COMPLETING ──` or `OK ──`) to stdout."""
        if HAS_RICH and self.console is not None:
            t = self._style_rich_log_line(ln)
            self.console.print(t)
        else:
            clean = (
                ln.replace("COMPLETING --", "COMPLETING ──")
                .replace("OK --", "OK         ──")
                .replace("ERR --", "ERROR      ──")
            )
            print(clean, flush=True)

    @staticmethod
    def _style_rich_log_line(ln: str) -> Any:
        t = Text()
        if "COMPLETING --" in ln:
            parts = ln.split("COMPLETING --", 1)
            t.append(parts[0], style="#64748B")
            t.append("COMPLETING ──", style="bold #38BDF8")
            t.append(parts[1], style="bold #F8FAFC")
        elif "OK --" in ln:
            parts = ln.split("OK --", 1)
            t.append(parts[0], style="#64748B")
            t.append("OK         ──", style="bold #10B981")
            t.append(parts[1], style="#E2E8F0")
        elif "ERR --" in ln:
            parts = ln.split("ERR --", 1)
            t.append(parts[0], style="#64748B")
            t.append("ERROR      ──", style="bold #EF4444")
            t.append(parts[1], style="#FCA5A5")
        else:
            t.append(ln, style="#94A3B8")
        return t

    def print_mode_selector(self) -> None:
        """Print the compact 2-option selector header (no quest list clutter)."""
        metrics = self.app.discovery.compute_summary_metrics(
            self.app.task_manager.get_task_states_map()
        )
        user = self.app.client.connected_user
        account_label = user.display_tag if user else "Not Connected"
        mode_tag = "SANDBOX" if self.app.config.demo_mode else "LIVE API"

        if HAS_RICH and self.console is not None:
            body = Text.from_markup(
                f"[bold #F8FAFC]◆ {self.app.config.app_name}[/] [dim]v{self.app.config.app_version}[/]  ·  "
                f"[#94A3B8]Account:[/] [bold #38BDF8]{account_label}[/]  ·  "
                f"[#94A3B8]Status:[/] [bold #10B981]CONNECTED ({mode_tag})[/]\n"
                f"[#94A3B8]Ready on Account:[/] [bold #C084FC]{metrics['orbs_available']} Orbs Quests ({metrics['total_orbs_pool']} Orbs)[/]  +  "
                f"[bold #2DD4BF]{metrics['others_available']} Other Quest(s)[/]\n\n"
                f"  [bold #C084FC]\\[1] Orbs[/]    [#E2E8F0]── Start & complete all Orbs quests[/]\n"
                f"  [bold #2DD4BF]\\[2] Others[/]  [#E2E8F0]── Start Other quests first, then Orbs quests[/]\n"
                f"  [bold #64748B]\\[q] Exit[/]    [#94A3B8]── Quit Project Orion[/]"
            )
            panel = Panel(
                body,
                title="[bold #38BDF8]Discord Quest Completer[/]",
                border_style="#1E293B",
                box=box.ROUNDED,
                padding=(0, 2),
            )
            self.console.print(panel)
        else:
            print(self.build_header_panel())
            print(self.build_controls_panel())

    def print_dashboard(self) -> None:
        """Render the compact status & execution log view (used by --once or status checks)."""
        if HAS_RICH and self.console is not None:
            self.console.print(self.render_rich_group())
        else:
            print(self.render_full_view())

    def render_rich_group(self) -> Any:
        progress_panel = build_active_progress_panel_rich(
            self.app.task_manager.active_task,
            batch_mode=self.app.task_manager.active_batch_mode,
            queue_len=len(self.app.task_manager.queued_quest_ids),
        )
        raw_lines = self.logger.get_recent_lines(12)
        rich_log_lines = [self._style_rich_log_line(ln) for ln in raw_lines]
        log_panel = Panel(
            Group(*rich_log_lines) if rich_log_lines else Text("Ready.", style="#64748B"),
            title="[bold #F8FAFC]Discord API & Quest Execution Log[/]",
            border_style="#1E293B",
            box=box.ROUNDED,
            padding=(0, 1),
        )
        return Group(progress_panel, log_panel)

    def build_header_panel(self) -> str:
        metrics = self.app.discovery.compute_summary_metrics(
            self.app.task_manager.get_task_states_map()
        )
        user = self.app.client.connected_user
        account_label = user.display_tag if user else "Not Connected"
        conn_status = (
            "CONNECTED (DEMO)"
            if self.app.config.demo_mode and user
            else ("CONNECTED (LIVE API)" if user else "DISCONNECTED")
        )
        return "\n".join(
            [
                "╭─── Project Orion · Discord Quest Completer ──────────────────────────────────╮",
                f"│ Account : {account_label}  ·  Status: {conn_status}",
                f"│ Ready   : {metrics['orbs_available']} Orbs Quests ({metrics['total_orbs_pool']} Orbs)  ·  {metrics['others_available']} Other Quest(s)",
                "╰──────────────────────────────────────────────────────────────────────────────╯",
            ]
        )

    def build_controls_panel(self) -> str:
        return "\n".join(
            [
                "╭─── Select Mode ──────────────────────────────────────────────────────────────╮",
                "│   [1] Orbs    ── Start & complete all Orbs quests                            │",
                "│   [2] Others  ── Start Other quests first, then Orbs quests                  │",
                "│   [q] Exit                                                                   │",
                "╰──────────────────────────────────────────────────────────────────────────────╯",
            ]
        )

    def build_log_panel(self, line_count: int = 12) -> str:
        lines = self.logger.get_recent_lines(line_count)
        if not lines:
            lines = ["Waiting for execution events..."]
        formatted = [
            ln.replace("COMPLETING --", "COMPLETING ──")
            .replace("OK --", "OK         ──")
            .replace("ERR --", "ERROR      ──")
            for ln in lines
        ]
        body = "\n".join(f"│ {ln}" for ln in formatted)
        return (
            "╭─ Live Quest Execution & API Log ─────────────────────────────────────────────╮\n"
            f"{body}\n"
            "╰──────────────────────────────────────────────────────────────────────────────╯"
        )

    def render_full_view(self) -> str:
        return "\n".join(
            [
                self.build_header_panel(),
                build_active_progress_panel(self.app.task_manager.active_task),
                self.build_log_panel(12),
                self.build_controls_panel(),
            ]
        )
