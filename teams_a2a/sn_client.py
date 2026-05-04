"""Minimal ServiceNow client for the v3 skill.

Self-contained per the MS skills sample shape: skill talks to its
downstream system directly (no separate bridge service in the call path).

This is the spike-grade subset of `bridge/servicenow_bridge.py` reduced
to just what `endConversation` needs:
    - resolve a Teams user email to sys_user.sys_id (cached)
    - POST the scripted REST `/open_chat` to create interaction +
      sys_cs_conversation + queued awa_work_item

Future iteration will add `send_message` for the user<->rep relay path.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests


_log = logging.getLogger("teams_a2a.sn_client")


SN_INSTANCE = (os.environ.get("SN_INSTANCE") or "").rstrip("/")
SN_USER = os.environ.get("SN_USER") or ""
SN_PASSWORD = os.environ.get("SN_PASSWORD") or ""
SN_BRIDGE_API_BASE = os.environ.get("SN_BRIDGE_API_BASE") or "/api/1833944/intranet_bridge"
SN_REQUEST_TIMEOUT = float(os.environ.get("SN_REQUEST_TIMEOUT", "15"))

# Defaults match bridge/servicenow_bridge.py for the same SN PDI.
SN_DEFAULT_USER_SYS_ID = os.environ.get(
    "SN_DEFAULT_USER_SYS_ID", "e23081fb3b580310e4058e0f23e45a88"
)
SN_DEFAULT_QUEUE_SYS_ID = os.environ.get(
    "SN_DEFAULT_QUEUE_SYS_ID", "3787b03b3b180310e4058e0f23e45ad0"
)
SN_DEFAULT_CHANNEL_SYS_ID = os.environ.get(
    "SN_DEFAULT_CHANNEL_SYS_ID", "27f675e3739713004a905ee515f6a7c3"
)


def _configured() -> bool:
    return bool(SN_INSTANCE and SN_USER and SN_PASSWORD)


_user_cache: dict[str, str] = {}


def email_to_sys_user_sys_id(email: str | None) -> str | None:
    """Resolve email/upn -> sys_user.sys_id via Table API. Cached per process."""
    if not email or not _configured():
        return None
    key = email.lower()
    cached = _user_cache.get(key)
    if cached is not None:
        return cached or None
    try:
        r = requests.get(
            f"{SN_INSTANCE}/api/now/table/sys_user",
            params={
                "sysparm_query": f"email={email}^ORuser_name={email}",
                "sysparm_fields": "sys_id,email,user_name",
                "sysparm_limit": "1",
            },
            auth=(SN_USER, SN_PASSWORD),
            headers={"Accept": "application/json"},
            timeout=SN_REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("result") or []
        sys_id = rows[0].get("sys_id") if rows else None
    except Exception:  # noqa: BLE001
        _log.exception("sys_user lookup failed for %s", email)
        sys_id = None
    _user_cache[key] = sys_id or ""
    return sys_id


def open_chat(
    *,
    bridge_session_id: str,
    user_email: str | None,
    initial_query: str,
    short_description: str | None = None,
) -> dict[str, Any]:
    """Create live-chat artefacts in SN and queue an AWA work item.

    Returns dict with `interaction_sys_id`, `interaction_number`,
    `conversation_sys_id`, `sn_user_sys_id`. Raises on HTTP failure.
    """
    if not _configured():
        raise RuntimeError(
            "ServiceNow env vars (SN_INSTANCE/SN_USER/SN_PASSWORD) not configured"
        )

    sn_user_sys_id = email_to_sys_user_sys_id(user_email) or SN_DEFAULT_USER_SYS_ID
    label = user_email or "Copilot Studio user"
    body = {
        "bridge_session_id": bridge_session_id,
        "user_sys_id": sn_user_sys_id,
        "first_message": initial_query or "",
        "queue_sys_id": SN_DEFAULT_QUEUE_SYS_ID,
        "channel_sys_id": SN_DEFAULT_CHANNEL_SYS_ID,
        "short_description": (
            short_description or f"Copilot Studio handoff from {label}"
        ),
    }
    r = requests.post(
        f"{SN_INSTANCE}{SN_BRIDGE_API_BASE}/open_chat",
        json=body,
        auth=(SN_USER, SN_PASSWORD),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=SN_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    result = (r.json() or {}).get("result") or {}
    return {
        "interaction_sys_id": result.get("interaction_sys_id"),
        "interaction_number": result.get("interaction_number"),
        "conversation_sys_id": result.get("conversation_sys_id"),
        "sn_user_sys_id": sn_user_sys_id,
    }


def send_user_message(
    *,
    conversation_sys_id: str,
    sn_user_sys_id: str,
    text: str,
) -> None:
    """Forward a user-side message into a live SN chat.

    Uses the scripted REST `/send_message` endpoint (which calls
    `sn_cs.AgentChatScriptObject.send()` so the CSR's SOW pane updates
    live). Mirrors `bridge/servicenow_bridge.py:sn_append_user_message`.

    Caller is responsible for echo-suppression bookkeeping (recording the
    text in `ActiveHandoff.recent_user_texts`) BEFORE invoking this, so
    the SN BR re-delivery of our own insert can be discarded.
    """
    if not _configured():
        raise RuntimeError("ServiceNow env vars not configured")
    if not (conversation_sys_id and sn_user_sys_id):
        raise ValueError("conversation_sys_id and sn_user_sys_id required")
    body = {
        "conversation_sys_id": conversation_sys_id,
        "user_sys_id": sn_user_sys_id,
        "text": text or "",
    }
    r = requests.post(
        f"{SN_INSTANCE}{SN_BRIDGE_API_BASE}/send_message",
        json=body,
        auth=(SN_USER, SN_PASSWORD),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=SN_REQUEST_TIMEOUT,
    )
    if r.status_code >= 400:
        _log.error(
            "send_message %s body=%r resp=%r",
            r.status_code, body, (r.text or "")[:1000],
        )
    r.raise_for_status()
