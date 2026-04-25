"""ServiceNow handoff bridge.

Mediates between an intranet webchat session (Copilot Studio over Direct Line)
and a ServiceNow live agent who works in their native ServiceNow UI.

State machine per session:
    BOT       -> default; messages go to/from Copilot Studio in the browser
    QUEUED    -> escalation requested; SN interaction created, awaiting rep
    LIVE      -> rep claimed; user msgs -> SN interaction; rep msgs -> WS push
    CLOSED    -> ended; UI returns to BOT

Endpoints (registered as a Flask blueprint):
    POST /api/servicenow/init-session       Browser pre-creates a bridge session on page load.
    POST /api/servicenow/agent/create-ticket  Copilot Studio agent: log incident, no escalation.
    POST /api/servicenow/agent/escalate     Copilot Studio agent: hand off to live rep.
    POST /api/servicenow/escalate           Browser-initiated fallback escalation (manual button).
    POST /api/servicenow/user-message       Browser sends user message during LIVE.
    POST /api/servicenow/webhook            ServiceNow Business Rule -> rep reply.
    GET  /api/servicenow/poll/<sid>         Fallback long-poll for rep replies.
    WS   /ws/intranet/<sid>                 Push channel for rep replies + status.

ServiceNow auth uses basic auth via SN_USER / SN_PASSWORD in the environment.
The inbound BR webhook is protected by SN_WEBHOOK_SECRET (header X-Bridge-Secret).
The agent endpoints are protected by AGENT_API_SECRET (header X-Agent-Secret).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, request


SN_INSTANCE = (os.environ.get("SN_INSTANCE") or "").rstrip("/")
SN_USER = os.environ.get("SN_USER")
SN_PASSWORD = os.environ.get("SN_PASSWORD")
SN_WEBHOOK_SECRET = os.environ.get("SN_WEBHOOK_SECRET") or "dev-shared-secret-change-me"
AGENT_API_SECRET = os.environ.get("AGENT_API_SECRET") or "dev-agent-secret-change-me"
SN_REQUEST_TIMEOUT = float(os.environ.get("SN_REQUEST_TIMEOUT", "15"))

# Scripted REST namespace exposed by tools/sn_scripted_rest_open_chat.js etc.
SN_BRIDGE_API_BASE = os.environ.get("SN_BRIDGE_API_BASE") or "/api/1833944/intranet_bridge"
# Default mapping for browser user -> ServiceNow sys_user.sys_id when the
# browser hasn't been authenticated against SN. Used by Alice probe flow.
SN_DEFAULT_USER_SYS_ID = os.environ.get("SN_DEFAULT_USER_SYS_ID") or "e23081fb3b580310e4058e0f23e45a88"
SN_DEFAULT_QUEUE_SYS_ID = os.environ.get("SN_DEFAULT_QUEUE_SYS_ID") or "3787b03b3b180310e4058e0f23e45ad0"  # IT Help Chat
SN_DEFAULT_CHANNEL_SYS_ID = os.environ.get("SN_DEFAULT_CHANNEL_SYS_ID") or "27f675e3739713004a905ee515f6a7c3"  # Chat

# How long a Teams session in queued / live / closed state may sit idle
# before the next user turn auto-recycles it back to a fresh BOT session.
# Without this, a user who escalated a week ago is stuck talking to a
# long-dead live-chat that the CSR has long since left.
TEAMS_SESSION_IDLE_TIMEOUT_S = float(
    os.environ.get("TEAMS_SESSION_IDLE_TIMEOUT_S", "3600")
)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

STATE_BOT = "bot"
STATE_QUEUED = "queued"
STATE_LIVE = "live"
STATE_CLOSED = "closed"


@dataclass
class BridgeSession:
    sid: str
    state: str = STATE_BOT
    interaction_sys_id: str | None = None
    interaction_number: str | None = None
    conversation_sys_id: str | None = None
    sn_user_sys_id: str | None = None
    user_email: str | None = None
    user_display_name: str | None = None
    rep_name: str | None = None
    created_at: float = field(default_factory=time.time)
    # Updated whenever the user sends a turn (Teams or web) so we can detect
    # stale sessions and auto-recycle them.
    last_activity_at: float = field(default_factory=time.time)
    # Buffered rep messages for clients that poll instead of using WS.
    pending: deque = field(default_factory=deque)
    websocket: Any = None  # flask_sock Server, set when client connects
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Texts the user just sent into ServiceNow. The SN sys_cs_message BR
    # fires on every insert (including ours), so the webhook can re-deliver
    # the user's own text as a rep message. We drop the matching echo.
    recent_user_texts: deque = field(default_factory=lambda: deque(maxlen=20))
    # ----- Teams channel additions (see ../teams_bot/) -----
    # "web" (browser webchat, default) | "teams" (Bot Framework relay)
    channel: str = "web"
    # Stable per-Teams-user key (AAD object id) used to look up an existing
    # session on subsequent turns so we don't allocate a new sid per message.
    teams_user_key: str | None = None
    # Serialized BotFramework ConversationReference for proactive push via
    # adapter.continue_conversation. Stored as a plain dict so the bridge
    # itself stays free of botbuilder imports (lazy import in the push hook).
    teams_conversation_reference: dict | None = None


_sessions: dict[str, BridgeSession] = {}
_sessions_lock = threading.Lock()
# Reverse index for webhook lookups by interaction sys_id
_by_interaction: dict[str, str] = {}
# Reverse index for Teams: AAD user key -> bridge sid (so each Teams user
# keeps a stable sid across turns / app restarts of the Teams client).
_by_teams_user: dict[str, str] = {}


def _get_session(sid: str) -> BridgeSession | None:
    with _sessions_lock:
        return _sessions.get(sid)


def _new_session(user_email: str | None, user_display_name: str | None) -> BridgeSession:
    sid = uuid.uuid4().hex
    s = BridgeSession(sid=sid, user_email=user_email, user_display_name=user_display_name)
    with _sessions_lock:
        _sessions[sid] = s
    return s


def _email_to_sn_user_sys_id(email: str | None) -> str | None:
    """Resolve an AAD email/upn to a sys_user.sys_id via the SN Table API.
    Returns None on no-match so the caller can fall back to SN_DEFAULT_USER_SYS_ID.
    Result is cached per-process; the dev cache is fine for the demo scope."""
    if not email:
        return None
    cached = _sn_user_cache.get(email.lower())
    if cached is not None:
        return cached or None
    if not (SN_INSTANCE and SN_USER and SN_PASSWORD):
        return None
    try:
        r = requests.get(
            _sn_url("/api/now/table/sys_user"),
            params={
                "sysparm_query": f"email={email}^ORuser_name={email}",
                "sysparm_fields": "sys_id,email,user_name",
                "sysparm_limit": "1",
            },
            auth=_sn_auth(),
            headers={"Accept": "application/json"},
            timeout=SN_REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("result") or []
        sys_id = rows[0].get("sys_id") if rows else None
    except Exception:  # noqa: BLE001
        sys_id = None
    _sn_user_cache[email.lower()] = sys_id or ""
    return sys_id


_sn_user_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# ServiceNow REST helpers
# ---------------------------------------------------------------------------

def _sn_auth() -> tuple[str, str]:
    if not (SN_INSTANCE and SN_USER and SN_PASSWORD):
        raise RuntimeError("ServiceNow env vars (SN_INSTANCE/SN_USER/SN_PASSWORD) not configured")
    return (SN_USER, SN_PASSWORD)


def _sn_url(path: str) -> str:
    return f"{SN_INSTANCE}{path}"


def sn_open_chat(session: BridgeSession, opening_message: str) -> dict:
    """Create a fully-wired live chat (sys_cs_conversation + interaction +
    sys_cs_session + sys_cs_session_binding + queued awa_work_item) via the
    Scripted REST endpoint installed by tools/sn_scripted_rest_open_chat.js.

    Captures conversation_sys_id and sn_user_sys_id on the session so
    subsequent /send_message calls (and webhook lookups) can resolve.
    """
    sn_user_sys_id = session.sn_user_sys_id or SN_DEFAULT_USER_SYS_ID
    body = {
        "bridge_session_id": session.sid,
        "user_sys_id": sn_user_sys_id,
        "first_message": opening_message or "",
        "queue_sys_id": SN_DEFAULT_QUEUE_SYS_ID,
        "channel_sys_id": SN_DEFAULT_CHANNEL_SYS_ID,
        "short_description": (
            f"Intranet webchat handoff from {session.user_display_name or session.user_email or 'guest'}"
        ),
    }
    r = requests.post(
        _sn_url(f"{SN_BRIDGE_API_BASE}/open_chat"),
        json=body,
        auth=_sn_auth(),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=SN_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    result = r.json().get("result") or {}
    session.sn_user_sys_id = sn_user_sys_id
    session.conversation_sys_id = result.get("conversation_sys_id")
    return {
        "sys_id": result.get("interaction_sys_id"),
        "number": result.get("interaction_number"),
        "conversation_sys_id": result.get("conversation_sys_id"),
    }


def sn_create_incident(short_description: str, description: str, caller_email: str | None = None) -> dict:
    """Create a regular incident record (no live-agent involvement)."""
    body = {
        "short_description": short_description or "Intranet webchat ticket",
        "description": description or "",
        "contact_type": "chat",
        "category": "inquiry",
    }
    if caller_email:
        body["caller_id"] = caller_email  # SN will resolve email -> sys_user if it matches
    r = requests.post(
        _sn_url("/api/now/table/incident"),
        json=body,
        auth=_sn_auth(),
        headers={"Accept": "application/json"},
        timeout=SN_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["result"]


def sn_append_user_message(session: BridgeSession, text: str) -> None:
    """Forward a user message into the live chat via /send_message Scripted REST,
    which calls sn_cs.AgentChatScriptObject.send() so the agent's SOW pane
    updates live (vs. raw GlideRecord insert which only persists the row)."""
    if not (session.conversation_sys_id and session.sn_user_sys_id):
        return
    # Remember exactly what we sent so the webhook can drop the echo.
    norm = (text or "").strip()
    if norm:
        with session.lock:
            session.recent_user_texts.append(norm)
    body = {
        "conversation_sys_id": session.conversation_sys_id,
        "user_sys_id": session.sn_user_sys_id,
        "text": text,
    }
    r = requests.post(
        _sn_url(f"{SN_BRIDGE_API_BASE}/send_message"),
        json=body,
        auth=_sn_auth(),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=SN_REQUEST_TIMEOUT,
    )
    r.raise_for_status()


# ---------------------------------------------------------------------------
# Push to browser
# ---------------------------------------------------------------------------

def _push_to_browser(session: BridgeSession, payload: dict) -> None:
    """Send a JSON frame to the browser via WS if connected, else buffer for poll."""
    encoded = json.dumps(payload)
    delivered = False
    with session.lock:
        ws = session.websocket
        if ws is not None:
            try:
                ws.send(encoded)
                delivered = True
            except Exception:
                # WS is dead; drop reference so future pushes go to buffer.
                session.websocket = None
        if not delivered:
            session.pending.append(payload)


def _push_to_user(session: BridgeSession, payload: dict) -> None:
    """Channel-aware dispatcher. Web sessions go to the WS/poll buffer; Teams
    sessions are pushed via the Bot Framework adapter's continue_conversation."""
    if session.channel == "teams":
        # Always also buffer in pending so a future /poll endpoint or debug
        # tool can inspect what was sent. Cheap insurance and matches web shape.
        with session.lock:
            session.pending.append(payload)
        try:
            from teams_bot.push import push as _teams_push
        except Exception:  # noqa: BLE001
            current_app.logger.exception("[push] teams_bot import failed")
            return
        _teams_push(session.teams_conversation_reference, payload)
        return
    _push_to_browser(session, payload)


