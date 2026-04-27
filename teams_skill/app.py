"""A2A agent app — v3 spike.

Exposes /api/messages as a Microsoft 365 Agents SDK agent that the
Copilot Studio orchestrator calls via the "Add an agent → Microsoft 365
Agents SDK" (A2A) connection. NOT a skill — there is no skill manifest
and no callback to pvaruntime/skillsV2 (the Entra-Agent-ID identity
model makes those callbacks impossible to authenticate).

Flow:
  - First message in a fresh CS conversation -> open a SN chat session.
  - Subsequent messages -> relay to the SN live agent.
  - SN webhook -> buffer rep replies in state; deliver on next user turn
    (synchronous A2A response). No proactive push.

Run:
    python -m teams_skill.app

Env (read from process env or teams_skill/.env):
    SKILL_APP_ID         Azure Bot app reg id for THIS agent
    SKILL_APP_PASSWORD   client secret for SKILL_APP_ID
    SKILL_TENANT_ID      tenant id (SingleTenant)
    SKILL_PUBLIC_URL     https URL the agent is reachable at
    PORT                 default 3979
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from aiohttp import web


def _patch_mcs_connector() -> None:
    """Patch SDK so empty 200 ack from CS A2A endpoint doesn't crash.

    M365 Agents SDK 0.9.x calls `response.json()` on the CS reply. CS A2A
    `/external-agent` endpoint acks with HTTP 200 and an empty body
    (no JSON), which raises ContentTypeError. The reply DID land — the
    crash is just over-eager parsing. Replace `send_to_conversation`
    with a tolerant version.
    """
    try:
        from microsoft_agents.hosting.core.connector.mcs import (  # type: ignore
            mcs_connector_client as m,
        )
        from microsoft_agents.activity import ResourceResponse  # type: ignore
    except Exception:  # noqa: BLE001
        return

    if getattr(m.MCSConversations.send_to_conversation, "_a2a_patched", False):
        return

    async def send_to_conversation(self, conversation_id, activity, **kwargs):  # noqa: ANN001
        if activity is None:
            raise ValueError("activity is required")
        async with self._client.post(
            self._endpoint,
            json=activity.model_dump(
                by_alias=True, exclude_unset=True, mode="json"
            ),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        ) as response:
            if response.status >= 300:
                response.raise_for_status()
            try:
                data = await response.json(content_type=None)
            except Exception:  # noqa: BLE001
                data = None
            if not isinstance(data, dict):
                data = {}
            return ResourceResponse.model_validate(data)

    send_to_conversation._a2a_patched = True  # type: ignore[attr-defined]
    m.MCSConversations.send_to_conversation = send_to_conversation


_patch_mcs_connector()


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("teams_skill.app")


SKILL_APP_ID = os.environ.get("SKILL_APP_ID", "")
SKILL_APP_PASSWORD = os.environ.get("SKILL_APP_PASSWORD", "")
SKILL_TENANT_ID = os.environ.get("SKILL_TENANT_ID", "")
SKILL_PUBLIC_URL = os.environ.get("SKILL_PUBLIC_URL", "http://localhost:3979")
# Shared secret for the inbound SN BR webhook -> /api/sn-webhook.
SN_WEBHOOK_SECRET = os.environ.get("SN_WEBHOOK_SECRET", "")
PORT = int(os.environ.get("PORT", "3979"))


_app = None
_adapter = None


def _build_app_and_adapter():
    """Wire AgentApplication as a skill (callee).

    Skill differ from regular bot in two way:
      - allowed-callers ACL must include CS agent app id
      - on EndOfConversation activity, skill should clean up session

    For spike we just prove SDK accept the wiring without crash.
    """
    from microsoft_agents.hosting.aiohttp import CloudAdapter  # type: ignore
    from microsoft_agents.hosting.core import (  # type: ignore
        AgentAuthConfiguration,
        MemoryStorage,
    )
    from microsoft_agents.hosting.core.authorization.auth_types import AuthTypes  # type: ignore
    from microsoft_agents.authentication.msal import MsalConnectionManager  # type: ignore
    from microsoft_agents.hosting.core.app import AgentApplication, ApplicationOptions  # type: ignore

    service_cfg = AgentAuthConfiguration(
        auth_type=AuthTypes.client_secret,
        client_id=SKILL_APP_ID,
        client_secret=SKILL_APP_PASSWORD,
        tenant_id=SKILL_TENANT_ID,
        connection_name="SERVICE_CONNECTION",
    )
    cm = MsalConnectionManager(
        connections_configurations={"SERVICE_CONNECTION": service_cfg}
    )

    storage = MemoryStorage()
    adapter = CloudAdapter(connection_manager=cm)

    app = AgentApplication(
        options=ApplicationOptions(
            adapter=adapter,
            bot_app_id=SKILL_APP_ID,
            storage=storage,
        ),
        connection_manager=cm,
    )

    @app.activity("message")
    async def _on_message(context, state):  # noqa: ANN001
        """A2A turn handler.

        First message in a fresh CS conversation -> open SN chat session
        (the user message becomes the initial query, plus any
        ``userEmail``/``initialQuery`` slots passed via channel data).
        Subsequent messages -> relay to SN, drain any buffered rep
        replies, and reply synchronously.
        """
        from . import state as handoff_state
        from . import sn_client

        text = (context.activity.text or "").strip()
        convo_id = getattr(context.activity.conversation, "id", "") or ""
        active = handoff_state.get(convo_id)

        # Pull optional slots from channelData (CS topic can pass them
        # via a SetVariable -> ChannelData binding before invoking us).
        cd = getattr(context.activity, "channel_data", None) or {}
        if not isinstance(cd, dict):
            cd = {}
        user_email = (cd.get("userEmail") or "").strip() or None
        initial_query_slot = (cd.get("initialQuery") or "").strip()

        # ---------- Path 1: no active handoff -> open one --------------
        if active is None:
            if active and active.closed:
                handoff_state.end(convo_id)  # safety
            initial = initial_query_slot or text or "(no message)"
            try:
                result = sn_client.open_chat(
                    bridge_session_id=convo_id[:64] or "a2a-spike",
                    user_email=user_email,
                    initial_query=initial,
                )
            except Exception as exc:  # noqa: BLE001
                _log.exception("[a2a] SN open_chat failed")
                await context.send_activity(
                    f"Sorry — I couldn't reach the live-agent queue: {exc}"
                )
                return
            number = result.get("interaction_number") or "(no number)"
            handoff = handoff_state.ActiveHandoff(
                cs_conversation_id=convo_id,
                conversation_reference=_serialize_reference(context),
                sn_conversation_sys_id=result.get("conversation_sys_id") or "",
                sn_user_sys_id=result.get("sn_user_sys_id") or "",
                sn_interaction_sys_id=result.get("interaction_sys_id") or "",
                sn_interaction_number=number,
                user_email=user_email,
                service_url=getattr(context.activity, "service_url", "") or "",
            )
            if initial:
                handoff.recent_user_texts.append(initial)
            handoff_state.start(handoff)
            _log.info("[a2a] opened SN chat interaction=%s convo=%s",
                      number, handoff.sn_conversation_sys_id)
            await context.send_activity(
                f"Connected you to a live agent — your ticket is **{number}**. "
                "Hold on while a person picks up; anything you type next goes "
                "straight to them."
            )
            return

        # ---------- Path 2: active handoff -> relay + drain ------------
        if text:
            with active.lock:
                active.recent_user_texts.append(text)
            try:
                sn_client.send_user_message(
                    conversation_sys_id=active.sn_conversation_sys_id,
                    sn_user_sys_id=active.sn_user_sys_id,
                    text=text,
                )
                _log.info("[a2a] relayed user->SN convo=%s text=%r",
                          active.sn_conversation_sys_id, text[:80])
            except Exception:  # noqa: BLE001
                _log.exception("[a2a] relay user->SN failed")
                await context.send_activity(
                    "Sorry — I couldn't pass that to the live agent. Try again?"
                )
                return

        # Drain pending rep replies that arrived between turns.
        # Refresh the saved service_url in case the first turn missed it
        # or CS rotated the signed URL.
        with active.lock:
            su = getattr(context.activity, "service_url", "") or ""
            if su:
                active.service_url = su
        drained: list[str] = []
        with active.lock:
            while active.pending_replies:
                drained.append(active.pending_replies.popleft())
            closed = active.closed
        for line in drained:
            await context.send_activity(line)
        if closed:
            handoff_state.end(convo_id)
            await context.send_activity(
                "The live agent has ended this chat. Returning you to the assistant."
            )
            return
        # Note: we deliberately do NOT send a "(waiting…)" filler here.
        # Rep replies are pushed proactively via /api/sn-webhook ->
        # _push_to_cs(), so the user sees them as they arrive instead of
        # only on the next user turn. Sending an extra activity here just
        # adds noise.

    return app, adapter


def _get_app_and_adapter():
    global _app, _adapter
    if _app is None:
        _app, _adapter = _build_app_and_adapter()
    return _app, _adapter


# ---------------------------------------------------------------------------
# ConversationReference helpers + EOC sender
# ---------------------------------------------------------------------------

def _serialize_reference(turn_context) -> dict:  # noqa: ANN001
    """Best-effort serialize the turn's ConversationReference to a JSON dict.

    We need this stored so /api/sn-webhook can later push a rep reply back
    via adapter.continue_conversation, even though that POST has no
    TurnContext of its own.
    """
    act = turn_context.activity
    ref = None
    getter = getattr(act, "get_conversation_reference", None)
    if callable(getter):
        try:
            ref = getter()
        except Exception:  # noqa: BLE001
            ref = None
    if ref is None:
        try:
            from microsoft_agents.hosting.core import TurnContext  # type: ignore
            getter = getattr(TurnContext, "get_conversation_reference", None)
            if callable(getter):
                ref = getter(act)
        except Exception:  # noqa: BLE001
            ref = None
    if ref is None:
        return {}
    for fn in ("model_dump", "to_dict", "serialize", "dict"):
        m = getattr(ref, fn, None)
        if callable(m):
            try:
                return m()  # type: ignore[no-any-return]
            except Exception:  # noqa: BLE001
                continue
    return {}


async def _send_eoc(turn_context) -> None:  # noqa: ANN001
    """Emit an EndOfConversation activity (kept for completeness; A2A
    pattern doesn't strictly require it, but harmless if CS sends one)."""
    try:
        from microsoft_agents.activity import Activity, ActivityTypes  # type: ignore
        eoc = Activity(type=ActivityTypes.end_of_conversation, code="completedSuccessfully")
        await turn_context.send_activity(eoc)
    except Exception:  # noqa: BLE001
        _log.exception("[a2a] failed to send endOfConversation")


async def _push_to_cs(service_url: str, text: str) -> bool:
    """Proactively send a message back to a CS A2A conversation.

    The CS A2A `external-agent` service_url is HMAC-signed by CS
    (`?...&sig=...`), so we can POST to it directly without a token.
    This mirrors what `MCSConversations.send_to_conversation` does
    inside the SDK during a normal turn, but here we're called from
    the SN webhook handler outside any TurnContext.

    Returns True on HTTP 2xx, False otherwise.
    """
    import aiohttp  # type: ignore

    activity = {
        "type": "message",
        "text": text,
        "textFormat": "markdown",
    }
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(
                service_url,
                json=activity,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            ) as resp:
                ok = 200 <= resp.status < 300
                if not ok:
                    body = (await resp.text())[:200]
                    _log.warning(
                        "[a2a] proactive push HTTP %s body=%r url=%s",
                        resp.status, body, service_url[:80],
                    )
                else:
                    _log.info(
                        "[a2a] proactive push OK status=%s text=%r",
                        resp.status, text[:80],
                    )
                return ok
    except Exception:  # noqa: BLE001
        _log.exception("[a2a] proactive push exception url=%s", service_url[:80])
        return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

async def messages(request: web.Request) -> web.Response:
    """A2A receiver. CS POST activity here."""
    if request.content_type and "application/json" not in request.content_type:
        return web.Response(status=415)

    raw = await request.read()

    # DIAG: dump every header (redact long values) so we can see what CS sent.
    hdrs = []
    for k, v in request.headers.items():
        if k.lower() in ("cookie", "set-cookie"):
            v = "<redacted>"
        elif len(v) > 80:
            v = v[:40] + "..." + v[-20:] + f" (len={len(v)})"
        hdrs.append(f"{k}={v}")
    _log.info("[a2a] inbound headers: %s", " | ".join(hdrs))

    expected_key = os.environ.get("CS_API_KEY", "")
    presented = ""
    matched_header = ""
    if expected_key:
        for k, v in request.headers.items():
            if v == expected_key or v == f"Bearer {expected_key}":
                presented = v
                matched_header = k
                break
    _log.info("[a2a] api-key check: expected_set=%s matched_header=%s",
              bool(expected_key), matched_header or "(none)")

    if expected_key and not presented:
        return web.Response(status=401, headers={"WWW-Authenticate": "ApiKey"})

    try:
        app_, adapter = _get_app_and_adapter()
    except Exception as exc:  # noqa: BLE001
        _log.exception("a2a build failed: %s", exc)
        return web.json_response(
            {"error": "agent not configured", "detail": str(exc)},
            status=500,
        )

    try:
        from microsoft_agents.hosting.aiohttp import start_agent_process  # type: ignore
        return await start_agent_process(request, app_, adapter)
    except Exception as exc:  # noqa: BLE001
        _log.exception("a2a process failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "mode": "a2a",
        "skill_app_id_set": bool(SKILL_APP_ID),
        "sn_webhook_secret_set": bool(SN_WEBHOOK_SECRET),
        "public_url": SKILL_PUBLIC_URL,
    })


