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
        """Stub. Replace with a real call to your Copilot Studio token endpoint.

        The reference intranet.html expects:
            POST /directline/token  ->  { "token": "<direct-line-token>" }

        See:
        https://learn.microsoft.com/microsoft-copilot-studio/publication-connect-bot-to-custom-application
        https://learn.microsoft.com/azure/bot-service/rest-api/bot-framework-rest-direct-line-3-0-authentication
        """
        return (
            jsonify(
                error="not_implemented",
                hint=(
                    "Wire this endpoint up to your Copilot Studio token URL "
                    "and return { token: <direct-line-token> }."
                ),
            ),
            501,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
