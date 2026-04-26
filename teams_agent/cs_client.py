"""Factory for Copilot Studio `CopilotClient` instances.

Each turn we mint a client bound to the OBO-exchanged user token retrieved
from the SDK's UserAuthorization handler. This mirrors the .NET
GenesysHandoff `CopilotClientFactory.CreateClient(turnContext)` pattern.
"""
from __future__ import annotations

import logging
from typing import Optional

from microsoft_agents.copilotstudio.client import (
    ConnectionSettings,
    CopilotClient,
)

from . import config


_log = logging.getLogger(__name__)


def build_settings() -> ConnectionSettings:
    return ConnectionSettings(
        environment_id=config.COPILOTSTUDIO_ENVIRONMENT_ID,
        agent_identifier=config.COPILOTSTUDIO_SCHEMA_NAME,
        cloud=None,
        copilot_agent_type=None,
        custom_power_platform_cloud=None,
    )


def make_client(token: str) -> CopilotClient:
    """`token` must be an OBO-exchanged token with the
    `https://api.powerplatform.com/CopilotStudio.Copilots.Invoke` scope."""
    if not token:
        raise ValueError("Cannot create CopilotClient: empty token")
    return CopilotClient(build_settings(), token)


def get_token_from_turn(turn_context, user_state, handler_name: Optional[str] = None) -> Optional[str]:
    """Best-effort token retrieval from the SDK's auth state.

    The exact accessor moved a few times across SDK versions; we probe a
    couple of common locations so this scaffold survives minor SDK changes.
    Replace with the canonical `UserAuthorization.get_turn_token(...)` call
    once the import path is locked in for 0.9.x.
    """
    handler = handler_name or config.AZURE_BOT_OAUTH_CONNECTION_NAME

    # Likely accessor (kept loose for 0.9.x churn).
    for attr_path in (
        ("user_authorization", "get_turn_token"),
        ("auth", "get_turn_token"),
    ):
        obj = turn_context
        ok = True
        for attr in attr_path:
            obj = getattr(obj, attr, None)
            if obj is None:
                ok = False
                break
        if ok and callable(obj):
            try:
                tok = obj(handler)
                if tok:
                    return tok
            except Exception:  # noqa: BLE001
                _log.debug("token accessor %s raised", ".".join(attr_path), exc_info=True)

    return None
