"""Outbound push from the bridge into a Teams 1:1 chat.

Called by `servicenow_bridge._push_to_user` when `session.channel == "teams"`.
Wraps `adapter.continue_conversation` so the rest of the bridge stays
synchronous Flask code.
"""
from __future__ import annotations

import logging

from botbuilder.core import CardFactory, TurnContext
from botbuilder.schema import Activity, ActivityTypes

from . import config, runtime


_log = logging.getLogger(__name__)


def push(reference_dict: dict | None, payload: dict) -> bool:
    """Push a bridge event into the user's Teams chat.

    Returns True on success, False if the event was dropped (e.g. no
    conversation reference yet, or the bot isn't configured).
    """
    if not config.is_configured():
        return False
    if not reference_dict:
        return False

    evt_type = (payload.get("type") or "").lower()

    async def _logic(turn_context: TurnContext) -> None:
        if evt_type == "status":
            await turn_context.send_activity(_status_activity(payload))
        elif evt_type == "message":
            text = payload.get("text") or ""
            rep = payload.get("rep_name") or "Support Agent"
            if text:
                await turn_context.send_activity(f"**{rep}:** {text}")
        elif evt_type == "typing":
            await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        else:
            _log.info("[teams.push] ignoring unknown event type %r", evt_type)

    try:
        runtime.continue_conversation_sync(reference_dict, _logic)
        return True
    except Exception:  # noqa: BLE001
        _log.exception("[teams.push] continue_conversation failed for event %r", evt_type)
        return False


# ---------------------------------------------------------------------------
# Adaptive Cards for status transitions
# ---------------------------------------------------------------------------

def _status_activity(payload: dict) -> Activity:
    state = (payload.get("state") or "").lower()
    rep_name = payload.get("rep_name") or "Support Agent"
    interaction_number = payload.get("interaction_number") or ""

    if state == "queued":
        body = [
            {"type": "TextBlock", "text": "Connecting an agent...", "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": "I've opened a ticket for you. You'll start chatting with a live agent here as soon as one is available.", "wrap": True},
        ]
        if interaction_number:
            body.append({"type": "TextBlock", "text": f"Reference: **{interaction_number}**", "wrap": True, "spacing": "Small"})
        card = _adaptive_card(body)
        return Activity(type=ActivityTypes.message, attachments=[CardFactory.adaptive_card(card)])

    if state == "live":
        body = [
            {"type": "TextBlock", "text": f"You're now chatting with {rep_name}", "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": "Anything you type next goes straight to them.", "wrap": True},
        ]
        card = _adaptive_card(body)
        return Activity(type=ActivityTypes.message, attachments=[CardFactory.adaptive_card(card)])

    if state == "closed":
        body = [
            {"type": "TextBlock", "text": "This chat has ended.", "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": "Type **new** to start a new conversation.", "wrap": True},
        ]
        card = _adaptive_card(body)
        return Activity(type=ActivityTypes.message, attachments=[CardFactory.adaptive_card(card)])

    # Unknown state -> plain text fallback so we never silently drop.
    return Activity(type=ActivityTypes.message, text=f"(status: {state})")


def _adaptive_card(body: list[dict]) -> dict:
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
