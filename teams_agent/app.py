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
# Adapter + AgentApplication wiring  -- lazy so import doesn't fail w/o env
# ---------------------------------------------------------------------------

_adapter = None
_app = None  # AgentApplication


def _build_app_and_adapter():
    """Wire the SDK with a single SERVICE_CONNECTION (Azure Bot app reg).

    Direct-Line parity: no Authorization / AuthHandler / OBO. CS is
    reached via the bridge's `/directline/token` endpoint using the
    shared DL secret, exactly like the legacy `teams_bot/`.
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
        client_id=config.AZURE_BOT_APP_ID,
        client_secret=config.AZURE_BOT_APP_PASSWORD,
        tenant_id=config.AZURE_BOT_TENANT_ID,
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
            bot_app_id=config.AZURE_BOT_APP_ID,
            storage=storage,
        ),
        connection_manager=cm,
    )

    import re

    @app.activity("message")
    async def _on_message(context, state):  # noqa: ANN001
        conv_id = getattr(context.activity.conversation, "id", "") or "default"
        store = await _get_conv_store(conv_id)
        await agent.handle_turn(context, store)

    @app.activity("event")
    async def _on_event(context, state):  # noqa: ANN001
        conv_id = getattr(context.activity.conversation, "id", "") or "default"
        store = await _get_conv_store(conv_id)
        await agent.handle_turn(context, store)

    return app, adapter


def _get_app_and_adapter():
    global _adapter, _app
    if _app is None:
        _app, _adapter = _build_app_and_adapter()
    return _app, _adapter


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

async def messages(request: web.Request) -> web.Response:
    """Inbound activity from Teams via the Bot Framework channel."""
    if request.content_type != "application/json":
        return web.Response(status=415)

    app, adapter = _get_app_and_adapter()
    response = await adapter.process(request, app)
    return response if response is not None else web.Response(status=200)


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

    _log.info(
        "teams_push ref_keys=%s bot=%s service_url=%s payload_type=%s",
        list(ref.keys()), ref.get("bot"), ref.get("service_url") or ref.get("serviceUrl"), payload.get("type"),
    )

    from microsoft_agents.activity import ConversationReference  # type: ignore

    reference = (
        ConversationReference().deserialize(ref)
        if hasattr(ConversationReference, "deserialize")
        else ConversationReference(**ref)
    )
    _log.info(
        "teams_push deserialized bot=%s recipient=%s service_url=%s conv=%s",
        getattr(reference, "bot", None),
        getattr(reference, "user", None),
        getattr(reference, "service_url", None) or getattr(reference, "serviceUrl", None),
        getattr(reference, "conversation", None),
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

    adapter = _get_app_and_adapter()[1]
    try:
        continuation = (
            reference.get_continuation_activity()
            if hasattr(reference, "get_continuation_activity")
            else None
        )
        if continuation is None:
            return web.json_response({"error": "bad conversation_reference"}, status=400)
        await adapter.continue_conversation(
            config.AZURE_BOT_APP_ID, continuation, _logic
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
    from microsoft_agents.hosting.aiohttp import jwt_authorization_decorator  # type: ignore
    from microsoft_agents.hosting.core import AgentAuthConfiguration  # type: ignore
    from microsoft_agents.hosting.core.authorization.auth_types import AuthTypes  # type: ignore

    # Auth config used by the JWT decorator on /api/messages to validate inbound BF tokens.
    # The decorator extracts the JWT from `Authorization`, verifies it, and
    # attaches a populated ClaimsIdentity (with `aud`/`appid`) to the request.
    agent_cfg = AgentAuthConfiguration(
        auth_type=AuthTypes.client_secret,
        client_id=config.AZURE_BOT_APP_ID,
        client_secret=config.AZURE_BOT_APP_PASSWORD,
        tenant_id=config.AZURE_BOT_TENANT_ID,
    )
    # Required by `_jwt_patch_is_valid_aud` in the validator.
    agent_cfg._connections = {"SERVICE_CONNECTION": agent_cfg}

    app = web.Application()
    app["agent_configuration"] = agent_cfg
    app.router.add_post("/api/messages", jwt_authorization_decorator(messages))
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
