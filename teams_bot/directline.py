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
from urllib.parse import urlparse

import requests

from . import config


_log = logging.getLogger(__name__)


# Default Bot Framework Direct Line endpoint. Copilot Studio issues tokens
# bound to a *regional* DL endpoint (for example
# `https://europe.directline.botframework.com`); the actual base is captured
# per-conversation from the `streamUrl` field returned by the token endpoint
# so we never POST to the wrong region.
DIRECTLINE_BASE_DEFAULT = "https://directline.botframework.com/v3/directline"


@dataclass
class DirectLineConversation:
    """State for one CS Direct Line conversation tied to a BridgeSession."""

    sid: str
    token: str = ""
    conversation_id: str = ""
    expires_at: float = 0.0  # monotonic-ish epoch seconds when the token expires
    watermark: str | None = None
    # Region-correct DL base, e.g. https://europe.directline.botframework.com/v3/directline
    dl_base: str = DIRECTLINE_BASE_DEFAULT
    # Activity IDs we posted as the user; used to filter our own messages out
    # of the poll response (DL rewrites `from.id` to the token's encoded user
    # id so a from.id == sid filter doesn't match).
    sent_activity_ids: set[str] = field(default_factory=set)
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


def _mint_token(sid: str) -> tuple[str, str, float, str]:
    """Ask the bridge's own /directline/token route for a fresh token bound to
    this bridge session id (used as the Direct Line `User.Id`).

    Returns ``(token, conversation_id, expires_at, dl_base)`` where ``dl_base``
    is the region-correct Direct Line REST root parsed from the response's
    ``streamUrl`` (so we don't end up POSTing CS-issued tokens to the wrong
    region's gateway and getting a 404)."""
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
    dl_base = _dl_base_from_stream_url(payload.get("streamUrl") or "")
    return token, conv_id, time.time() + max(60.0, expires_in - 60.0), dl_base


def _dl_base_from_stream_url(stream_url: str) -> str:
    """Convert ``wss://<region>.directline.botframework.com/v3/directline/...``
    (or the regional https variant) into the matching ``https://<host>/v3/directline``
    REST base. Falls back to the global DL endpoint if parsing fails."""
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


def _ensure_token(conv: DirectLineConversation) -> None:
    """Mint a token on first use, then explicitly start a Direct Line
    conversation so we get an authoritative ``streamUrl`` (and thus the
    region-correct host). Refresh ~1 min before expiry.

    We deliberately ignore any ``conversationId`` returned by the bridge's
    token endpoint — Copilot Studio's token endpoint returns a conv id that
    isn't reachable on the global DL gateway and doesn't include a
    ``streamUrl``, so we'd have no way to know which regional host to call.
    Calling ``POST /v3/directline/conversations`` ourselves returns both.
    """
    if conv.token and time.time() < conv.expires_at and conv.conversation_id:
        return
    token, _ignored_conv_id, expires_at, dl_base = _mint_token(conv.sid)
    conv.token = token
    conv.expires_at = expires_at
    # Always re-start the conversation when re-minting the token so we don't
    # try to reuse a conv_id that's tied to an expired token.
    conv.conversation_id = ""
    conv.watermark = None
    conv_id, real_dl_base = _start_conversation(
        dl_base or DIRECTLINE_BASE_DEFAULT, token, conv.sid
    )
    conv.conversation_id = conv_id
    conv.dl_base = real_dl_base or dl_base or DIRECTLINE_BASE_DEFAULT


def _start_conversation(dl_base: str, token: str, user_id: str) -> tuple[str, str]:
    """Start a DL conversation and return ``(conversation_id, region_dl_base)``.

    The region-correct DL base is derived from the ``streamUrl`` field of the
    response, which is the only reliable way to know which regional gateway
    Copilot Studio assigned this conversation to.
    """
    r = requests.post(
        f"{dl_base}/conversations",
        headers={"Authorization": f"Bearer {token}"},
        json={"user": {"id": user_id}},
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    conv_id = payload["conversationId"]
    real_base = _dl_base_from_stream_url(payload.get("streamUrl") or "")
    return conv_id, real_base or dl_base


def post_user_text(sid: str, text: str) -> None:
    """Send a user message into the CS Direct Line conversation."""
    conv = _get_or_create(sid)
    with conv.lock:
        _ensure_token(conv)
        r = requests.post(
            f"{conv.dl_base}/conversations/{conv.conversation_id}/activities",
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
                f"{conv.dl_base}/conversations/{conv.conversation_id}/activities",
                headers={
                    "Authorization": f"Bearer {conv.token}",
                    "Content-Type": "application/json",
                },
                json={"type": "message", "from": {"id": sid}, "text": text},
                timeout=15,
            )
        r.raise_for_status()
        try:
            posted_id = (r.json() or {}).get("id") or ""
        except ValueError:
            posted_id = ""
        if posted_id:
            conv.sent_activity_ids.add(posted_id)


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
            url = f"{conv.dl_base}/conversations/{conv.conversation_id}/activities"
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
            act_id = act.get("id") or ""
            if act_id and act_id in conv.sent_activity_ids:
                conv.sent_activity_ids.discard(act_id)
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
