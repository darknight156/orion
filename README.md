# ◆ Project Orion — Discord Quest Completer (`v2.7.1`)

> **Standalone Python 3.12+ CLI** · **Asyncio + aiohttp + Gateway v9** · **Rich / ANSI Live Terminal UI** · **SQLite3 WAL Persistence** · **Windows / Linux / macOS / Termux**

**Project Orion** is a standalone, asynchronous Python application for discovering, enrolling, monitoring, and completing **Discord Quests** (both **Orbs** quests and **Avatar Decoration / Other** quests) directly from your terminal—with zero browser automation, zero `discord.py` bot overhead, and zero external dependencies required at runtime.

*Inspired by [darknight156/Dpy-Quest-Completer](https://github.com/darknight156/Dpy-Quest-Completer) — rebuilt from the ground up with a pure asynchronous HTTP + Gateway architecture.*

---

## ⚡ Key Features

- **2-Option Instant Batch Modes**
  - **`[1] Orbs`** — Automatically discovers, enrolls, and completes all available **Discord Orbs** quests (`200 Orbs` Video quests + `700 Orbs` Play/Activity quests) in parallel.
  - **`[2] Others -> Orbs`** — Completes non-Orbs quests first (Avatar Decorations, In-Game Items, Nitro Rewards), then automatically transitions to all remaining Orbs quests.
- **Full Quest Discovery (`/quests/@me` + Quest Home Decisions)**
  - Queries both `GET /api/v9/quests/@me` and Discord's Quest Home decision placements (`GET /api/v9/quests/get-decisions` & `GET /api/v9/quests/decision`) with Desktop `X-Super-Properties` so un-enrolled sponsored **Video Quests** (`Watch 28s` / `Watch 30s+`) are discovered automatically.
- **8x Parallel Turbo Execution**
  - Runs up to **8 quests simultaneously** using an `asyncio.Semaphore` worker pool.
  - **Video Quests (`WATCH_VIDEO` / `WATCH_VIDEO_ON_MOBILE`)** complete in ~15–25 seconds by stepping through `POST /api/v9/quests/{id}/video-progress` within Discord's `enrolled_at + 10s` server validation window.
  - **Play & Activity Quests (`PLAY_ON_DESKTOP` / `PLAY_ACTIVITY`)** run concurrently in parallel so multiple 15-minute quests finish together in a single 15-minute window.
- **Built-In Desktop Gateway Presence (`wss://gateway.discord.gg/?v=9`)**
  - Maintains a lightweight background Gateway WebSocket session advertising Opcode 3 `PRESENCE_UPDATE` with each active quest's `application_id` while sending 20-second `POST /api/v9/quests/{id}/heartbeat` beats.
- **100% Authoritative Server Progress**
  - Never invents or fakes local progress bars in Live API mode. Progress percentages and `COMPLETED` status are strictly driven by Discord's `user_status.progress` and `completed_at` responses.
  - Sends `"terminal": false` on all ongoing heartbeats until Discord confirms `value >= target`, preventing the `99% (899s/900s)` premature session-termination bug.
- **Zero-Dependency Fallback & Termux Support**
  - Uses `aiohttp` and `rich` when installed, and automatically falls back to Python's standard library (`urllib.request` + ANSI rendering) so it runs out-of-the-box on **Windows PowerShell**, **Linux**, **macOS**, and **Android (Termux)**.

---

## 🖥️ Live Terminal Output Preview

```text
◆ Project Orion v2.7.1  ·  Account: DarkNighT  ·  Status: CONNECTED (LIVE API)
Ready on Account: 5 Orbs Quests (2500 Orbs)  +  1 Other Quest(s)

  [1] Orbs    ── Start & complete all Orbs quests
  [2] Others  ── Start Other quests first, then Orbs quests
  [q] Exit    ── Quit Project Orion

Select Option ([1] Orbs Quests, [2] Others -> Orbs, [q] Exit) [1]: 1

[13:14:02] QUEST     OK          ── Mode [1] ORBS TURBO started: Running 5 Orbs quests (2 Video, 3 Play · 2500 Orbs) with 8x parallel workers.
[13:14:02] NETWORK   OK          ── POST /api/v9/quests/130491827364510002/enroll -> HTTP 200 (rate-limit: ok)
[13:14:03] NETWORK   OK          ── POST /api/v9/quests/130491827364510002/video-progress -> HTTP 200 (rate-limit: ok)
[13:14:03] PROGRESS  [130491827364510002] COMPLETING ── [████████████████] 100% (28s/28s) · FINAL FANTASY XIV: EVERCOLD QUEST [200 Orbs]
[13:14:03] SUCCESS   [130491827364510002] OK         ── COMPLETED 'FINAL FANTASY XIV: EVERCOLD QUEST' -> Reward ready: 200 Orbs!
[13:14:04] NETWORK   OK          ── Connected Desktop Gateway session (wss://gateway.discord.gg v9)
[13:14:05] NETWORK   OK          ── POST /api/v9/quests/1552876032875560980/heartbeat -> HTTP 200 (rate-limit: ok)
[13:14:05] PROGRESS  [1552876032875560980] COMPLETING ── [███████████████░]  99% (899s/900s) · World of Warcraft: Forever Play [700 Orbs]
[13:14:25] NETWORK   OK          ── POST /api/v9/quests/1552876032875560980/heartbeat -> HTTP 200 (rate-limit: ok)
[13:14:25] PROGRESS  [1552876032875560980] COMPLETING ── [████████████████] 100% (900s/900s) · World of Warcraft: Forever Play [700 Orbs]
[13:14:25] SUCCESS   [1552876032875560980] OK         ── COMPLETED 'World of Warcraft: Forever Play' -> Reward ready: 700 Orbs!
[13:14:25] SUCCESS   OK          ── All queued quests have finished processing!
```

---

## 🚀 Quick Start

### 1. Windows (PowerShell / Command Prompt)

```powershell
# 1. Unpack or update Project Orion
python3 install_orion_project.py
cd orion

# 2. (Optional) Install aiohttp & rich for WebSocket Gateway presence and Rich TUI panels
pip install -r requirements.txt

# 3. Launch in Mode 1 (Orbs) with 8 parallel workers
python3 main.py --mode orbs --workers 8
```

> **Note for Windows PowerShell**: When prompted for your Discord token, simply **right-click** or press **`Ctrl + V`** and hit **Enter**. Project Orion immediately masks the pasted line in the terminal as `[REDACTED]` and never writes your token to disk or logs.

### 2. Linux & macOS

```bash
cd orion
python3 -m pip install -r requirements.txt
python3 main.py --mode orbs --workers 8
```

### 3. Android (Termux)

```bash
pkg update && pkg upgrade -y
pkg install python -y
pip install aiohttp rich

cd orion
python3 main.py --mode orbs --workers 8
```

---

## 🎛️ CLI Arguments & Environment Variables

### Command-Line Flags

| Flag | Description | Default |
| :--- | :--- | :--- |
| `--mode orbs` | Immediately start **Option [1] Orbs** (all actionable Orbs quests in parallel) | Interactive prompt |
| `--mode others` | Immediately start **Option [2] Others -> Orbs** (Decoration/Other quests first, then Orbs) | Interactive prompt |
| `--workers <N>` | Number of quests to process concurrently in parallel (`1`–`16`) | `8` |
| `--interval <SEC>` | Poll / heartbeat interval in seconds (Live API enforces `>=20s` for `/heartbeat`) | `20.0` |
| `--demo` | Launch in offline Sandbox Mode with 8 realistic Discord Quest fixtures (7 Orbs + 1 Other) | `False` |
| `--once` | Print initial dashboard & progress snapshot once and exit cleanly | `False` |

### Environment Variables (Optional)

You can also pass your token via environment variable so you don't need to paste it each run:

```bash
# Linux / macOS / Termux
export ORION_DISCORD_TOKEN="your_discord_token_here"
python3 main.py --mode orbs --workers 8
```

```powershell
# Windows PowerShell
$env:ORION_DISCORD_TOKEN="your_discord_token_here"
python3 main.py --mode orbs --workers 8
```

---

## 🧩 Supported Discord Quest Categories

| Task Category | Execution Method | Status | Notes |
| :--- | :--- | :--- | :--- |
| `WATCH_VIDEO` | `POST /api/v9/quests/{id}/video-progress` | ✅ Supported | Sends initial `timestamp: 0`, then steps by `~7–8s` within Discord's `enrolled_at + 10s` window |
| `WATCH_VIDEO_ON_MOBILE` | `POST /api/v9/quests/{id}/video-progress` | ✅ Supported | Prioritized alongside desktop video tasks |
| `PLAY_ON_DESKTOP` | Gateway Presence + `POST /api/v9/quests/{id}/heartbeat` | ✅ Supported | Registers `application_id` via Gateway Opcode 3 + sends 20s heartbeats with `terminal: false` |
| `PLAY_ACTIVITY` | `POST /api/v9/quests/{id}/heartbeat` | ✅ Supported | Sends `stream_key: "call:{id}:1"` heartbeats every 20s |
| `STREAM_ON_DESKTOP` | `POST /api/v9/quests/{id}/heartbeat` | ✅ Supported | Sends stream heartbeats and tracks `stream_progress_seconds` |
| `ACHIEVEMENT_BASED` | Publisher Server Webhook | ⚠️ Detected | Identified as server-managed achievement rather than faking local completion |
| `CONSOLE_BASED` | Console Network Bridge (`PS5` / `Xbox`) | ⚠️ Detected | Identified as console-linked task |

---

## 🏗️ Project Structure

```text
orion/
├── main.py                        # CLI entry point, argument parser, and interactive/batch runner
├── config.py                      # Runtime configuration & environment variable loader
├── requirements.txt               # Dependencies (aiohttp, rich, textual, pydantic, pytest)
├── API_RESEARCH.md                # Detailed Discord Quest API v9/v10 endpoint & header documentation
├── README.md                      # Project documentation
├── core/
│   ├── application.py             # Top-level OrionApplication orchestrator
│   ├── event_manager.py           # Async pub/sub telemetry bus (SYSTEM, NETWORK, QUEST, PROGRESS, SUCCESS)
│   └── task_manager.py            # 8x parallel worker pool, video window pacer & 20s heartbeat loop
├── discord/
│   ├── authentication.py          # Token sanitization, redaction & Desktop X-Super-Properties builder
│   ├── client.py                  # Async HTTP client + Desktop Gateway WebSocket presence (Opcode 2 & 3)
│   ├── quest_discovery.py         # Multi-source discovery (/quests/@me + /quests/get-decisions)
│   ├── quest_models.py            # Quest & reward models, multi-reward Orbs summing, task prioritization
│   └── rate_limiter.py            # Per-route token bucket rate limiter & HTTP 429 Retry-After handler
├── database/
│   ├── connection.py              # SQLite3 WAL connection manager
│   ├── migrations.py              # Schema migrations (sessions, quest_history, progress_snapshots, error_log)
│   └── repository.py              # Persistence repository for session recovery & completion history
├── ui/
│   ├── dashboard.py               # Rich / ANSI terminal mode selector & live dashboard renderer
│   └── logger.py                  # Structured terminal logger with automatic token redaction
├── utils/
│   ├── exceptions.py              # Typed exception hierarchy (Auth, RateLimit, ServerError)
│   └── formatting.py              # Unicode block progress bars, ETA calculator & duration formatters
└── tests/
    ├── test_models_and_auth.py    # Unit tests for ad-decision payloads, Orbs detection & redaction
    ├── test_database_and_tasks.py # Async tests for SQLite persistence & batch task completion
    └── test_rate_limiter.py       # Unit tests for 429 backoff & in-flight GET deduplication
```

---

## 🧪 Running Unit Tests

```bash
python3 -m unittest discover -s orion/tests -v
```

---

## 🔒 Security & Privacy

1. **Zero Credential Leakage**: Your Discord token is held strictly in memory inside `Authenticator` (`repr=False`) and automatically scrubbed from all terminal and SQLite logs via `OrionTerminalLogger`.
2. **Terminal Line Masking**: When you paste your token interactively, Orion immediately overwrites the echoed line with `[REDACTED]` using ANSI cursor control.
3. **Local SQLite WAL Storage**: Session history and quest completion records are stored locally in `orion_state.db` on your machine—no third-party servers or external telemetry.
