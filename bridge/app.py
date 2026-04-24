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
from flask import Flask, jsonify, send_from_directory
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
        return send_from_directory(WEB_DIR, "intranet.html")

    @app.get("/healthz")
    def _healthz():
        return jsonify(status="ok")

    @app.post("/directline/token")
    def _directline_token():
        """Mint a Direct Line token for the reference intranet page.

        Two supported modes (in order of preference):

        1. ``DIRECTLINE_TOKEN_ENDPOINT`` -- the Copilot Studio "Token
           endpoint" URL (Channels -> Custom website). The bridge POSTs to
           it and returns the JSON straight through. This is the
           recommended path because it requires no shared secret in the
           bridge.

        2. ``DIRECTLINE_SECRET`` -- a Direct Line channel secret from the
           Azure Bot resource (Bot Framework Direct Line channel). The
           bridge POSTs to ``https://directline.botframework.com/v3/directline/tokens/generate``
           with ``Authorization: Bearer <secret>`` and returns the JSON.
           Use this if the Power Platform token endpoint isn't available
           (e.g. agent isn't published to a custom-website channel).

        Returns ``{"token": "...", "conversationId": "...", "expires_in": 3600}``
        for BotFramework WebChat.
        """
        token_url = os.environ.get("DIRECTLINE_TOKEN_ENDPOINT", "").strip()
        secret    = os.environ.get("DIRECTLINE_SECRET", "").strip()

        # Try the Copilot Studio token endpoint first.
        if token_url:
            try:
                r = requests.post(token_url, timeout=15)
                if r.ok:
                    try:
                        return jsonify(r.json())
                    except ValueError:
                        pass  # fall through to secret path
                # Non-2xx: log and try secret fallback if present.
                upstream_detail = r.text[:500]
                upstream_status = r.status_code
            except requests.RequestException as exc:
                upstream_detail = str(exc)
                upstream_status = None
        else:
            upstream_detail = None
            upstream_status = None

        # Fallback: mint a token from the Direct Line channel secret.
        if secret:
            try:
                r = requests.post(
                    "https://directline.botframework.com/v3/directline/tokens/generate",
                    headers={"Authorization": f"Bearer {secret}"},
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
