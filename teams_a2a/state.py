"""In-memory handoff state for the v3 spike skill.

Tracks live ServiceNow handoff sessions keyed by Copilot Studio
conversation id, plus a reverse index keyed by the SN
sys_cs_conversation.sys_id so the inbound SN webhook can resolve
which CS conversation to push a rep reply into.

PRODUCTION NOTE: this is in-process state. With ACA `--max-replicas > 1`
or any restart, sessions are lost. Move to Redis/Cosmos before going
beyond a demo. The data shape is intentionally JSON-friendly so the
swap is mechanical.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ActiveHandoff:
    cs_conversation_id: str
    conversation_reference: dict
    sn_conversation_sys_id: str
    sn_user_sys_id: str
    sn_interaction_sys_id: str
    sn_interaction_number: str
    user_email: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    # Last few user texts we forwarded into SN. The SN business rule fires
    # on every sys_cs_message insert (including consumer-direction inserts
    # we just made) so the webhook would loop them back as if they were
    # rep replies. Drop on exact-text match. Mirrors the bridge pattern
    # (bridge/servicenow_bridge.py: BridgeSession.recent_user_texts).
    recent_user_texts: deque = field(default_factory=lambda: deque(maxlen=20))
    # A2A buffer: rep replies / status messages from SN webhook that
    # arrived between user turns. Drained on the next user message turn
    # and prepended to our reply, since CS A2A doesn't allow proactive
    # push back into a parent conversation.
    pending_replies: deque = field(default_factory=lambda: deque(maxlen=50))
    closed: bool = False
    # Saved service_url from the first inbound CS activity. CS A2A signs
    # the external-agent URL (`...?conversationId=...&sig=...`) so we can
    # POST proactive replies to it any time without auth headers. Without
    # this, rep replies only land when the user types again — feels broken.
    service_url: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


_by_cs: dict[str, ActiveHandoff] = {}
_by_sn: dict[str, str] = {}  # sn_conversation_sys_id -> cs_conversation_id
_by_interaction: dict[str, str] = {}  # sn_interaction_sys_id -> cs_conversation_id
_lock = threading.Lock()


def start(handoff: ActiveHandoff) -> None:
    with _lock:
        _by_cs[handoff.cs_conversation_id] = handoff
        if handoff.sn_conversation_sys_id:
            _by_sn[handoff.sn_conversation_sys_id] = handoff.cs_conversation_id
        if handoff.sn_interaction_sys_id:
            _by_interaction[handoff.sn_interaction_sys_id] = handoff.cs_conversation_id


def get(cs_conversation_id: str) -> Optional[ActiveHandoff]:
    with _lock:
        return _by_cs.get(cs_conversation_id)


def get_by_sn_conversation(sn_conversation_sys_id: str) -> Optional[ActiveHandoff]:
    with _lock:
        cs_id = _by_sn.get(sn_conversation_sys_id)
        return _by_cs.get(cs_id) if cs_id else None


def get_by_sn_interaction(sn_interaction_sys_id: str) -> Optional[ActiveHandoff]:
    with _lock:
        cs_id = _by_interaction.get(sn_interaction_sys_id)
        return _by_cs.get(cs_id) if cs_id else None


def end(cs_conversation_id: str) -> Optional[ActiveHandoff]:
    with _lock:
        h = _by_cs.pop(cs_conversation_id, None)
        if h:
            _by_sn.pop(h.sn_conversation_sys_id, None)
            _by_interaction.pop(h.sn_interaction_sys_id, None)
        return h