# ---------------------------------------------------------------------------
# Flask blueprint
# ---------------------------------------------------------------------------

bp = Blueprint("servicenow_bridge", __name__)


def _require_agent_secret() -> bool:
    secret = request.headers.get("X-Agent-Secret") or request.args.get("agent_secret")
    return secret == AGENT_API_SECRET


def _escalate_session(s: BridgeSession, opening_message: str) -> dict:
    """Promote a session from BOT to QUEUED by opening a live chat in SN."""
    result = sn_open_chat(s, opening_message)
    s.interaction_sys_id = result.get("sys_id")
    s.interaction_number = result.get("number")
    s.state = STATE_QUEUED
    with _sessions_lock:
        _by_interaction[s.interaction_sys_id] = s.sid
    _push_to_user(
        s,
        {
            "type": "status",
            "state": s.state,
            "interaction_number": s.interaction_number,
            "rep_name": None,
        },
    )
    return result


@bp.route("/api/servicenow/init-session", methods=["POST"])
def init_session():
    """Browser pre-creates a bridge session on page load. Returns the session id
    which the browser then uses as its Direct Line user id so the Copilot Studio
    agent can reference it when calling the agent endpoints."""
    data = request.get_json(silent=True) or {}
    s = _new_session(
        user_email=(data.get("user_email") or "").strip() or None,
        user_display_name=(data.get("user_display_name") or "").strip() or None,
    )
    return jsonify({"session_id": s.sid, "state": s.state})


