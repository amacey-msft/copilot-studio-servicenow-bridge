"""Per-conversation state helpers.

Mirrors `ConversationStateManager` in the GenesysHandoff .NET sample. State
is persisted via the SDK's IStorage abstraction (MemoryStorage in dev,
swap for Cosmos/Blob in prod).
"""
from __future__ import annotations

from typing import Any, Optional


# Keys live on the conversation-scoped state bag the SDK provides per turn.
_K_CS_CONVO_ID = "cs.conversationId"
_K_CS_REFERENCE = "cs.reference"
_K_ESCALATED = "handoff.escalated"
_K_BRIDGE_SID = "bridge.sessionId"


class ConvState:
    """Thin wrapper over the SDK's per-conversation state dict."""

    def __init__(self, store: dict[str, Any]):
        self._s = store

    # ---- Copilot Studio conversation tracking --------------------------

    def get_cs_conversation_id(self) -> Optional[str]:
        return self._s.get(_K_CS_CONVO_ID)

    def set_cs_conversation_id(self, value: Optional[str]) -> None:
        if value:
            self._s[_K_CS_CONVO_ID] = value
        else:
            self._s.pop(_K_CS_CONVO_ID, None)

    def get_cs_reference(self) -> Optional[dict]:
        return self._s.get(_K_CS_REFERENCE)

    def set_cs_reference(self, value: Optional[dict]) -> None:
        if value:
            self._s[_K_CS_REFERENCE] = value
        else:
            self._s.pop(_K_CS_REFERENCE, None)

    # ---- Escalation flag -----------------------------------------------

    def is_escalated(self) -> bool:
        return bool(self._s.get(_K_ESCALATED))

    def set_escalated(self, value: bool) -> None:
        if value:
            self._s[_K_ESCALATED] = True
        else:
            self._s.pop(_K_ESCALATED, None)

    # ---- Bridge session id (so push-back can find this conversation) ---

    def get_bridge_session_id(self) -> Optional[str]:
        return self._s.get(_K_BRIDGE_SID)

    def set_bridge_session_id(self, value: Optional[str]) -> None:
        if value:
            self._s[_K_BRIDGE_SID] = value
        else:
            self._s.pop(_K_BRIDGE_SID, None)

    def clear_all(self) -> None:
        for k in (_K_CS_CONVO_ID, _K_CS_REFERENCE, _K_ESCALATED, _K_BRIDGE_SID):
            self._s.pop(k, None)
