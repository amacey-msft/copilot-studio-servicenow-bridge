"""HTTP client for the existing Flask bridge.

Reuses every endpoint `teams_bot/relay.py` already calls. No changes to
the bridge required for Stage 1; Stage 2 will add `/api/teams/push` so
the bridge can push proactive replies back to this agent.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

from . import config


_log = logging.getLogger(__name__)


async def _post(path: str, body: dict, timeout_s: float = 20.0) -> Optional[dict]:
    url = f"{config.BRIDGE_INTERNAL_URL}{path}"
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body) as resp:
                resp.raise_for_status()
                if resp.content_length == 0:
                    return {}
                return await resp.json()
    except Exception:  # noqa: BLE001
        _log.exception("bridge POST %s failed", path)
        return None


async def init_session(
    teams_user_key: str,
    user_email: Optional[str],
    user_display_name: Optional[str],
    conversation_reference: dict,
) -> Optional[dict]:
    """Idempotent: returns the same `session_id` for a given user key."""
    return await _post(
        "/api/teams/init-session",
        {
            "teams_user_key": teams_user_key,
            "user_email": user_email,
            "user_display_name": user_display_name,
            "conversation_reference": conversation_reference,
        },
    )


async def reset_session(teams_user_key: str) -> None:
    await _post("/api/teams/reset-session", {"teams_user_key": teams_user_key})


async def send_user_message(session_id: str, text: str) -> None:
    await _post(
        "/api/servicenow/user-message",
        {"session_id": session_id, "text": text},
    )


async def trigger_escalation(
    session_id: str, summary: Optional[str] = None
) -> Optional[dict]:
    """Used when the agent itself initiates escalation (Genesys-style event).

    The legacy CS topic still calls `/api/servicenow/agent/escalate`
    directly via an HTTP action; this is for the Stage 2 path where the
    CS topic instead raises an event activity that this agent catches.
    """
    body: dict[str, Any] = {"session_id": session_id}
    if summary:
        body["summary"] = summary
    return await _post("/api/servicenow/agent/escalate", body)
