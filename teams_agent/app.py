"""aiohttp host for the M365 Agents SDK Teams agent.

Routes:
    POST /api/messages      -- Bot Framework inbound from Teams (auth-validated)
    POST /api/teams/push    -- proactive push from the bridge (shared-secret)
    GET  /healthz           -- liveness

Run locally:
    python -m teams_agent.app
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from aiohttp import web

from . import agent, config


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("teams_agent.app")


# ---------------------------------------------------------------------------
# In-memory conversation state (Stage 1 only — swap for CosmosDB in prod)
# ---------------------------------------------------------------------------

_conv_store_lock = asyncio.Lock()
_conv_store: dict[str, dict[str, Any]] = {}


async def _get_conv_store(conversation_id: str) -> dict[str, Any]:
    async with _conv_store_lock:
        store = _conv_store.setdefault(conversation_id, {})
    return store


# ---------------------------------------------------------------------------
# Adapter wiring  --  intentionally lazy so import doesn't fail without env
# ---------------------------------------------------------------------------

_adapter = None


def _build_adapter():
    """Construct the SDK CloudAdapter on first request.

    Kept loose against 0.9.x because the exact factory class moved
    between preview releases. Replace with the canonical
    `CloudAdapter.from_configuration(...)` once locked.
    """
    from microsoft_agents.hosting.aiohttp import CloudAdapter  # type: ignore

    # The adapter consumes a config object exposing `APP_ID`, `APP_PASSWORD`,
    # `APP_TYPE`, `APP_TENANTID` — same shape teams_bot uses.
    class _Cfg:
        APP_ID = config.AZURE_BOT_APP_ID
        APP_PASSWORD = config.AZURE_BOT_APP_PASSWORD
        APP_TYPE = config.AZURE_BOT_APP_TYPE
        APP_TENANTID = config.AZURE_BOT_TENANT_ID

        def get(self, k: str, default: Any = None) -> Any:
            return getattr(self, k, default)

    return CloudAdapter(_Cfg())


def _get_adapter():
    global _adapter
    if _adapter is None:
        _adapter = _build_adapter()
    return _adapter


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

async def messages(request: web.Request) -> web.Response:
    """Inbound activity from Teams via the Bot Framework channel."""
    if request.content_type != "application/json":
        return web.Response(status=415)

    body = await request.json()
    auth_header = request.headers.get("Authorization", "")

    from microsoft_agents.activity import Activity  # type: ignore

    activity = Activity().deserialize(body) if hasattr(Activity, "deserialize") else Activity(**body)

    async def _logic(turn_context):
        conv_id = getattr(turn_context.activity.conversation, "id", "") or "default"
        store = await _get_conv_store(conv_id)
        await agent.handle_turn(turn_context, store)

    adapter = _get_adapter()
    invoke_response = await adapter.process_activity(auth_header, activity, _logic)
    if invoke_response is not None:
        return web.json_response(
            getattr(invoke_response, "body", invoke_response),
            status=getattr(invoke_response, "status", 200),
        )
    return web.Response(status=200)


async def teams_push(request: web.Request) -> web.Response:
    """Proactive push from the Flask bridge.

    Body: `{conversation_reference: {...}, payload: {...}}`
    where payload mirrors what `teams_bot/push.py` already accepts:
        {"type": "status"|"message"|"typing", ...}
    """
    secret = request.headers.get("X-Bridge-Secret", "")
    if not config.PUSH_SHARED_SECRET or secret != config.PUSH_SHARED_SECRET:
        return web.json_response({"error": "unauthorized"}, status=401)

    body = await request.json()
    ref = body.get("conversation_reference") or {}
    payload = body.get("payload") or {}
    if not ref:
        return web.json_response({"error": "missing conversation_reference"}, status=400)

    from microsoft_agents.activity import ConversationReference  # type: ignore

    reference = (
        ConversationReference().deserialize(ref)
        if hasattr(ConversationReference, "deserialize")
        else ConversationReference(**ref)
    )

    async def _logic(turn_context):
        evt = (payload.get("type") or "").lower()
        if evt == "message":
            text = payload.get("text") or ""
            rep = payload.get("rep_name") or "Support Agent"
            if text:
                await turn_context.send_activity(f"**{rep}:** {text}")
        elif evt == "status":
            state_name = (payload.get("state") or "").lower()
            if state_name == "queued":
                await turn_context.send_activity(
                    "Connecting an agent... I'll let you know when someone joins."
                )
            elif state_name == "live":
                rep = payload.get("rep_name") or "Support Agent"
                await turn_context.send_activity(f"You're now chatting with **{rep}**.")
            elif state_name == "closed":
                await turn_context.send_activity(
                    "This chat has ended. Type **new** to start a new conversation."
                )
        # typing/unknown -> drop silently for now

    adapter = _get_adapter()
    try:
        await adapter.continue_conversation(
            reference, _logic, bot_app_id=config.AZURE_BOT_APP_ID
        )
        return web.json_response({"ok": True})
    except Exception as exc:  # noqa: BLE001
        _log.exception("teams_push failed")
        return web.json_response({"error": str(exc)}, status=500)


async def healthz(_: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "configured": config.is_configured(),
            "bridge_url": config.BRIDGE_INTERNAL_URL,
        }
    )


# ---------------------------------------------------------------------------
# App factory + entrypoint
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api/messages", messages)
    app.router.add_post("/api/teams/push", teams_push)
    app.router.add_get("/healthz", healthz)
    return app


def main() -> None:
    if not config.is_configured():
        _log.warning(
            "teams_agent starting WITHOUT full configuration. /healthz will report "
            "configured=false. Set AZURE_BOT_APP_ID / AZURE_BOT_TENANT_ID / "
            "COPILOTSTUDIO_ENVIRONMENT_ID / COPILOTSTUDIO_SCHEMA_NAME."
        )
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
