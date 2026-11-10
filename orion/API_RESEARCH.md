# Project Orion — Discord Quest API Research & Endpoint Compatibility Report

## Phase 1 & 2: Comprehensive API Research & Verification Matrix

This document details the Discord API capabilities related to Quests, user identity verification, rate-limit semantics, and error contracts. Project Orion adheres strictly to transparent, non-deceptive API communication and explicitly distinguishes between **officially documented public endpoints** and **undocumented client-internal endpoints**, flagging unsupported or server-managed tasks rather than spoofing completion.

---

## 1. Endpoint Verification & Compatibility Matrix

| Operation | Method | URL Path | Auth Header | Verification Status | Orion Support Policy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Current User Identity** | `GET` | `/api/v10/users/@me` | `Authorization` | **Official / Verified** | Supported for account verification & display |
| **Quest Discovery & State** | `GET` | `/api/v10/quests/@me` | `Authorization` | **Client-Internal (Observed)** | Supported for read-only discovery & progress polling |
| **Single Quest Status** | `GET` | `/api/v10/quests/{quest_id}` | `Authorization` | **Client-Internal (Observed)** | Supported for targeted progress refresh |
| **Quest Enrollment (Accept)** | `POST` | `/api/v10/quests/{quest_id}/enroll` | `Authorization` | **Client-Internal (Observed)** | Supported when user explicitly enrolls in an opt-in quest |
| **Video Progress Heartbeat** | `POST` | `/api/v10/quests/{quest_id}/video-progress` | `Authorization` | **Client-Internal / Client-Gated** | **Read-Only Monitoring** (Requires genuine video playback timestamp state) |
| **Desktop / Stream Heartbeat** | `POST` | `/api/v10/quests/{quest_id}/heartbeat` | `Authorization` | **Client-Internal / IPC-Gated** | **Read-Only Monitoring** (Managed by local Discord Desktop RPC/IPC process) |
| **Console / In-Game Achievement** | `N/A` | Server-to-Server OAuth2 Link | Partner OAuth2 | **Server-Managed** | **Flagged as Server-Managed / External** (Updated by game publisher backend) |
| **Reward Claim / Code Retrieval** | `POST` | `/api/v10/quests/{quest_id}/claim-reward` | `Authorization` | **Client-Internal (Observed)** | Supported only after Discord confirms `completed_at != null` |

---

## 2. Detailed Endpoint Specifications

### 2.1 Account Identity Verification
- **Method**: `GET`
- **URL**: `https://discord.com/api/v10/users/@me`
- **Authentication**: `Authorization: <token>` (or `Bearer <oauth_token>`)
- **Request Parameters**: None
- **Response Structure**:
  ```json
  {
    "id": "80351110224678912",
    "username": "nelly",
    "Global_name": "Nelly",
    "discriminator": "0",
    "avatar": "8342729096ea3675442027381ff50dfe",
    "verified": true
  }
  ```
- **Verification Status**: Officially documented in Discord Developer Documentation.

### 2.2 Quest Discovery, Metadata, Requirements, Expiration & Progress
- **Method**: `GET`
- **URL**: `https://discord.com/api/v10/quests/@me`
- **Authentication**: Required (`Authorization` header)
- **Request Parameters**: None
- **Response Structure**:
  ```json
  {
    "quests": [
      {
        "id": "124508918273645123",
        "preview": false,
        "config": {
          "id": "124508918273645123",
          "config_version": 2,
          "starts_at": "2026-11-01T00:00:00+00:00",
          "expires_at": "2026-11-20T23:59:59+00:00",
          "application": {
            "id": "356875221078245376",
            "name": "Cyberpunk 2077",
            "link": "https://www.cyberpunk.net"
          },
          "messages": {
            "quest_name": "Night City Mercenary Quest",
            "game_title": "Cyberpunk 2077",
            "game_publisher": "CD PROJEKT RED"
          },
          "task_config": {
            "type": 1,
            "join_operator": "or",
            "tasks": {
              "PLAY_ON_DESKTOP": {
                "event_name": "PLAY_ON_DESKTOP",
                "target": 900,
                "external_ids": []
              }
            }
          },
          "rewards_config": {
            "assignment_method": 1,
            "rewards": [
              {
                "type": 3,
                "sku_id": "124508999111222333",
                "messages": {
                  "name": "Samurai Glitch Avatar Decoration",
                  "name_with_article": "a Samurai Glitch Avatar Decoration"
                },
                "orb_quantity": null
              }
            ],
            "rewards_expire_at": "2027-01-20T23:59:59+00:00"
          }
        },
        "user_status": {
          "user_id": "80351110224678912",
          "quest_id": "124508918273645123",
          "enrolled_at": "2026-11-08T14:22:10+00:00",
          "completed_at": null,
          "claimed_at": null,
          "stream_progress_seconds": 0,
          "dismissed_quest_content": 0,
          "progress": {
            "PLAY_ON_DESKTOP": {
              "event_name": "PLAY_ON_DESKTOP",
              "value": 450,
              "updated_at": "2026-11-09T03:15:00+00:00",
              "completed_at": null,
              "heartbeat": {
                "last_beat_at": "2026-11-09T03:15:00+00:00",
                "expires_at": "2026-11-09T03:20:00+00:00"
              }
            }
          }
        }
      }
    ],
    "excluded_quests": []
  }
  ```

