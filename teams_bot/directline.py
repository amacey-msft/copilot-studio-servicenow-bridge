"""Per-bridge-session Direct Line client.

Each Teams `BridgeSession` owns one long-lived Direct Line conversation against
the Copilot Studio agent. We use polling (GET /activities?watermark=N) rather
than the WebSocket streamUrl because polling is dead-simple and the relay bot
only needs to pump replies for ~10s after each user turn.

The Direct Line *token* and *conversationId* come from the bridge's existing
`POST /directline/token` route, which already supports both the Copilot Studio
token-endpoint flow and the Direct Line channel-secret fallback. Reusing that
route means the relay bot has zero secrets of its own to manage for DL.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import requests

from . import config


_log = logging.getLogger(__name__)


DIRECTLINE_BASE = "https://directline.botframework.com/v3/directline"


@dataclass
class DirectLineConversation:
    """State for one CS Direct Line conversation tied to a BridgeSession."""

    sid: str
    token: str = ""
    conversation_id: str = ""
    expires_at: float = 0.0  # monotonic-ish epoch seconds when the token expires
    watermark: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


# Per-process registry; keyed by BridgeSession.sid. The bridge's own session
# store is the source of truth for everything else, but the DL conversation is
# Teams-only state that lives in this module.
_conversations: dict[str, DirectLineConversation] = {}
_registry_lock = threading.Lock()


def _get_or_create(sid: str) -> DirectLineConversation:
    with _registry_lock:
        c = _conversations.get(sid)
        if c is None:
            c = DirectLineConversation(sid=sid)
            _conversations[sid] = c
        return c


def reset(sid: str) -> None:
    """Drop the cached conversation; the next call will mint a fresh one."""
    with _registry_lock:
        _conversations.pop(sid, None)


def _mint_token(sid: str) -> tuple[str, str, float]:
    """Ask the bridge's own /directline/token route for a fresh token bound to
    this bridge session id (used as the Direct Line `User.Id`)."""
    r = requests.post(
        f"{config.BRIDGE_INTERNAL_URL}/directline/token",
        json={"user_id": sid},
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    token = payload.get("token") or ""
    conv_id = payload.get("conversationId") or ""
    expires_in = float(payload.get("expires_in") or 3600)
    if not token:
        raise RuntimeError(f"directline/token returned no token: {payload!r}")
    return token, conv_id, time.time() + max(60.0, expires_in - 60.0)


def _ensure_token(conv: DirectLineConversation) -> None:
    """Mint a token + conversationId on first use; refresh ~1 min before expiry."""
    if conv.token and time.time() < conv.expires_at and conv.conversation_id:
        return
    token, conv_id, expires_at = _mint_token(conv.sid)
    conv.token = token
    conv.expires_at = expires_at
    if not conv.conversation_id:
        conv.conversation_id = conv_id
    elif conv_id and conv_id != conv.conversation_id:
        # The token endpoint minted a brand-new conversation. Reset watermark
        # so we don't hold a stale pointer into the old one.
        conv.conversation_id = conv_id
        conv.watermark = None
    if not conv.conversation_id:
        # DIRECTLINE_SECRET path *should* return conversationId; if it didn't,
        # explicitly start a conversation here.
        conv.conversation_id = _start_conversation(conv.token, conv.sid)


def _start_conversation(token: str, user_id: str) -> str:
    r = requests.post(
        f"{DIRECTLINE_BASE}/conversations",
        headers={"Authorization": f"Bearer {token}"},
        json={"user": {"id": user_id}},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["conversationId"]


def post_user_text(sid: str, text: str) -> None:
    """Send a user message into the CS Direct Line conversation."""
    conv = _get_or_create(sid)
    with conv.lock:
        _ensure_token(conv)
        r = requests.post(
            f"{DIRECTLINE_BASE}/conversations/{conv.conversation_id}/activities",
            headers={
                "Authorization": f"Bearer {conv.token}",
                "Content-Type": "application/json",
            },
            json={
                "type": "message",
                "from": {"id": sid},
                "text": text,
            },
            timeout=15,
        )
        if r.status_code in (401, 403, 410):
            # Token or conversation expired; reset and retry once.
            _log.warning("[directline] %s on post; refreshing conversation", r.status_code)
            conv.token = ""
            conv.conversation_id = ""
            conv.watermark = None
            _ensure_token(conv)
            r = requests.post(
                f"{DIRECTLINE_BASE}/conversations/{conv.conversation_id}/activities",
                headers={
                    "Authorization": f"Bearer {conv.token}",
                    "Content-Type": "application/json",
                },
                json={"type": "message", "from": {"id": sid}, "text": text},
                timeout=15,
            )
        r.raise_for_status()


def collect_bot_replies(sid: str) -> list[dict]:
    """Drain pending CS-bot activities for this session.

    Polls until a quiet period (no new activities for `DIRECTLINE_QUIET_PERIOD_S`)
    or the per-turn ceiling (`DIRECTLINE_TURN_TIMEOUT_S`) is reached. Returns the
    raw activities authored by anyone other than this user (id == sid).

    Also surfaces `event` activities (e.g. `handoff.initiate`) so the caller can
    react to escalation triggers.
    """
    conv = _get_or_create(sid)
    deadline = time.time() + config.DIRECTLINE_TURN_TIMEOUT_S
    quiet_deadline = time.time() + config.DIRECTLINE_QUIET_PERIOD_S
    collected: list[dict] = []

    while time.time() < deadline:
        with conv.lock:
            _ensure_token(conv)
            url = f"{DIRECTLINE_BASE}/conversations/{conv.conversation_id}/activities"
            params = {"watermark": conv.watermark} if conv.watermark else {}
            r = requests.get(
                url,
                headers={"Authorization": f"Bearer {conv.token}"},
                params=params,
                timeout=15,
            )
        if r.status_code in (401, 403, 410):
            _log.warning("[directline] %s on poll; refreshing conversation", r.status_code)
            conv.token = ""
            conv.conversation_id = ""
            conv.watermark = None
            time.sleep(config.DIRECTLINE_POLL_INTERVAL_S)
            continue
        r.raise_for_status()
        body = r.json()
        new_watermark = body.get("watermark")
        activities = body.get("activities") or []
        progress = False
        for act in activities:
            from_id = ((act.get("from") or {}).get("id") or "")
            if from_id == sid:
                continue
            collected.append(act)
            progress = True
        if new_watermark is not None:
            conv.watermark = str(new_watermark)
        if progress:
            quiet_deadline = time.time() + config.DIRECTLINE_QUIET_PERIOD_S
        if time.time() >= quiet_deadline and collected:
            break
        time.sleep(config.DIRECTLINE_POLL_INTERVAL_S)

    return collected
