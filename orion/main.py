"""Entry point for Project Orion — Standalone Python 3.12+ Discord Quest Completer."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
from pathlib import Path
import sys

# Support running `python main.py` from any folder name (e.g. `orion`)
_THIS_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _THIS_DIR.parent
for _p in (str(_PARENT_DIR), str(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if "orion" not in sys.modules and not (_PARENT_DIR / "orion" / "__init__.py").exists():
    _init_py = _THIS_DIR / "__init__.py"
    _spec = importlib.util.spec_from_file_location(
        "orion",
        str(_init_py) if _init_py.exists() else None,
        submodule_search_locations=[str(_THIS_DIR)],
    )
    if _spec is not None:
        _pkg = importlib.util.module_from_spec(_spec)
        _pkg.__path__ = [str(_THIS_DIR)]
        sys.modules["orion"] = _pkg

from orion.config import OrionConfig
from orion.core.application import OrionApplication
from orion.core.event_manager import LogCategory
from orion.discord.authentication import Authenticator
from orion.ui.dashboard import OrionDashboard
from orion.utils.exceptions import OrionError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="orion",
        description="Project Orion — Discord Quest Completer (1. Orbs | 2. Others -> Orbs)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with realistic Discord Orbs & Others Quest fixtures without requiring a live token.",
    )
    parser.add_argument(
        "--mode",
        choices=["orbs", "others"],
        default=None,
        help="Immediately start Mode 1 ('orbs') or Mode 2 ('others' -> then orbs).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Refresh interval in seconds for Quest heartbeat polling (minimum 1.0).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of quests to complete concurrently in parallel (default: 8).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Start the selected mode, print initial progress snapshot, and exit cleanly.",
    )
    return parser.parse_args(argv)


async def _run_batch_and_stream(
    app: OrionApplication,
    dashboard: OrionDashboard,
    mode: str,
    *,
    run_once: bool = False,
) -> None:
    """Start Mode 1 (Orbs) or Mode 2 (Others -> Orbs) and stream live logs until completion."""
    dashboard.stream_live_events = not run_once
    print()
    if mode == "orbs":
        await app.task_manager.start_orbs_batch()
    else:
        await app.task_manager.start_others_then_orbs_batch()

    if run_once:
        await asyncio.sleep(0.25)
        dashboard.print_dashboard()
        return

    try:
        await app.task_manager.wait_until_queue_complete()
    except (KeyboardInterrupt, asyncio.CancelledError):
        await app.task_manager.stop_active()
    finally:
        dashboard.stream_live_events = False


async def _prompt_for_token(app: OrionApplication) -> None:
    """
    Prompt for the user's Discord token using standard `input()` so Windows PowerShell
    Ctrl+V / right-click paste works reliably, then mask the echoed line in the terminal.
    """
    while not app.authenticator.is_configured and not app.config.demo_mode:
        print("\n◆ Project Orion Authentication")
        try:
            entered = await asyncio.to_thread(
                input,
                "Paste your Discord Token and press Enter (or type 'demo' for Sandbox): ",
            )
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

        cleaned = entered.strip().strip('"').strip("'")
        if not cleaned:
            print("  [!] Token was empty. Right-click or press Ctrl+V to paste your token, then press Enter.")
            continue

        if cleaned.lower() == "demo":
            app.config.demo_mode = True
            await app.events.emit(
                LogCategory.SYSTEM,
                "OK -- Launching in Demo Sandbox Mode.",
            )
            await app.authenticate()
            break

        # Mask the pasted token line in the terminal immediately after Enter
        print("\033[1A\033[2K◆ Discord Token received: [REDACTED] · Verifying with Discord API...")
        try:
            app.config.demo_mode = False
            await app.authenticate(cleaned)
            break
        except OrionError as exc:
            print(f"  [!] Authentication failed: {exc.message}. Please paste a valid Discord token.")
            app.authenticator.clear_token()


async def interactive_loop(
    app: OrionApplication,
    dashboard: OrionDashboard,
    *,
    run_once: bool = False,
    initial_mode: str | None = None,
) -> int:
    await app.initialize()

    if not app.authenticator.is_configured and not app.config.demo_mode:
        await _prompt_for_token(app)
    else:
        await app.authenticate()

    await app.discover_quests()

    if initial_mode in {"orbs", "others"}:
        await _run_batch_and_stream(app, dashboard, initial_mode, run_once=run_once)
        await app.shutdown()
        return 0

    if run_once:
        dashboard.print_dashboard()
        await app.shutdown()
        return 0

    while True:
        print()
        dashboard.print_mode_selector()
        try:
            cmd = await asyncio.to_thread(
                input,
                "\nSelect Option ([1] Orbs Quests, [2] Others -> Orbs, [q] Exit) [1]: ",
            )
        except (EOFError, KeyboardInterrupt):
            break

        raw_input = cmd.strip().strip('"').strip("'")

        # If the user pasted their Discord token at the option prompt, authenticate with it automatically
        if Authenticator.is_plausible_discord_token(raw_input) or len(raw_input) >= 24:
            print("\033[1A\033[2K◆ Discord Token detected · Switching to LIVE API and authenticating...")
            try:
                app.config.demo_mode = False
                await app.authenticate(raw_input)
                await app.discover_quests()
                continue
            except OrionError as exc:
                print(f"  [!] Token error: {exc.message}")
                continue

        choice = (raw_input or "1").lower()
        if choice in {"q", "exit", "quit", "3", "11"}:
            break
        elif choice in {"1", "o", "orbs"}:
            await app.discover_quests()
            await _run_batch_and_stream(app, dashboard, "orbs", run_once=False)
        elif choice in {"2", "b", "others"}:
            await app.discover_quests()
            await _run_batch_and_stream(app, dashboard, "others", run_once=False)
        else:
            print("Invalid option. Type 1 for Orbs, 2 for Others -> Orbs, or q to Exit.")

    await app.shutdown()
    return 0


def main() -> int:
    args = parse_args()
    config = OrionConfig.from_env()
    if args.demo:
        config.demo_mode = True
    if args.interval is not None:
        config.refresh_interval_seconds = max(1.0, args.interval)
    if args.workers is not None:
        config.max_concurrent_quests = max(1, min(16, args.workers))

    app = OrionApplication(config)
    dashboard = OrionDashboard(app)
    try:
        return asyncio.run(
            interactive_loop(
                app,
                dashboard,
                run_once=args.once,
                initial_mode=args.mode,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting Project Orion.")
        return 0
    except OrionError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