### 2.3 Quest Task Categories & Support Classification

Project Orion categorizes all Quest task requirements into explicit support tiers:

1. **`WATCH_VIDEO`** (`event_name: "WATCH_VIDEO"`):
   - **Target Unit**: Seconds of video watched (e.g., `15` to `180` seconds).
   - **Progress Tracking**: Reported in `user_status.progress.WATCH_VIDEO.value`.
   - **Support Mode**: `MONITOR_SUPPORTED` — Orion polls progress snapshots and calculates remaining duration.

2. **`WATCH_VIDEO_ON_MOBILE`** (`event_name: "WATCH_VIDEO_ON_MOBILE"`):
   - **Target Unit**: Seconds of video watched on Discord Mobile client.
   - **Support Mode**: `CLIENT_RESTRICTED` — Requires active mobile client session; Orion monitors server-reported progress and flags the mobile requirement.

3. **`PLAY_ON_DESKTOP`** (`event_name: "PLAY_ON_DESKTOP"`):
   - **Target Unit**: Seconds of game execution detected by Discord Desktop (`900` seconds = 15 minutes typical).
   - **Support Mode**: `MONITOR_SUPPORTED` — Orion monitors `user_status.progress.PLAY_ON_DESKTOP.value` and `heartbeat.last_beat_at` while the user runs the game with Discord Desktop open.

4. **`STREAM_ON_DESKTOP`** (`event_name: "STREAM_ON_DESKTOP"`):
   - **Target Unit**: Seconds of Go Live application streaming to at least 1 viewer in a voice channel (`900` seconds typical).
   - **Support Mode**: `MONITOR_SUPPORTED` — Orion monitors `stream_progress_seconds` and `progress.STREAM_ON_DESKTOP.value`.

5. **`PLAY_ACTIVITY`** (`event_name: "PLAY_ACTIVITY"`):
   - **Target Unit**: Seconds spent inside an Embedded App / Discord Activity (`900` seconds typical).
   - **Support Mode**: `MONITOR_SUPPORTED` — Orion monitors activity progress updates.

6. **`ACHIEVEMENT_IN_GAME` / Achievement-Based Quests**:
   - **Target Unit**: Event count or milestone integer pushed by the partner game server via Discord Partner API.
   - **Support Mode**: `SERVER_MANAGED` — Cannot be advanced by client heartbeats; Orion flags as external server-managed and polls for server confirmation.

7. **`PLAY_ON_XBOX` / `PLAY_ON_PLAYSTATION` / Console-Based Quests**:
   - **Target Unit**: Seconds of console presence transmitted via linked Xbox Network / PlayStation Network accounts.
   - **Support Mode**: `SERVER_MANAGED` — Managed via OAuth2 console presence bridge; Orion monitors remote progress without fabricating local events.

---

## 3. Rate-Limit Headers & Backoff Contract

Discord enforces both **route-specific buckets** and **global rate limits**:

- `X-RateLimit-Limit`: Maximum requests permitted in the current bucket window.
- `X-RateLimit-Remaining`: Remaining requests before `429 Too Many Requests`.
- `X-RateLimit-Reset`: Epoch timestamp (float seconds) when the bucket resets.
- `X-RateLimit-Reset-After`: Relative seconds (float) until bucket reset.
- `X-RateLimit-Bucket`: Opaque hash identifying the shared route bucket.
- `X-RateLimit-Global`: `true` if the rate limit applies across all routes.
- `Retry-After`: Seconds to wait when HTTP `429` is returned.

Orion's `RateLimiter` (`orion/discord/rate_limiter.py`):
- Pre-emptively sleeps when `X-RateLimit-Remaining == 0` for a known route bucket before issuing the next request.
- Honors `Retry-After` on HTTP `429` responses (both global and route-specific).
- Implements bounded exponential backoff with jitter (`base * 2^attempt + uniform(0, 0.5)`) on transient `500/502/503/504` errors.
- Deduplicates concurrent identical `GET` requests in flight so rapid UI refreshes never spam the Discord API.

---

## 4. Structured Error Responses

| HTTP Status | Discord JSON Code | Orion Exception Class | Handling Strategy |
| :--- | :--- | :--- | :--- |
| `401 Unauthorized` | `0` / `40001` | `AuthenticationError` | Halt active tasks immediately; never retry invalid tokens. |
| `403 Forbidden` | `40002` / `50001` | `AuthorizationError` | Mark operation restricted; log warning without retrying. |
| `404 Not Found` | `10002` / `unknown` | `EndpointNotFoundError` | Mark quest/endpoint unavailable; stop tracking that quest. |
| `429 Too Many Requests` | `N/A` | `RateLimitExceededError` | Suspend bucket or global lock for `retry_after` seconds; retry up to `max_retries`. |
| `500`–`504` | `N/A` | `DiscordServerError` | Exponential backoff retry up to `max_retries`. |