async def sn_webhook(request: web.Request) -> web.Response:
    """Receive rep replies / status from a ServiceNow Business Rule.

    Body (set by SN BR — same shape the legacy bridge accepts):
        {
            "interaction_sys_id": "...",
            "conversation_sys_id": "...",   # preferred lookup key
            "bridge_session_id": "...",     # fallback (= CS conversation id)
            "rep_name": "Jane Doe",
            "text": "<reply>",
            "event": "reply" | "claimed" | "closed" | "typing"
        }

    Auth: shared secret in `X-Bridge-Secret` header. SN_WEBHOOK_SECRET
    must be set in env or this endpoint refuses everything.
    """
    if not SN_WEBHOOK_SECRET:
        return web.json_response({"error": "webhook disabled"}, status=503)
    secret = request.headers.get("X-Bridge-Secret") or request.query.get("secret") or ""
    if secret != SN_WEBHOOK_SECRET:
        return web.json_response({"error": "forbidden"}, status=403)

    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "bad json"}, status=400)

    from . import state as handoff_state

    sn_convo_id = (data.get("conversation_sys_id") or "").strip()
    sn_interaction_id = (data.get("interaction_sys_id") or "").strip()
    bridge_sid = (data.get("bridge_session_id") or "").strip()

    handoff = None
    if sn_convo_id:
        handoff = handoff_state.get_by_sn_conversation(sn_convo_id)
    if handoff is None and sn_interaction_id:
        handoff = handoff_state.get_by_sn_interaction(sn_interaction_id)
    if handoff is None and bridge_sid:
        handoff = handoff_state.get(bridge_sid)
    if handoff is None:
        # Most likely: webhook fired for a conversation owned by the legacy
        # bridge, not the skill. Acknowledge cleanly so SN doesn't retry.
        _log.info("[skill] webhook: no active handoff for sn_convo=%s sn_int=%s sid=%s",
                  sn_convo_id, sn_interaction_id, bridge_sid)
        return web.json_response({"ok": True, "matched": False})

    event = (data.get("event") or "reply").lower()
    text = (data.get("text") or "").strip()
    rep_name = (data.get("rep_name") or "Support Agent").strip()

    # Build the message we'll push into CS (or a status/typing line).
    push_text: str | None = None
    if event == "claimed":
        push_text = f"You're now chatting with **{rep_name}**."
    elif event == "closed":
        push_text = "The live agent has ended this chat."
        with handoff.lock:
            handoff.closed = True
    elif event == "typing":
        return web.json_response({"ok": True, "ignored": "typing"})
    else:
        if not text:
            return web.json_response({"ok": True, "ignored": "empty"})
        # Echo-suppression: drop our own user-text re-delivery.
        with handoff.lock:
            try:
                handoff.recent_user_texts.remove(text)
                _log.info("[a2a] webhook: dropping user echo %r", text[:80])
                return web.json_response({"ok": True, "dropped": "echo"})
            except ValueError:
                pass
        push_text = f"**{rep_name}:** {text}"

    # Try proactive push to CS first. CS A2A external-agent URL is
    # signed (auth-by-URL), so a plain JSON POST works — no token. If
    # it fails, fall back to buffering for the next user turn.
    pushed = False
    if push_text and handoff.service_url:
        try:
            pushed = await _push_to_cs(handoff.service_url, push_text)
        except Exception:  # noqa: BLE001
            _log.exception("[a2a] proactive push to CS failed")
            pushed = False

    if push_text and not pushed:
        with handoff.lock:
            handoff.pending_replies.append(push_text)
        _log.info("[a2a] buffered rep reply (event=%s) for cs_convo=%s",
                  event, handoff.cs_conversation_id)
        return web.json_response({"ok": True, "buffered": True})

    return web.json_response({"ok": True, "pushed": pushed})


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/api/messages", messages)
    app.router.add_post("/api/sn-webhook", sn_webhook)
    return app


def main() -> None:
    web.run_app(make_app(), host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
