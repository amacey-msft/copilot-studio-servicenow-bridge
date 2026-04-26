"""AgentApplication subclass — Genesys-style handoff for ServiceNow.

State machine mirrors `teams_bot/relay.py`:

    BOT     -> proxy turns to Copilot Studio via CopilotClient. Watch for
               an event named COPILOTSTUDIO_HANDOFF_EVENT_NAME (Stage 2:
               wired via a CS Event node) which flips us to LIVE.
    QUEUED  -> bridge has opened an SN interaction but no agent has
               accepted yet. Suppress user input with a polite message.
    LIVE    -> forward user text straight to ServiceNow via the bridge;
               do NOT call CS.
    CLOSED  -> tell user to type `new` to start over.

Reset commands (`-reset`, `new`, `start over`) clear local state AND tell
the bridge to drop its session.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from microsoft_agents.activity import ActivityTypes
from microsoft_agents.hosting.core import TurnContext

from . import bridge, config, cs_client
from .state import ConvState


_log = logging.getLogger(__name__)


RESET_COMMANDS = {"-reset", "new", "new chat", "start new chat", "reset", "start over", "restart"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channel_user_key(turn_context: TurnContext) -> str:
    act = turn_context.activity
    from_acc = getattr(act, "from_property", None) or getattr(act, "from_", None)
    if from_acc is None:
        return "unknown"
    return getattr(from_acc, "aad_object_id", None) or getattr(from_acc, "id", "unknown")


def _user_email(turn_context: TurnContext) -> Optional[str]:
    from_acc = getattr(turn_context.activity, "from_property", None) or getattr(
        turn_context.activity, "from_", None
    )
    if from_acc is None:
        return None
    for attr in ("user_principal_name", "email", "name"):
        v = getattr(from_acc, attr, None)
        if v and "@" in str(v):
            return str(v)
    return None


def _display_name(turn_context: TurnContext) -> Optional[str]:
    from_acc = getattr(turn_context.activity, "from_property", None) or getattr(
        turn_context.activity, "from_", None
    )
    return getattr(from_acc, "name", None) if from_acc else None


def _serialize_reference(turn_context: TurnContext) -> dict:
    """Best-effort ConversationReference serialization across SDK versions."""
    ref = TurnContext.get_conversation_reference(turn_context.activity)
    for fn in ("model_dump", "to_dict", "serialize", "dict"):
        m = getattr(ref, fn, None)
        if callable(m):
            try:
                return m()  # type: ignore[no-any-return]
            except Exception:  # noqa: BLE001
                continue
    return {}


# ---------------------------------------------------------------------------
# Per-turn entry point — called by app.py via AgentApplication routing
# ---------------------------------------------------------------------------

async def handle_turn(turn_context: TurnContext, conv_store: dict[str, Any]) -> None:
    """Single-route handler. `conv_store` is the per-conversation state dict
    obtained from the SDK's IStorage / state accessor in app.py."""
    state = ConvState(conv_store)
    text = (getattr(turn_context.activity, "text", "") or "").strip()
    act_type = (getattr(turn_context.activity, "type", "") or "").lower()

    # Always (re-)register this conversation with the bridge so SN webhooks
    # know where to push back to. Idempotent on teams_user_key.
    user_key = _channel_user_key(turn_context)
    session = await bridge.init_session(
        teams_user_key=user_key,
        user_email=_user_email(turn_context),
        user_display_name=_display_name(turn_context),
        conversation_reference=_serialize_reference(turn_context),
    )
    if not session:
        if act_type == ActivityTypes.message:
            await turn_context.send_activity(
                "Sorry, I can't reach the bridge right now. Please try again."
            )
        return

    sid = session.get("session_id")
    bridge_state = (session.get("state") or "bot").lower()
    state.set_bridge_session_id(sid)

    # Reset commands work in any state.
    if act_type == ActivityTypes.message and text.lower() in RESET_COMMANDS:
        await bridge.reset_session(user_key)
        state.clear_all()
        await turn_context.send_activity("Started a new chat. How can I help?")
        return

    # State branches (single source of truth = the bridge).
    if bridge_state == "closed":
        if act_type == ActivityTypes.message:
            await turn_context.send_activity("This chat has ended. Type **new** to start over.")
        return

    if bridge_state == "queued":
        if act_type == ActivityTypes.message:
            await turn_context.send_activity(
                "Connecting an agent... please hold. Type **new** to cancel."
            )
        return

    if bridge_state == "live":
        if act_type == ActivityTypes.message and text:
            await bridge.send_user_message(sid, text)
        return

    # bridge_state == "bot": proxy to Copilot Studio via CopilotClient.
    if act_type != ActivityTypes.message or not text:
        return

    await _proxy_to_copilot_studio(turn_context, state, sid, text)


async def _proxy_to_copilot_studio(
    turn_context: TurnContext, state: ConvState, bridge_sid: Optional[str], text: str
) -> None:
    token = cs_client.get_token_from_turn(turn_context, None)
    if not token:
        # Without an OBO token we can't talk to CS. Surface a helpful error
        # rather than silently swallowing the turn.
        await turn_context.send_activity(
            "Sign-in required to talk to the assistant. Please complete the "
            "sign-in prompt and try again."
        )
        return

    client = cs_client.make_client(token)
    cs_convo_id = state.get_cs_conversation_id()

    try:
        if not cs_convo_id:
            # Start a new CS conversation for this user.
            async for activity in client.start_conversation(emit_start_conversation_event=True):
                await _dispatch_cs_activity(turn_context, state, bridge_sid, activity)
                if getattr(activity, "conversation", None) and getattr(activity.conversation, "id", None):
                    state.set_cs_conversation_id(activity.conversation.id)
            cs_convo_id = state.get_cs_conversation_id()

        # Forward this user turn.
        if cs_convo_id:
            async for activity in client.ask_question(text, cs_convo_id):
                await _dispatch_cs_activity(turn_context, state, bridge_sid, activity)
    except Exception:  # noqa: BLE001
        _log.exception("CopilotClient proxy failed")
        await turn_context.send_activity(
            "Sorry, I'm having trouble reaching my brain right now. Please try again."
        )


async def _dispatch_cs_activity(
    turn_context: TurnContext,
    state: ConvState,
    bridge_sid: Optional[str],
    activity: Any,
) -> None:
    a_type = getattr(activity, "type", None)
    a_name = (getattr(activity, "name", "") or "")

    if a_type == ActivityTypes.message:
        text_out = getattr(activity, "text", "") or ""
        if text_out:
            await turn_context.send_activity(text_out)
        return

    if a_type == ActivityTypes.event and a_name == config.COPILOTSTUDIO_HANDOFF_EVENT_NAME:
        # Genesys-style escalation hook (Stage 2 — only fires if you've
        # added an Event node to the CS Escalate topic). For Stage 1 this
        # is a no-op when the legacy HTTP-action path is still wired.
        _log.info("CS handoff event received; triggering bridge escalation")
        summary = ""
        val = getattr(activity, "value", None)
        if isinstance(val, str):
            summary = val
        elif val is not None:
            summary = str(val)
        if bridge_sid:
            await bridge.trigger_escalation(bridge_sid, summary=summary or None)
        state.set_escalated(True)
        return

    if a_type == ActivityTypes.end_of_conversation:
        state.set_cs_conversation_id(None)
        return
