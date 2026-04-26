"""Async Direct Line client — parity port of `teams_bot.directline`.

Each `BridgeSession` (keyed by sid) owns one long-lived Direct Line
conversation against the Copilot Studio agent. We poll instead of using
the WebSocket streamUrl because polling is simple and we only need to
pump replies for ~10s after each user turn.

The DL token + conversationId come from the bridge's existing
`POST /directline/token` route (same as the legacy teams_bot), so the
agent has zero CS secrets of its own.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from . import config


_log = logging.getLogger(__name__)


DIRECTLINE_BASE_DEFAULT = "https://directline.botframework.com/v3/directline"


@dataclass
class _DLConv:
    sid: str
    token: str = ""
    conversation_id: str = ""
    expires_at: float = 0.0
    watermark: Optional[str] = None
    dl_base: str = DIRECTLINE_BASE_DEFAULT
    sent_activity_ids: set = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_conversations: dict[str, _DLConv] = {}
_registry_lock = asyncio.Lock()


async def _get_or_create(sid: str) -> _DLConv:
    async with _registry_lock:
        c = _conversations.get(sid)
        if c is None:
            c = _DLConv(sid=sid)
            _conversations[sid] = c
        return c


async def reset(sid: str) -> None:
    async with _registry_lock:
        _conversations.pop(sid, None)


def _dl_base_from_stream_url(stream_url: str) -> str:
    if not stream_url:
        return DIRECTLINE_BASE_DEFAULT
    try:
        parsed = urlparse(stream_url)
        host = parsed.netloc
        if not host:
            return DIRECTLINE_BASE_DEFAULT
        return f"https://{host}/v3/directline"
    except Exception:  # noqa: BLE001
        return DIRECTLINE_BASE_DEFAULT


async def _mint_token(session: aiohttp.ClientSession, sid: str) -> tuple[str, float, str]:
    """Returns (token, expires_at_epoch, dl_base). Conversation id is
    deliberately ignored — we always start a fresh DL conversation
    ourselves below to capture the regional streamUrl."""
    async with session.post(
        f"{config.BRIDGE_INTERNAL_URL}/directline/token",
        json={"user_id": sid},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as r:
        r.raise_for_status()
        payload = await r.json()
    token = payload.get("token") or ""
    if not token:
        raise RuntimeError(f"directline/token returned no token: {payload!r}")
    expires_in = float(payload.get("expires_in") or 3600)
    dl_base = _dl_base_from_stream_url(payload.get("streamUrl") or "")
    return token, time.time() + max(60.0, expires_in - 60.0), dl_base


async def _start_conversation(
    session: aiohttp.ClientSession, dl_base: str, token: str, user_id: str
) -> tuple[str, str]:
    async with session.post(
        f"{dl_base}/conversations",
        headers={"Authorization": f"Bearer {token}"},
        json={"user": {"id": user_id}},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as r:
        r.raise_for_status()
        payload = await r.json()
    conv_id = payload["conversationId"]
    real_base = _dl_base_from_stream_url(payload.get("streamUrl") or "")
    return conv_id, real_base or dl_base


async def _ensure_token(session: aiohttp.ClientSession, conv: _DLConv) -> None:
    if conv.token and time.time() < conv.expires_at and conv.conversation_id:
        return
    token, expires_at, dl_base = await _mint_token(session, conv.sid)
    conv.token = token
    conv.expires_at = expires_at
    conv.conversation_id = ""
    conv.watermark = None
    conv_id, real_dl_base = await _start_conversation(
        session, dl_base or DIRECTLINE_BASE_DEFAULT, token, conv.sid
    )
    conv.conversation_id = conv_id
    conv.dl_base = real_dl_base or dl_base or DIRECTLINE_BASE_DEFAULT
    # Extract the DL-rewritten user id from the token JWT and tell the bridge
    # to map it back to our sid. Required so the CS escalate HTTP tool (which
    # passes System.Activity.From.Id as session_id) resolves to a real session.
    dl_user_id = _decode_dl_user_id(token)
    if dl_user_id and dl_user_id != conv.sid:
        try:
            async with session.post(
                f"{config.BRIDGE_INTERNAL_URL}/api/teams/map-dl-user",
                json={"session_id": conv.sid, "dl_user_id": dl_user_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status >= 400:
                    _log.warning("[dl] map-dl-user failed status=%s", r.status)
        except Exception:  # noqa: BLE001
            _log.exception("[dl] map-dl-user request failed")


def _decode_dl_user_id(token: str) -> Optional[str]:
    """DL tokens are JWTs. The `user` claim holds the DL-bound user id."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("user") or payload.get("sub") or None
    except Exception:  # noqa: BLE001
        return None