@bp.route("/api/teams/init-session", methods=["POST"])
def init_session_teams():
    """Idempotent per-Teams-user session lookup/create.

    Called by the relay bot (teams_bot/) on every turn. The first call for a
    given `teams_user_key` allocates a new BridgeSession (channel="teams")
    and resolves the AAD email to a sys_user.sys_id; later calls return the
    same sid (so escalations and webhooks continue to find the right session).

    Always refreshes the stored ConversationReference because Teams may rotate
    serviceUrl, conversation ids, etc. across app upgrades or device switches.
    """
    data = request.get_json(silent=True) or {}
    teams_user_key = (data.get("teams_user_key") or "").strip()
    if not teams_user_key:
        return jsonify({"error": "teams_user_key required"}), 400

    user_email = (data.get("user_email") or "").strip() or None
    user_display_name = (data.get("user_display_name") or "").strip() or None
    conversation_reference = data.get("conversation_reference") or None

    with _sessions_lock:
        existing_sid = _by_teams_user.get(teams_user_key)
    s = _get_session(existing_sid) if existing_sid else None

    # Auto-recycle a stale non-BOT session: if the user comes back days /
    # weeks after escalating, the live-chat is long dead and forwarding more
    # messages into it just produces silence. Treat the next turn as a fresh
    # bot conversation. The old BridgeSession is dropped from the in-memory
    # store; the SN-side records (interaction etc.) remain in SN as history.
    if s is not None and s.state != STATE_BOT:
        idle_for = time.time() - (s.last_activity_at or s.created_at)
        if s.state == STATE_CLOSED or idle_for > TEAMS_SESSION_IDLE_TIMEOUT_S:
            current_app.logger.info(
                "[teams] recycling stale session sid=%s state=%s idle_s=%.0f",
                s.sid, s.state, idle_for,
            )
            with _sessions_lock:
                _by_teams_user.pop(teams_user_key, None)
                _sessions.pop(s.sid, None)
                if s.interaction_sys_id:
                    _by_interaction.pop(s.interaction_sys_id, None)
            s = None

    if s is None:
        s = _new_session(user_email=user_email, user_display_name=user_display_name)
        s.channel = "teams"
        s.teams_user_key = teams_user_key
        s.sn_user_sys_id = (
            _email_to_sn_user_sys_id(user_email) or SN_DEFAULT_USER_SYS_ID
        )
        with _sessions_lock:
            _by_teams_user[teams_user_key] = s.sid
    else:
        # Refresh display name / email if the bot now has a better value.
        if user_display_name and not s.user_display_name:
            s.user_display_name = user_display_name
        if user_email and not s.user_email:
            s.user_email = user_email
            resolved = _email_to_sn_user_sys_id(user_email)
            if resolved:
                s.sn_user_sys_id = resolved

    s.last_activity_at = time.time()

    if conversation_reference:
        s.teams_conversation_reference = conversation_reference

    return jsonify(
        {
            "session_id": s.sid,
            "state": s.state,
            "rep_name": s.rep_name,
            "interaction_number": s.interaction_number,
        }
    )


