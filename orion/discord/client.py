"""Asynchronous HTTP & Gateway Presence client for Discord Quest discovery, enrollment, video-progress, and heartbeats."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any, Callable, Optional
import urllib.error
import urllib.parse
import urllib.request

try:
    import aiohttp  # type: ignore
except ImportError:
    aiohttp = None  # type: ignore

from orion.config import OrionConfig
from orion.discord.authentication import Authenticator
from orion.discord.quest_models import DiscordUser
from orion.discord.rate_limiter import RateLimiter
from orion.utils.exceptions import (
    AuthenticationError,
    AuthorizationError,
    DiscordAPIError,
    DiscordServerError,
    EndpointNotFoundError,
    OrionError,
    RateLimitExceededError,
)


class DiscordAPIClient:
    """
    Asynchronous HTTP & Gateway Presence client for Discord API communication.
    Emits structured HTTP telemetry logs (`OK -- GET/POST /api/v9/...`), enforces rate limits,
    and maintains a lightweight Desktop Gateway WebSocket presence so `/heartbeat` requests
    for PLAY_ON_DESKTOP and PLAY_ACTIVITY quests are credited by Discord's backend.
    """

    def __init__(
        self,
        config: OrionConfig,
        authenticator: Authenticator,
        rate_limiter: Optional[RateLimiter] = None,
        on_api_log: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.config = config
        self.authenticator = authenticator
        self.rate_limiter = rate_limiter or RateLimiter(
            max_retries=config.max_retries,
            backoff_base_seconds=config.backoff_base_seconds,
        )
        self.on_api_log = on_api_log
        self._session: Any = None
        self._connected_user: Optional[DiscordUser] = None
        self._demo_progress_offsets: dict[str, float] = {}

        # Optional Gateway WebSocket state for Desktop presence during Play quests
        self._ws: Any = None
        self._ws_task: Optional[asyncio.Task[None]] = None
        self._ws_ready: asyncio.Event = asyncio.Event()
        self._active_games: dict[str, tuple[str, str, int]] = {}  # quest_id -> (app_id, app_name, start_ms)

    @property
    def is_connected(self) -> bool:
        return self._connected_user is not None

    @property
    def connected_user(self) -> Optional[DiscordUser]:
        return self._connected_user

    @property
    def api_version_prefix(self) -> str:
        return "/api/v9" if "/v9" in self.config.api_base_url else "/api/v10"

    async def ensure_session(self) -> Any:
        if aiohttp is None:
            return None
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
            timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_seconds)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._ws_task is not None and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
        self._ws_task = None
        if self._ws is not None and not getattr(self._ws, "closed", True):
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        if self._session is not None and not getattr(self._session, "closed", True):
            await self._session.close()
        self._session = None

    async def ensure_gateway_presence(self) -> None:
        """
        Ensure a lightweight Discord Gateway WebSocket session is active in the background
        so Discord's backend sees an active Desktop session when `/quests/{id}/heartbeat` is called.
        """
        if self.config.demo_mode or aiohttp is None or not self.authenticator.is_configured:
            return
        if self._ws_task is not None and not self._ws_task.done():
            return
        self._ws_ready.clear()
        self._ws_task = asyncio.create_task(self._gateway_loop())
        try:
            await asyncio.wait_for(self._ws_ready.wait(), timeout=3.5)
        except asyncio.TimeoutError:
            pass

    async def register_active_game(self, quest_id: str, app_id: str, app_name: str) -> None:
        """Register a game in the Gateway presence activities list and broadcast Opcode 3."""
        if self.config.demo_mode:
            return
        clean_app_id = str(app_id).strip()
        if clean_app_id and clean_app_id != "0":
            self._active_games[quest_id] = (clean_app_id, app_name, int(time.time() * 1000))
        await self.ensure_gateway_presence()
        await self._send_gateway_presence_update()

    async def unregister_active_game(self, quest_id: str) -> None:
        """Remove a completed/stopped quest game from the Gateway presence activities list."""
        if quest_id in self._active_games:
            self._active_games.pop(quest_id, None)
            await self._send_gateway_presence_update()

    def _build_activities_list(self) -> list[dict[str, Any]]:
        activities: list[dict[str, Any]] = []
        seen_apps: set[str] = set()
        for app_id, app_name, start_ms in self._active_games.values():
            if app_id in seen_apps:
                continue
            seen_apps.add(app_id)
            activities.append(
                {
                    "name": app_name,
                    "type": 0,
                    "application_id": app_id,
                    "timestamps": {"start": start_ms},
                }
            )
        return activities

    async def _send_gateway_presence_update(self) -> None:
        if self._ws is None or getattr(self._ws, "closed", True):
            return
        try:
            payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": self._build_activities_list(),
                    "status": "online",
                    "afk": False,
                },
            }
            await self._ws.send_str(json.dumps(payload))
        except Exception:
            pass

    async def _gateway_loop(self) -> None:
        session = await self.ensure_session()
        if session is None or aiohttp is None:
            return
        gw_url = "wss://gateway.discord.gg/?v=9&encoding=json"
        try:
            async with session.ws_connect(
                gw_url,
                headers={"User-Agent": self.config.user_agent},
                heartbeat=30.0,
            ) as ws:
                self._ws = ws
                hello_msg = await ws.receive_json(timeout=5.0)
                hb_interval_ms = (
                    hello_msg.get("d", {}).get("heartbeat_interval", 41250)
                    if isinstance(hello_msg, dict)
                    else 41250
                )
                identify_payload = {
                    "op": 2,
                    "d": {
                        "token": self.authenticator.get_raw_token(),
                        "capabilities": 16381,
                        "properties": self.authenticator.get_super_properties_dict(
                            self.config.user_agent
                        ),
                        "presence": {
                            "status": "online",
                            "since": 0,
                            "activities": self._build_activities_list(),
                            "afk": False,
                        },
                        "compress": False,
                        "client_state": {
                            "guild_versions": {},
                            "highest_last_message_id": "0",
                            "read_state_version": 0,
                            "user_guild_settings_version": -1,
                            "user_settings_version": -1,
                            "private_channels_version": "0",
                            "api_code_version": 0,
                        },
                    },
                }
                await ws.send_str(json.dumps(identify_payload))
                self._ws_ready.set()

                if self.on_api_log is not None:
                    res = self.on_api_log(
                        "OK -- Connected Desktop Gateway session (wss://gateway.discord.gg v9)"
                    )
                    if asyncio.iscoroutine(res):
                        await res

                last_seq: Optional[int] = None
                hb_sec = max(15.0, float(hb_interval_ms) / 1000.0)

                async def _hb_sender() -> None:
                    while not ws.closed:
                        await asyncio.sleep(hb_sec)
                        if not ws.closed:
                            await ws.send_str(json.dumps({"op": 1, "d": last_seq}))

                hb_task = asyncio.create_task(_hb_sender())
                try:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                if isinstance(data, dict) and data.get("s") is not None:
                                    last_seq = data.get("s")
                            except Exception:
                                pass
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                finally:
                    hb_task.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._ws_ready.set()
        finally:
            self._ws = None

    async def verify_account(self) -> DiscordUser:
        if self.config.demo_mode:
            await asyncio.sleep(0.02)
            user = DiscordUser(
                id="984120558192017408",
                username="orion.operator",
                global_name="Orion Operator",
                discriminator="0",
                verified=True,
            )
            self._connected_user = user
            return user

        payload = await self.request("GET", "/users/@me")
        user = DiscordUser.model_validate(payload)
        self._connected_user = user
        return user

    async def fetch_quest_decisions(self) -> list[dict[str, Any]]:
        """
        Probe Discord's Quest Home decision placements (`/quests/get-decisions` & `/quests/decision`)
        so un-enrolled sponsored Video Quests (e.g., 200 Orbs Video Quests) are discovered alongside `/quests/@me`.
        """
        if self.config.demo_mode:
            return []

        discovered_items: list[dict[str, Any]] = []
        hb_id = self.authenticator.client_heartbeat_session_id
        ad_id = self.authenticator.client_ad_session_id

        # Probe /quests/get-decisions for Desktop & Quest Home placements
        for placement in (1, 3, 4):
            try:
                data = await self.request(
                    "GET",
                    "/quests/get-decisions",
                    params={
                        "placement": placement,
                        "num_decisions_requested": 15,
                        "client_heartbeat_session_id": hb_id,
                        "client_ad_session_id": ad_id,
                    },
                    silent_errors=True,
                )
                if isinstance(data, dict):
                    decisions = data.get("decisions") or data.get("quests") or []
                    if isinstance(decisions, list):
                        for item in decisions:
                            if isinstance(item, dict):
                                discovered_items.append(item)
            except Exception:
                pass

        # Also probe single-placement /quests/decision for Quest Home Banner / Video Modal
        for placement in (1, 3, 4, 5):
            try:
                data = await self.request(
                    "GET",
                    "/quests/decision",
                    params={
                        "placement": placement,
                        "client_heartbeat_session_id": hb_id,
                        "client_ad_session_id": ad_id,
                    },
                    silent_errors=True,
                )
                if isinstance(data, dict) and (data.get("quest") or data.get("creative")):
                    discovered_items.append(data)
            except Exception:
                pass

        return discovered_items

    async def enroll_quest(
        self,
        quest_id: str,
        *,
        metadata_sealed: Optional[str] = None,
        traffic_metadata_sealed: Optional[str] = None,
    ) -> dict[str, Any]:
        """Enroll the authenticated user in an available Quest."""
        if self.config.demo_mode:
            await asyncio.sleep(0.02)
            return {"quest_id": quest_id, "enrolled_at": datetime.now(timezone.utc).isoformat()}
        body: dict[str, Any] = {
            "location": 11,
            "is_targeted": bool(metadata_sealed or traffic_metadata_sealed),
            "metadata_raw": None,
        }
        if metadata_sealed:
            body["metadata_sealed"] = metadata_sealed
        if traffic_metadata_sealed:
            body["traffic_metadata_sealed"] = traffic_metadata_sealed
        return await self.request(
            "POST",
            f"/quests/{quest_id}/enroll",
            json_body=body,
        )

    async def send_video_progress(self, quest_id: str, timestamp: float) -> dict[str, Any]:
        """Report WATCH_VIDEO or WATCH_VIDEO_ON_MOBILE timestamp progress to Discord API."""
        if self.config.demo_mode:
            await asyncio.sleep(0.02)
            self._demo_progress_offsets[quest_id] = max(
                self._demo_progress_offsets.get(quest_id, 0.0), float(timestamp)
            )
            return {
                "quest_id": quest_id,
                "timestamp": round(float(timestamp), 2),
                "progress": {
                    "WATCH_VIDEO": {
                        "event_name": "WATCH_VIDEO",
                        "value": round(float(timestamp), 2),
                    }
                },
            }
        return await self.request(
            "POST",
            f"/quests/{quest_id}/video-progress",
            json_body={"timestamp": round(float(timestamp), 4)},
        )

    async def send_heartbeat(
        self,
        quest_id: str,
        stream_key: str,
        *,
        application_id: Optional[str] = None,
        terminal: bool = False,
    ) -> dict[str, Any]:
        """Send periodic Quest heartbeat for PLAY_ON_DESKTOP, PLAY_ACTIVITY, or STREAM_ON_DESKTOP."""
        if self.config.demo_mode:
            await asyncio.sleep(0.02)
            self.advance_demo_progress(quest_id, 225.0)
            val = self._demo_progress_offsets.get(quest_id, 225.0)
            return {
                "quest_id": quest_id,
                "stream_key": stream_key,
                "terminal": terminal,
                "progress": {
                    "PLAY_ON_DESKTOP": {"value": val},
                    "PLAY_ACTIVITY": {"value": val},
                    "STREAM_ON_DESKTOP": {"value": val},
                },
            }
        body: dict[str, Any] = {
            "stream_key": stream_key,
            "terminal": bool(terminal),
        }
        if application_id and str(application_id).strip() not in {"", "0", "None"}:
            body["application_id"] = str(application_id).strip()
        return await self.request(
            "POST",
            f"/quests/{quest_id}/heartbeat",
            json_body=body,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        silent_errors: bool = False,
    ) -> dict[str, Any]:
        normalized_method = method.upper()
        route_key = f"{normalized_method}:{path}"

        if normalized_method == "GET" and params is None:
            return await self.rate_limiter.deduplicated_call(
                route_key,
                lambda: self._execute_with_retries(
                    normalized_method,
                    path,
                    route_key=route_key,
                    params=params,
                    json_body=json_body,
                    silent_errors=silent_errors,
                ),
            )
        return await self._execute_with_retries(
            normalized_method,
            path,
            route_key=route_key,
            params=params,
            json_body=json_body,
            silent_errors=silent_errors,
        )

    async def _perform_http(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: Optional[dict[str, Any]],
        json_body: Optional[dict[str, Any]],
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        session = await self.ensure_session()
        if session is not None and aiohttp is not None:
            async with session.request(
                method, url, headers=headers, params=params, json=json_body
            ) as response:
                resp_headers = {k: v for k, v in response.headers.items()}
                status = response.status
                if status == 204:
                    return status, resp_headers, {}
                try:
                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        data = {"data": data}
                except Exception:
                    text_fallback = await response.text()
                    data = {"message": text_fallback[:250]}
                return status, resp_headers, data

        full_url = url
        if params:
            full_url = f"{url}?{urllib.parse.urlencode(params)}"
        body_bytes = json.dumps(json_body).encode("utf-8") if json_body is not None else None

        def _sync_req() -> tuple[int, dict[str, str], dict[str, Any]]:
            req = urllib.request.Request(full_url, data=body_bytes, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.config.request_timeout_seconds) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    parsed = json.loads(raw) if raw else {}
                    return (
                        resp.status,
                        dict(resp.headers.items()),
                        parsed if isinstance(parsed, dict) else {"data": parsed},
                    )
            except urllib.error.HTTPError as http_err:
                raw_err = http_err.read().decode("utf-8", errors="replace")
                try:
                    parsed_err = json.loads(raw_err) if raw_err else {}
                except Exception:
                    parsed_err = {"message": raw_err[:250]}
                return http_err.code, dict(http_err.headers.items()), parsed_err

        return await asyncio.to_thread(_sync_req)

    async def _execute_with_retries(
        self,
        method: str,
        path: str,
        *,
        route_key: str,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        silent_errors: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.config.api_base_url}{path}"
        headers = self.authenticator.build_auth_headers(self.config.user_agent)
        lock = self.rate_limiter.get_lock_for_route(route_key)
        ver_prefix = self.api_version_prefix

        for attempt in range(self.config.max_retries + 1):
            try:
                async with lock:
                    await self.rate_limiter.acquire(route_key)
                    status, resp_headers, data = await self._perform_http(
                        method, url, headers, params, json_body
                    )
                    bucket_state = self.rate_limiter.update_from_headers(route_key, resp_headers)

                if 200 <= status < 300:
                    if self.on_api_log is not None and not silent_errors:
                        rem = f"{bucket_state.remaining}/{bucket_state.limit}" if bucket_state else "ok"
                        res = self.on_api_log(
                            f"OK -- {method} {ver_prefix}{path} -> HTTP {status} (rate-limit: {rem})"
                        )
                        if asyncio.iscoroutine(res):
                            await res
                    return data

                discord_code = data.get("code") if isinstance(data, dict) else None
                message = (
                    data.get("message")
                    if isinstance(data, dict) and data.get("message")
                    else f"Discord API returned HTTP {status}"
                )

                if status == 401:
                    self._connected_user = None
                    raise AuthenticationError(
                        str(message),
                        status_code=401,
                        discord_code=discord_code,
                        route=route_key,
                    )
                if status == 403:
                    raise AuthorizationError(
                        str(message),
                        status_code=403,
                        discord_code=discord_code,
                        route=route_key,
                    )
                if status == 404:
                    raise EndpointNotFoundError(
                        str(message),
                        status_code=404,
                        discord_code=discord_code,
                        route=route_key,
                    )
                if status == 429:
                    retry_after, is_global, bucket_id = (
                        self.rate_limiter.record_rate_limit_response(
                            route_key, resp_headers, data
                        )
                    )
                    if attempt >= self.config.max_retries:
                        raise RateLimitExceededError(
                            f"Rate limit exceeded after {attempt + 1} attempts on {route_key}",
                            retry_after=retry_after,
                            is_global=is_global,
                            bucket=bucket_id,
                            route=route_key,
                        )
                    await asyncio.sleep(retry_after)
                    continue

                if status >= 500:
                    if attempt >= self.config.max_retries:
                        raise DiscordServerError(
                            str(message),
                            status_code=status,
                            discord_code=discord_code,
                            route=route_key,
                        )
                    await asyncio.sleep(self.rate_limiter.compute_backoff(attempt))
                    continue

                raise DiscordAPIError(
                    str(message),
                    status_code=status,
                    discord_code=discord_code,
                    route=route_key,
                    details=data,
                )

            except asyncio.CancelledError:
                raise
            except OrionError:
                raise
            except Exception as exc:
                if attempt >= self.config.max_retries:
                    raise DiscordAPIError(
                        f"Network transport error contacting Discord API: {exc}",
                        status_code=503,
                        route=route_key,
                    ) from exc
                await asyncio.sleep(self.rate_limiter.compute_backoff(attempt))

        raise DiscordAPIError("Exhausted retries without a valid response.", status_code=503, route=route_key)

    def advance_demo_progress(self, quest_id: str, delta_seconds: float) -> None:
        self._demo_progress_offsets[quest_id] = (
            self._demo_progress_offsets.get(quest_id, 0.0) + delta_seconds
        )

    def build_demo_quests_payload(self) -> dict[str, Any]:
        """Return active Discord Quests matching the real Discord Quest lineup (Orbs + Others)."""
        now = datetime.now(timezone.utc)
        starts = (now - timedelta(days=2)).isoformat()
        exp_11_12 = (now + timedelta(days=3, hours=6)).isoformat()
        exp_11_16 = (now + timedelta(days=7, hours=4)).isoformat()
        exp_11_19 = (now + timedelta(days=10, hours=8)).isoformat()
        exp_11_23 = (now + timedelta(days=14, hours=2)).isoformat()

        def _val(qid: str, base: float, target: float) -> float:
            return min(target, base + self._demo_progress_offsets.get(qid, 0.0))

        return {
            "quests": [
                {
                    "id": "130491827364510001",
                    "config": {
                        "id": "130491827364510001",
                        "starts_at": starts,
                        "expires_at": exp_11_12,
                        "application": {"id": "112233445566778801", "name": "Arknights: Endfield"},
                        "messages": {
                            "quest_name": "ENDFIELD IS NOW ON STEAM QUEST (Video)",
                            "game_title": "Arknights: Endfield",
                            "game_publisher": "GRYPHLINE",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "WATCH_VIDEO": {
                                    "event_name": "WATCH_VIDEO",
                                    "target": 29,
                                }
                            },
                        },
                        "rewards_config": {
                            "rewards": [
                                {
                                    "type": 4,
                                    "sku_id": "sku_endfield_200_orbs",
                                    "messages": {"name": "200 Orbs"},
                                    "orb_quantity": 200,
                                }
                            ]
                        },
                    },
                    "user_status": {
                        "enrolled_at": starts,
                        "completed_at": (
                            now.isoformat()
                            if _val("130491827364510001", 12, 29) >= 29
                            else None
                        ),
                        "progress": {
                            "WATCH_VIDEO": {
                                "event_name": "WATCH_VIDEO",
                                "value": _val("130491827364510001", 12, 29),
                            }
                        },
                    },
                },
                {
                    "id": "130491827364510002",
                    "config": {
                        "id": "130491827364510002",
                        "starts_at": starts,
                        "expires_at": exp_11_12,
                        "application": {"id": "112233445566778802", "name": "FINAL FANTASY XIV"},
                        "messages": {
                            "quest_name": "FINAL FANTASY XIV: EVERCOLD QUEST",
                            "game_title": "FINAL FANTASY XIV",
                            "game_publisher": "SQUARE ENIX",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "WATCH_VIDEO": {
                                    "event_name": "WATCH_VIDEO",
                                    "target": 28,
                                }
                            },
                        },
                        "rewards_config": {
                            "rewards": [
                                {
                                    "type": 4,
                                    "sku_id": "sku_ffxiv_200_orbs",
                                    "messages": {"name": "200 Orbs"},
                                    "orb_quantity": 200,
                                }
                            ]
                        },
                    },
                    "user_status": {
                        "enrolled_at": starts,
                        "completed_at": (
                            now.isoformat()
                            if _val("130491827364510002", 0, 28) >= 28
                            else None
                        ),
                        "progress": {
                            "WATCH_VIDEO": {
                                "event_name": "WATCH_VIDEO",
                                "value": _val("130491827364510002", 0, 28),
                            }
                        },
                    },
                },
                {
                    "id": "130491827364510003",
                    "config": {
                        "id": "130491827364510003",
                        "starts_at": starts,
                        "expires_at": exp_11_19,
                        "application": {"id": "112233445566778803", "name": "Anno 117: Pax Romana"},
                        "messages": {
                            "quest_name": "ANNO 117 | 40% OFF QUEST",
                            "game_title": "Anno 117: Pax Romana",
                            "game_publisher": "Ubisoft",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "WATCH_VIDEO": {
                                    "event_name": "WATCH_VIDEO",
                                    "target": 30,
                                }
                            },
                        },
                        "rewards_config": {
                            "rewards": [
                                {
                                    "type": 4,
                                    "sku_id": "sku_anno117_200_orbs",
                                    "messages": {"name": "200 Orbs"},
                                    "orb_quantity": 200,
                                }
                            ]
                        },
                    },
                    "user_status": {
                        "enrolled_at": starts,
                        "completed_at": (
                            now.isoformat()
                            if _val("130491827364510003", 0, 30) >= 30
                            else None
                        ),
                        "progress": {
                            "WATCH_VIDEO": {
                                "event_name": "WATCH_VIDEO",
                                "value": _val("130491827364510003", 0, 30),
                            }
                        },
                    },
                },
                {
                    "id": "130491827364510004",
                    "config": {
                        "id": "130491827364510004",
                        "starts_at": starts,
                        "expires_at": exp_11_16,
                        "application": {"id": "112233445566778804", "name": "Roblox"},
                        "messages": {
                            "quest_name": "BASKETBALL ZERO QUEST",
                            "game_title": "Basketball Zero on Roblox",
                            "game_publisher": "ROBLOX",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "PLAY_ON_DESKTOP": {
                                    "event_name": "PLAY_ON_DESKTOP",
                                    "target": 900,
                                }
                            },
                        },
                        "rewards_config": {
                            "rewards": [
                                {
                                    "type": 4,
                                    "sku_id": "sku_bball_700_orbs",
                                    "messages": {"name": "700 Orbs"},
                                    "orb_quantity": 700,
                                }
                            ]
                        },
                    },
                    "user_status": {
                        "enrolled_at": starts,
                        "completed_at": (
                            now.isoformat()
                            if _val("130491827364510004", 540, 900) >= 900
                            else None
                        ),
                        "progress": {
                            "PLAY_ON_DESKTOP": {
                                "event_name": "PLAY_ON_DESKTOP",
                                "value": _val("130491827364510004", 540, 900),
                            }
                        },
                    },
                },
                {
                    "id": "130491827364510005",
                    "config": {
                        "id": "130491827364510005",
                        "starts_at": starts,
                        "expires_at": exp_11_12,
                        "application": {"id": "112233445566778805", "name": "Arknights: Endfield"},
                        "messages": {
                            "quest_name": "ENDFIELD IS NOW ON STEAM QUEST (Play)",
                            "game_title": "Arknights: Endfield",
                            "game_publisher": "GRYPHLINE",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "PLAY_ON_DESKTOP": {
                                    "event_name": "PLAY_ON_DESKTOP",
                                    "target": 900,
                                }
                            },
                        },
                        "rewards_config": {
                            "rewards": [
                                {
                                    "type": 4,
                                    "sku_id": "sku_endfield_700_orbs",
                                    "messages": {"name": "700 Orbs"},
                                    "orb_quantity": 700,
                                }
                            ]
                        },
                    },
                    "user_status": {
                        "enrolled_at": starts,
                        "completed_at": (
                            now.isoformat()
                            if _val("130491827364510005", 180, 900) >= 900
                            else None
                        ),
                        "progress": {
                            "PLAY_ON_DESKTOP": {
                                "event_name": "PLAY_ON_DESKTOP",
                                "value": _val("130491827364510005", 180, 900),
                            }
                        },
                    },
                },
                {
                    "id": "130491827364510006",
                    "config": {
                        "id": "130491827364510006",
                        "starts_at": starts,
                        "expires_at": exp_11_16,
                        "application": {"id": "112233445566778806", "name": "Magic: The Gathering"},
                        "messages": {
                            "quest_name": "BEAT THE KOBAYASHI MARU QUEST",
                            "game_title": "MTG | Star Trek Activity",
                            "game_publisher": "Magic: The Gathering",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "PLAY_ACTIVITY": {
                                    "event_name": "PLAY_ACTIVITY",
                                    "target": 300,
                                }
                            },
                        },
                        "rewards_config": {
                            "rewards": [
                                {
                                    "type": 3,
                                    "sku_id": "sku_mtg_startrek_deco",
                                    "messages": {"name": "MTG | Star Trek Avatar Decoration"},
                                }
                            ]
                        },
                    },
                    "user_status": {
                        "enrolled_at": starts,
                        "completed_at": (
                            now.isoformat()
                            if _val("130491827364510006", 150, 300) >= 300
                            else None
                        ),
                        "progress": {
                            "PLAY_ACTIVITY": {
                                "event_name": "PLAY_ACTIVITY",
                                "value": _val("130491827364510006", 150, 300),
                            }
                        },
                    },
                },
                {
                    "id": "130491827364510007",
                    "config": {
                        "id": "130491827364510007",
                        "starts_at": starts,
                        "expires_at": exp_11_19,
                        "application": {"id": "112233445566778807", "name": "MapleStory Classic World"},
                        "messages": {
                            "quest_name": "MAPLESTORY CLASSIC WORLD QUEST",
                            "game_title": "MapleStory Classic World",
                            "game_publisher": "Nexon America",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "PLAY_ON_DESKTOP": {
                                    "event_name": "PLAY_ON_DESKTOP",
                                    "target": 900,
                                }
                            },
                        },
                        "rewards_config": {
                            "rewards": [
                                {
                                    "type": 4,
                                    "sku_id": "sku_maplestory_700_orbs",
                                    "messages": {"name": "700 Orbs"},
                                    "orb_quantity": 700,
                                }
                            ]
                        },
                    },
                    "user_status": {
                        "enrolled_at": starts,
                        "completed_at": (
                            now.isoformat()
                            if _val("130491827364510007", 0, 900) >= 900
                            else None
                        ),
                        "progress": {
                            "PLAY_ON_DESKTOP": {
                                "event_name": "PLAY_ON_DESKTOP",
                                "value": _val("130491827364510007", 0, 900),
                            }
                        },
                    },
                },
                {
                    "id": "130491827364510008",
                    "config": {
                        "id": "130491827364510008",
                        "starts_at": starts,
                        "expires_at": exp_11_23,
                        "application": {"id": "112233445566778808", "name": "Where Winds Meet"},
                        "messages": {
                            "quest_name": "VERSION 2.3: FRIENDS FROM AFAR QUEST",
                            "game_title": "Where Winds Meet",
                            "game_publisher": "NETEASE",
                        },
                        "task_config_v2": {
                            "tasks": {
                                "PLAY_ON_DESKTOP": {
                                    "event_name": "PLAY_ON_DESKTOP",
                                    "target": 900,
                                }
                            },
                        },
                        "rewards_config": {
                            "rewards": [
                                {
                                    "type": 4,
                                    "sku_id": "sku_wwm_700_orbs",
                                    "messages": {"name": "700 Orbs"},
                                    "orb_quantity": 700,
                                }
                            ]
                        },
                    },
                    "user_status": {
                        "enrolled_at": starts,
                        "completed_at": (
                            now.isoformat()
                            if _val("130491827364510008", 0, 900) >= 900
                            else None
                        ),
                        "progress": {
                            "PLAY_ON_DESKTOP": {
                                "event_name": "PLAY_ON_DESKTOP",
                                "value": _val("130491827364510008", 0, 900),
                            }
                        },
                    },
                },
            ]
        }
