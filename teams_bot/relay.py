"""TeamsActivityHandler subclass that drives the Teams relay bot.

State machine mirrors the browser webchat:

    BOT     -- proxy turn to Copilot Studio over Direct Line and stream
               replies back into the Teams chat synchronously.
    QUEUED  -- canned "Connecting..." card; user input is suppressed.
    LIVE    -- forward user text to ServiceNow via the existing
               /api/servicenow/user-message route.
    CLOSED  -- canned "chat ended" card; "Start new chat" resets to BOT.

Every turn we re-fetch the bridge session by `bridge_session_id` (cached on
the per-user TeamsActivityHandler state via the conversation's user id), which
keeps the Teams client thin: ServiceNow webhooks fan out to this bot via the
bridge's existing `_push_to_user` dispatcher (see Phase 2).
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from botbuilder.core import TurnContext
from botbuilder.core.teams import TeamsActivityHandler
from botbuilder.schema import Activity, ActivityTypes, ChannelAccount

from . import config, directline, runtime


_log = logging.getLogger(__name__)


# Activity event names emitted by Copilot Studio's "Transfer conversation"
# (TransferConversationV2) action -- same as the web flow.
HANDOFF_EVENT_NAMES = {"handoff.initiate"}


class TeamsRelayBot(TeamsActivityHandler):
    """Bridges a Teams 1:1 chat to Copilot Studio + ServiceNow."""

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _channel_user_key(turn_context: TurnContext) -> str:
        """Stable per-Teams-user key used to look up / mint the bridge session.
        For 1:1 chats this is just the AAD user id.
        """
        act = turn_context.activity
        from_account: ChannelAccount = act.from_property
        # `aad_object_id` is the AAD user GUID; falls back to the channel user id.
        return getattr(from_account, "aad_object_id", None) or from_account.id

    @staticmethod
    def _user_email(turn_context: TurnContext) -> str | None:
        # Available on Teams personal-scope conversations once the SDK has
        # fetched the member info; for the first turn we fall back to None
        # and let the bridge resolve via SN_DEFAULT_USER_SYS_ID.
        act = turn_context.activity
        from_account = act.from_property or ChannelAccount()
        for attr in ("user_principal_name", "email", "name"):
            v = getattr(from_account, attr, None)
            if v and "@" in str(v):
                return str(v)
        return None

    @staticmethod
    def _display_name(turn_context: TurnContext) -> str | None:
        act = turn_context.activity
        from_account = act.from_property or ChannelAccount()
        return getattr(from_account, "name", None)

    def _ensure_session(self, turn_context: TurnContext) -> dict | None:
        """Return the bridge session dict (state + ids) for this Teams user.

        Uses the bridge's `/api/teams/init-session` endpoint, which is
        idempotent on `teams_user_key` -- repeated calls return the same sid.
        Persists the conversation reference so SN webhooks can push back via
        `continue_conversation`.
        """
        try:
            reference = TurnContext.get_conversation_reference(turn_context.activity)
            ref_dict = runtime.serialize_reference(reference)
            payload = {
                "teams_user_key": self._channel_user_key(turn_context),
                "user_email": self._user_email(turn_context),
                "user_display_name": self._display_name(turn_context),
                "conversation_reference": ref_dict,
            }
            r = requests.post(
                f"{config.BRIDGE_INTERNAL_URL}/api/teams/init-session",
                json=payload,
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            _log.exception("init-session failed")
            return None

    @staticmethod
    def _bridge_post(path: str, body: dict) -> dict | None:
        try:
            r = requests.post(f"{config.BRIDGE_INTERNAL_URL}{path}", json=body, timeout=20)
            r.raise_for_status()
            return r.json() if r.content else {}
        except Exception:  # noqa: BLE001
            _log.exception("bridge POST %s failed", path)
            return None

    # ---- main entry points ------------------------------------------------

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        text = (turn_context.activity.text or "").strip()
        if not text:
            return

        session = self._ensure_session(turn_context)
        if session is None:
            await turn_context.send_activity(
                "Sorry, I can't reach the bridge right now. Please try again in a moment."
            )
            return
        sid = session.get("session_id")
        state = session.get("state") or "bot"

        # Universal escape hatch: let the user bail out of any state (queued
        # waiting for an agent, an old live chat that the rep has long since
        # left, or a closed session) by typing a reset command. Without this
        # a user who escalated days ago is stuck talking to a dead live-chat.
        RESET_COMMANDS = {"new", "new chat", "start new chat", "reset", "start over", "restart"}
        if text.lower() in RESET_COMMANDS:
            self._bridge_post(
                "/api/teams/reset-session",
                {"teams_user_key": self._channel_user_key(turn_context)},
            )
            directline.reset(sid or "")
            await turn_context.send_activity(
                "Started a new chat. How can I help?"
            )
            return

        # Allow simple "reset" command from CLOSED to start over.
        if state == "closed":
            await turn_context.send_activity(
                "This chat has ended. Type **new** to start over."
            )
            return

        if state == "queued":
            await turn_context.send_activity(
                "Connecting an agent... please hold. I'll let you know as soon "
                "as someone joins. Type **new** to cancel and start over."
            )
            return

        if state == "live":
            self._bridge_post(
                "/api/servicenow/user-message",
                {"session_id": sid, "text": text},
            )
            return

        # state == "bot": proxy to Copilot Studio.
        await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        try:
            directline.post_user_text(sid, text)
            replies = directline.collect_bot_replies(sid)
        except Exception:  # noqa: BLE001
            _log.exception("Direct Line proxy failed")
            await turn_context.send_activity(
                "Sorry, I'm having trouble reaching my brain right now. Please try again."
            )
            return

        for act in replies:
            await self._dispatch_directline_activity(turn_context, sid, act)

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ) -> None:
        # Capture the conversation reference even on the install/welcome turn so
        # we can proactively push status messages later.
        self._ensure_session(turn_context)
        bot_id = turn_context.activity.recipient.id if turn_context.activity.recipient else None
        for m in members_added or []:
            if m.id == bot_id:
                continue
            await turn_context.send_activity(
                "Hi! I'm your IT helper. Ask me anything, or say *talk to a human* to "
                "be connected to a live agent."
            )

    # ---- helpers for handling CS replies -----------------------------------

    async def _dispatch_directline_activity(
        self, turn_context: TurnContext, sid: str | None, act: dict
    ) -> None:
        act_type = (act.get("type") or "").lower()
        if act_type == "event":
            name = (act.get("name") or "").lower()
            if name in HANDOFF_EVENT_NAMES:
                value = act.get("value") or {}
                opening = (
                    value.get("va_AgentMessage")
                    or value.get("va_LastPhrases")
                    or value.get("va_LastTopic")
                    or ""
                )
                # Tell the bridge to escalate. The bridge will push a `status`
                # event to us via continue_conversation, which is rendered by
                # the dispatcher in servicenow_bridge.py.
                self._bridge_post(
                    "/api/servicenow/escalate",
                    {"session_id": sid, "opening_message": opening},
                )
            return
        if act_type != "message":
            return
        text = act.get("text") or ""
        if text:
            await turn_context.send_activity(text)
        # Cards / suggested actions / attachments could be forwarded here in a
        # later phase; for the first cut we keep parity with the simple text
        # flow the web reference uses.