@bp.route("/api/teams/reset-session", methods=["POST"])
def reset_session_teams():
    """Drop a Teams user's session so the next turn allocates a fresh one.

    The relay bot calls this when the user types "new" after a closed chat.
    """
    data = request.get_json(silent=True) or {}
    teams_user_key = (data.get("teams_user_key") or "").strip()
    if not teams_user_key:
        return jsonify({"error": "teams_user_key required"}), 400
    with _sessions_lock:
        sid = _by_teams_user.pop(teams_user_key, None)
        if sid:
            s = _sessions.pop(sid, None)
            if s and s.interaction_sys_id:
                _by_interaction.pop(s.interaction_sys_id, None)
    return jsonify({"ok": True})


@bp.route("/api/servicenow/agent/create-ticket", methods=["POST"])
def agent_create_ticket():
    """Called by the Copilot Studio agent when the user's request is best handled
    by logging an incident (no live rep needed). Returns the ticket number for
    the agent to read back to the user."""
    current_app.logger.info("[agent] create-ticket hit headers=%s body=%s", dict(request.headers), request.get_data(as_text=True))
    if not _require_agent_secret():
        current_app.logger.warning("[agent] create-ticket forbidden (bad/missing X-Agent-Secret)")
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    short_description = (data.get("short_description") or "").strip()
    description = (data.get("description") or "").strip()
    caller_email = (data.get("caller_email") or "").strip() or None
    if not short_description:
        return jsonify({"error": "short_description required"}), 400
    try:
        result = sn_create_incident(short_description, description, caller_email)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Agent create-ticket failed")
        return jsonify({"error": "create_failed", "detail": str(exc)}), 502
    return jsonify({"ticket_number": result.get("number"), "ticket_sys_id": result.get("sys_id")})