async def post_user_text(sid: str, text: str) -> None:
    conv = await _get_or_create(sid)
    async with conv.lock:
        async with aiohttp.ClientSession() as session:
            await _ensure_token(session, conv)
            posted_id = await _post_activity(session, conv, sid, text)
            if posted_id is None:
                # Token/conversation expired; reset and retry once.
                conv.token = ""
                conv.conversation_id = ""
                conv.watermark = None
                await _ensure_token(session, conv)
                posted_id = await _post_activity(session, conv, sid, text)
            if posted_id:
                conv.sent_activity_ids.add(posted_id)


async def _post_activity(
    session: aiohttp.ClientSession, conv: _DLConv, sid: str, text: str
) -> Optional[str]:
    async with session.post(
        f"{conv.dl_base}/conversations/{conv.conversation_id}/activities",
        headers={
            "Authorization": f"Bearer {conv.token}",
            "Content-Type": "application/json",
        },
        json={"type": "message", "from": {"id": sid}, "text": text},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as r:
        if r.status in (401, 403, 410):
            _log.warning("[dl] %s on post; will retry", r.status)
            return None
        r.raise_for_status()
        try:
            body = await r.json()
            return (body or {}).get("id") or ""
        except Exception:  # noqa: BLE001
            return ""


async def collect_bot_replies(sid: str) -> list[dict]:
    """Drain pending CS activities for this session. Returns raw activity dicts."""
    conv = await _get_or_create(sid)
    deadline = time.time() + _turn_timeout()
    quiet_deadline = time.time() + _quiet_period()
    poll_interval = _poll_interval()
    collected: list[dict] = []

    async with aiohttp.ClientSession() as session:
        while time.time() < deadline:
            async with conv.lock:
                await _ensure_token(session, conv)
                url = f"{conv.dl_base}/conversations/{conv.conversation_id}/activities"
                params = {"watermark": conv.watermark} if conv.watermark else {}
                try:
                    async with session.get(
                        url,
                        headers={"Authorization": f"Bearer {conv.token}"},
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as r:
                        status = r.status
                        if status in (401, 403, 410):
                            _log.warning("[dl] %s on poll; refreshing", status)
                            conv.token = ""
                            conv.conversation_id = ""
                            conv.watermark = None
                            await asyncio.sleep(poll_interval)
                            continue
                        r.raise_for_status()
                        body = await r.json()
                except aiohttp.ClientError:
                    _log.exception("[dl] poll failed")
                    await asyncio.sleep(poll_interval)
                    continue

            new_watermark = body.get("watermark")
            activities = body.get("activities") or []
            progress = False
            for act in activities:
                act_id = act.get("id") or ""
                if act_id and act_id in conv.sent_activity_ids:
                    conv.sent_activity_ids.discard(act_id)
                    continue
                collected.append(act)
                progress = True
            if new_watermark is not None:
                conv.watermark = str(new_watermark)
            if progress:
                quiet_deadline = time.time() + _quiet_period()
            if time.time() >= quiet_deadline and collected:
                break
            await asyncio.sleep(poll_interval)

    return collected


def _turn_timeout() -> float:
    return float(getattr(config, "DIRECTLINE_TURN_TIMEOUT_S", 12.0))


def _quiet_period() -> float:
    return float(getattr(config, "DIRECTLINE_QUIET_PERIOD_S", 1.5))


def _poll_interval() -> float:
    return float(getattr(config, "DIRECTLINE_POLL_INTERVAL_S", 0.5))
