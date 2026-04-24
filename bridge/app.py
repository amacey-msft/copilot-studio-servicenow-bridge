"""Minimal Flask host for the ServiceNow handoff bridge.

This is a reference entry point. It registers the bridge blueprint, the
WebSocket route, serves the reference intranet page, and provides a stub
for the Direct Line token endpoint.

For production use see ../docs/09-production-hardening.md.

Run locally:
    python -m venv .venv
    .venv\\Scripts\\Activate.ps1     # PowerShell
    pip install -r requirements.txt
    cp .env.sample .env  # then edit
    python app.py

Or via Docker:
    docker compose up --build
"""
from __future__ import annotations

import os
import pathlib

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_sock import Sock

from servicenow_bridge import bp as servicenow_bp, register_websocket


WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"


def create_app() -> Flask:
    app = Flask(__name__)
    sock = Sock(app)

    # The bridge: HTTP routes + WebSocket route.
    app.register_blueprint(servicenow_bp)
    register_websocket(sock)

    @app.get("/")
    def _index():
        # Serves the reference intranet page from ../web/intranet.html so a
        # single `docker compose up` produces a working demo.
        resp = send_from_directory(WEB_DIR, "intranet.html")
        # The reference page is hot-reloaded during development. Disable
        # caching so users always get the latest JS without a hard refresh.
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    @app.get("/healthz")
    def _healthz():
        return jsonify(status="ok")

    @app.post("/directline/token")
    def _directline_token():
        """Mint a Direct Line token for the reference intranet page.

        Two supported modes (in order of preference):

        1. ``DIRECTLINE_TOKEN_ENDPOINT`` -- the Copilot Studio "Token
           endpoint" URL (Channels -> Custom website). The bridge GETs it
           and returns the JSON straight through. The browser POSTs us a
           ``{"user_id": "..."}`` body (the bridge session id) which we
           append as ``?userId=...`` so Copilot Studio binds it to the
           Direct Line conversation -- the agent then sees
           ``System.Activity.From.Id == <bridge session id>`` and can pass
           it back to the bridge HTTP tools as a stable correlation key.
           This is the recommended path because it requires no shared
           secret in the bridge.

        2. ``DIRECTLINE_SECRET`` -- a Direct Line channel secret from the
           Azure Bot resource. The bridge POSTs to
           ``https://directline.botframework.com/v3/directline/tokens/generate``
           with ``Authorization: Bearer <secret>`` and a ``{"user": {"id": ...}}``
           body so the same correlation key is bound to the conversation.

        Returns ``{"token": "...", "conversationId": "...", "expires_in": 3600}``
        for BotFramework WebChat.
        """
        body      = request.get_json(silent=True) or {}
        user_id   = str(body.get("user_id") or "").strip()
        token_url = os.environ.get("DIRECTLINE_TOKEN_ENDPOINT", "").strip()
        secret    = os.environ.get("DIRECTLINE_SECRET", "").strip()

        # 1. Copilot Studio token endpoint (GET, with userId query param).
        if token_url:
            url = token_url
            if user_id:
                from urllib.parse import quote
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}userId={quote(user_id)}"
            try:
                r = requests.get(url, timeout=15)
                if r.ok:
                    try:
                        return jsonify(r.json())
                    except ValueError:
                        upstream_detail = r.text[:500]
                        upstream_status = r.status_code
                else:
                    upstream_detail = r.text[:500]
                    upstream_status = r.status_code
            except requests.RequestException as exc:
                upstream_detail = str(exc)
                upstream_status = None
        else:
            upstream_detail = None
            upstream_status = None

        # 2. Fallback: mint a token from the Direct Line channel secret.
        if secret:
            payload = {"user": {"id": user_id}} if user_id else None
            try:
                r = requests.post(
                    "https://directline.botframework.com/v3/directline/tokens/generate",
                    headers={"Authorization": f"Bearer {secret}"},
                    json=payload,
                    timeout=15,
                )
            except requests.RequestException as exc:
                return jsonify(error="upstream_unreachable", detail=str(exc)), 502
            if not r.ok:
                return (
                    jsonify(
                        error="directline_secret_rejected",
                        status=r.status_code,
                        detail=r.text[:500],
                    ),
                    502,
                )
            try:
                return jsonify(r.json())
            except ValueError:
                return jsonify(error="upstream_non_json", detail=r.text[:500]), 502

        # Nothing configured (or the token URL failed and no secret to fall back on).
        if upstream_status is not None:
            return (
                jsonify(
                    error="upstream_error",
                    status=upstream_status,
                    detail=upstream_detail,
                    hint=(
                        "DIRECTLINE_TOKEN_ENDPOINT returned an error. Verify "
                        "the URL in Copilot Studio (Channels -> Custom website "
                        "-> Token Endpoint) and that the agent is published, "
                        "or set DIRECTLINE_SECRET as a fallback."
                    ),
                ),
                502,
            )
        return (
            jsonify(
                error="not_configured",
                hint=(
                    "Set DIRECTLINE_TOKEN_ENDPOINT or DIRECTLINE_SECRET in "
                    "bridge/.env."
                ),
            ),
            501,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