@bp.route("/api/servicenow/agent/escalate", methods=["POST"])
def agent_escalate():
    """Called by the Copilot Studio agent when triage decides this needs a
    human. The agent passes the user's bridge session id (which is also the
    Direct Line user id). We create the SN interaction, transition the session
    to QUEUED, and push a WS event so the browser switches to the live-rep UI."""
    current_app.logger.info("[agent] escalate hit headers=%s body=%s", dict(request.headers), request.get_data(as_text=True))
    if not _require_agent_secret():
        current_app.logger.warning("[agent] escalate forbidden (bad/missing X-Agent-Secret)")
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    sid = (data.get("session_id") or data.get("user_id") or "").strip()
    opening_message = (data.get("opening_message") or data.get("summary") or "").strip()
    if not sid:
        return jsonify({"error": "session_id required"}), 400
    s = _get_session(sid)
    if not s:
        return jsonify({"error": "unknown session"}), 404
    if s.state in (STATE_QUEUED, STATE_LIVE):
        # Idempotent re-escalation: just return the existing interaction.
        return jsonify(
            {
                "ticket_number": s.interaction_number,
                "ticket_sys_id": s.interaction_sys_id,
                "state": s.state,
            }
        )
    try:
        result = _escalate_session(s, opening_message)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Agent escalate failed")
        return jsonify({"error": "escalate_failed", "detail": str(exc)}), 502
    return jsonify(
        {
            "ticket_number": result.get("number"),
            "ticket_sys_id": result.get("sys_id"),
            "state": s.state,
        }
    )


@bp.route("/api/servicenow/escalate", methods=["POST"])
def escalate():
    """Manual fallback: browser button bypasses the agent and escalates directly."""
    data = request.get_json(silent=True) or {}
    user_email = (data.get("user_email") or "").strip() or None
    user_display_name = (data.get("user_display_name") or "").strip() or None
    opening_message = (data.get("opening_message") or "").strip()
    sid = (data.get("session_id") or "").strip()

    s = _get_session(sid) if sid else None
    if s is None:
        s = _new_session(user_email=user_email, user_display_name=user_display_name)

    # Idempotency: if this session already has an interaction (e.g. the agent
    # already called /api/servicenow/agent/escalate from its HTTP tool, then
    # emitted handoff.initiate which causes the browser to call us here),
    # return the existing one instead of opening a second chat in ServiceNow.
    if s.interaction_sys_id:
        return jsonify(
            {
                "session_id": s.sid,
                "state": s.state,
                "interaction_number": s.interaction_number,
                "interaction_sys_id": s.interaction_sys_id,
                "already_escalated": True,
            }
        )

    try:
        result = _escalate_session(s, opening_message)
    except requests.HTTPError as exc:
        current_app.logger.exception("ServiceNow interaction create failed")
        body = exc.response.text if exc.response is not None else str(exc)
        return jsonify({"error": "servicenow_create_failed", "detail": body}), 502
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("ServiceNow interaction create failed")
        return jsonify({"error": "servicenow_create_failed", "detail": str(exc)}), 502

    return jsonify(
        {
            "session_id": s.sid,
            "state": s.state,
            "interaction_number": s.interaction_number,
            "interaction_sys_id": s.interaction_sys_id,
        }
    )


@bp.route("/api/servicenow/user-message", methods=["POST"])
def user_message():
    data = request.get_json(silent=True) or {}
    sid = (data.get("session_id") or "").strip()
    text = (data.get("text") or "").strip()
    if not sid or not text:
        return jsonify({"error": "session_id and text required"}), 400
    s = _get_session(sid)
    if not s:
        return jsonify({"error": "unknown session"}), 404
    if s.state == STATE_CLOSED:
        return jsonify({"error": "session closed"}), 409
    try:
        sn_append_user_message(s, text)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Forwarding user message to ServiceNow failed")
        return jsonify({"error": "forward_failed", "detail": str(exc)}), 502
    return jsonify({"ok": True})


