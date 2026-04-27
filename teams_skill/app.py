"""Skill app — v3 spike. aiohttp host + AgentApplication as a CS-callable skill.

Run:
    python -m teams_skill.app

Env (read from process env or teams_skill/.env):
    SKILL_APP_ID         Azure Bot app reg id for THIS skill
    SKILL_APP_PASSWORD   client secret for SKILL_APP_ID
    SKILL_TENANT_ID      tenant id (SingleTenant)
    SKILL_PUBLIC_URL     https URL skill is reachable at (devtunnel or ACA)
    CS_PARENT_APP_ID     Copilot Studio agent's app reg id (allowed-callers ACL)
    PORT                 default 3979 (avoid clash with teams_agent on 3978)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from aiohttp import web

from . import manifest as manifest_mod


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("teams_skill.app")


SKILL_APP_ID = os.environ.get("SKILL_APP_ID", "")
SKILL_APP_PASSWORD = os.environ.get("SKILL_APP_PASSWORD", "")
SKILL_TENANT_ID = os.environ.get("SKILL_TENANT_ID", "")
SKILL_PUBLIC_URL = os.environ.get("SKILL_PUBLIC_URL", "http://localhost:3979")
CS_PARENT_APP_ID = os.environ.get("CS_PARENT_APP_ID", "")
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
        text = (context.activity.text or "").strip()
        _log.info("[skill] message from CS: %r", text)
        # Spike: just echo so we can confirm CS->skill round-trip works.
        await context.send_activity(f"[skill spike] got: {text}")

    @app.activity("event")
    async def _on_event(context, state):  # noqa: ANN001
        name = getattr(context.activity, "name", "")
        value = getattr(context.activity, "value", None)
        _log.info("[skill] event name=%s value=%s", name, value)
        if name == "endConversation":
            await context.send_activity("[skill spike] endConversation received — would start SN handoff here.")
        elif name == "sendMessage":
            txt = (value or {}).get("text", "") if isinstance(value, dict) else ""
            await context.send_activity(f"[skill spike] sendMessage received: {txt}")

    @app.activity("endOfConversation")
    async def _on_eoc(context, state):  # noqa: ANN001
        _log.info("[skill] endOfConversation — CS terminating skill session")

    return app, adapter


def _get_app_and_adapter():
    global _app, _adapter
    if _app is None:
        _app, _adapter = _build_app_and_adapter()
    return _app, _adapter


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

async def manifest(request: web.Request) -> web.Response:
    """Serve skill manifest. CS read this URL when adding skill."""
    body = manifest_mod.build_manifest(SKILL_PUBLIC_URL, SKILL_APP_ID)
    return web.Response(
        text=json.dumps(body, indent=2),
        content_type="application/json",
    )


async def messages(request: web.Request) -> web.Response:
    """Skill receiver. CS POST activity here."""
    if request.content_type and "application/json" not in request.content_type:
        return web.Response(status=415)

    raw = await request.read()
    auth_header = request.headers.get("Authorization", "")
    _log.info("[skill] inbound activity: auth_present=%s body_len=%d",
              bool(auth_header), len(raw))

    try:
        app_, adapter = _get_app_and_adapter()
    except Exception as exc:  # noqa: BLE001
        _log.exception("skill build failed: %s", exc)
        return web.json_response(
            {"error": "skill not configured", "detail": str(exc)},
            status=500,
        )

    try:
        # M365 Agents SDK aiohttp helper
        from microsoft_agents.hosting.aiohttp import start_agent_process  # type: ignore
        return await start_agent_process(request, app_, adapter)
    except Exception as exc:  # noqa: BLE001
        _log.exception("skill process failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "skill_app_id_set": bool(SKILL_APP_ID),
        "cs_parent_app_id_set": bool(CS_PARENT_APP_ID),
        "public_url": SKILL_PUBLIC_URL,
    })


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/skill-manifest.json", manifest)
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/api/messages", messages)
    return app


def main() -> None:
    web.run_app(make_app(), host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
