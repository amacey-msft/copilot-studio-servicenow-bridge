"""Flask blueprint exposing the Bot Framework `/api/messages` endpoint.

Mounted on the existing bridge Flask app in app.py. No-op when MS_APP_ID isn't
configured, so the existing web flow keeps running in environments that don't
need the Teams branch.
"""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from . import config, runtime
from .relay import TeamsRelayBot


_log = logging.getLogger(__name__)


bp = Blueprint("teams_bot", __name__)

# Singleton bot. ActivityHandler instances are stateless apart from injected
# wiring, so reusing one is safe and saves per-turn allocations.
_bot = TeamsRelayBot()


@bp.route("/api/messages", methods=["POST"])
def messages():
    """Bot Framework receive endpoint."""
    if not config.is_configured():
        return jsonify({"error": "teams_bot_not_configured"}), 501
    if "application/json" not in (request.headers.get("Content-Type") or ""):
        return jsonify({"error": "expected application/json"}), 415

    auth_header = request.headers.get("Authorization", "")
    body = request.get_json(silent=True) or {}

    try:
        invoke_response = runtime.process_activity_sync(
            auth_header,
            body,
            _bot.on_turn,
        )
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Bot Framework process_activity failed")
        return jsonify({"error": "process_failed"}), 500

    if invoke_response is None:
        # Standard messages return 202 Accepted with no body.
        return ("", 202)
    return (
        invoke_response.body if invoke_response.body is not None else "",
        invoke_response.status,
    )


def register(app) -> None:
    """Idempotent registration; called from bridge/app.py."""
    if not config.is_configured():
        _log.info("[teams_bot] MS_APP_ID not set; /api/messages endpoint disabled")
        return
    app.register_blueprint(bp)
    _log.info("[teams_bot] /api/messages endpoint registered")