@bp.route("/api/servicenow/webhook", methods=["POST"])
def webhook():
    """Receive rep replies from a ServiceNow Business Rule.

    Expected JSON body (set by the BR):
        {
            "interaction_sys_id": "...",
            "bridge_session_id": "...",
            "rep_name": "Jane Doe",
            "text": "<the reply>",
            "event": "reply" | "claimed" | "closed"
        }
    """
    secret = request.headers.get("X-Bridge-Secret") or request.args.get("secret")
    if secret != SN_WEBHOOK_SECRET:
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    sid = (data.get("bridge_session_id") or "").strip()
    interaction_sys_id = (data.get("interaction_sys_id") or "").strip()

    s: BridgeSession | None = None
    if sid:
        s = _get_session(sid)
    if s is None and interaction_sys_id:
        with _sessions_lock:
            mapped = _by_interaction.get(interaction_sys_id)
        if mapped:
            s = _get_session(mapped)
    if s is None:
        return jsonify({"error": "session not found"}), 404

    event = (data.get("event") or "reply").lower()
    text = (data.get("text") or "").strip()
    rep_name = (data.get("rep_name") or "").strip() or s.rep_name

    if event == "claimed":
        s.state = STATE_LIVE
        s.rep_name = rep_name or "Support Agent"
        _push_to_user(s, {"type": "status", "state": s.state, "rep_name": s.rep_name})
    elif event == "closed":
        s.state = STATE_CLOSED
        _push_to_user(s, {"type": "status", "state": s.state})
    elif event == "typing":
        # Rep is typing in SN; surface as a transient indicator on the user
        # side. Web ignores by default (no-op in browser dispatcher); Teams
        # renders an Activity(type="typing"). See teams_bot/push.py.
        _push_to_user(s, {"type": "typing", "rep_name": s.rep_name})
    else:
        # First reply implicitly transitions to LIVE if not already
        if s.state == STATE_QUEUED:
            s.state = STATE_LIVE
            if rep_name:
                s.rep_name = rep_name
            _push_to_user(s, {"type": "status", "state": s.state, "rep_name": s.rep_name})
        if text:
            # Drop our own echo: SN's BR fires on every sys_cs_message insert,
            # including the consumer-direction inserts we just made on behalf
            # of the user. Match on exact text against the per-session buffer.
            norm = text.strip()
            with s.lock:
                try:
                    s.recent_user_texts.remove(norm)
                    is_echo = True
                except ValueError:
                    is_echo = False
            if is_echo:
                current_app.logger.info(
                    "[webhook] dropping user-text echo for session=%s text=%r", s.sid, norm[:80]
                )
            else:
                _push_to_user(
                    s,
                    {"type": "message", "from": "rep", "rep_name": s.rep_name, "text": text},
                )

    return jsonify({"ok": True})


@bp.route("/api/servicenow/poll/<sid>", methods=["GET"])
def poll(sid: str):
    s = _get_session(sid)
    if not s:
        return jsonify({"error": "unknown session"}), 404
    drained = []
    with s.lock:
        while s.pending:
            drained.append(s.pending.popleft())
    return jsonify({
        "state": s.state,
        "rep_name": s.rep_name,
        "interaction_number": s.interaction_number,
        "events": drained,
    })


def register_websocket(sock):
    """Register the WS push channel on the given flask_sock instance."""

    @sock.route("/ws/intranet/<sid>")
    def _ws(ws, sid):
        # Brief retry: client may open the WS just before the escalate POST
        # response is committed in this worker's view of the session map.
        s = _get_session(sid)
        if not s:
            for _ in range(5):
                time.sleep(0.1)
                s = _get_session(sid)
                if s:
                    break
        if not s:
            try:
                ws.send(json.dumps({"type": "error", "error": "unknown session"}))
            except Exception:
                pass
            return
        with s.lock:
            s.websocket = ws
            backlog = list(s.pending)
            s.pending.clear()
        # Flush anything buffered before WS connected
        for evt in backlog:
            try:
                ws.send(json.dumps(evt))
            except Exception:
                break
        # Keep the socket open; we read to detect close but don't expect messages.
        try:
            while True:
                msg = ws.receive(timeout=60)
                if msg is None:
                    # Heartbeat / idle; loop and keep the socket alive.
                    continue
        except Exception:
            pass
        finally:
            with s.lock:
                if s.websocket is ws:
                    s.websocket = None
