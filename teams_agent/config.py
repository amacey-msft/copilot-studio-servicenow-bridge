"""Environment configuration for `teams_agent` (M365 Agents SDK).

Distinct from `teams_bot.config` so the two services can run side-by-side
with separate Azure Bot registrations during cutover.
"""
from __future__ import annotations

import os


# --- Azure Bot registration (NEW resource, not the legacy teams_bot one) ---

AZURE_BOT_APP_ID = os.environ.get("AZURE_BOT_APP_ID", "").strip()
AZURE_BOT_APP_PASSWORD = os.environ.get("AZURE_BOT_APP_PASSWORD", "").strip()
AZURE_BOT_TENANT_ID = os.environ.get("AZURE_BOT_TENANT_ID", "").strip()
# "SingleTenant" | "MultiTenant" | "UserAssignedMsi"
AZURE_BOT_APP_TYPE = os.environ.get("AZURE_BOT_APP_TYPE", "SingleTenant").strip()

# OAuth connection name configured on the Azure Bot for OBO sign-in.
AZURE_BOT_OAUTH_CONNECTION_NAME = os.environ.get(
    "AZURE_BOT_OAUTH_CONNECTION_NAME", "mcs"
).strip()


# --- OBO app reg (used to exchange the user token for a CS-callable token) -

OBO_CLIENT_ID = os.environ.get("OBO_CLIENT_ID", "").strip()
OBO_CLIENT_SECRET = os.environ.get("OBO_CLIENT_SECRET", "").strip()
OBO_TENANT_ID = os.environ.get("OBO_TENANT_ID", AZURE_BOT_TENANT_ID).strip()


# --- Copilot Studio target -------------------------------------------------

COPILOTSTUDIO_ENVIRONMENT_ID = os.environ.get("COPILOTSTUDIO_ENVIRONMENT_ID", "").strip()
COPILOTSTUDIO_SCHEMA_NAME = os.environ.get("COPILOTSTUDIO_SCHEMA_NAME", "").strip()
# Name of the event activity raised by the CS Escalate topic.
# Optional: only needed if you wire a CS Event node for full Genesys parity.
COPILOTSTUDIO_HANDOFF_EVENT_NAME = os.environ.get(
    "COPILOTSTUDIO_HANDOFF_EVENT_NAME", "ServiceNowHandoff"
).strip()


# --- Bridge callback (existing Flask app, unchanged) -----------------------

# The agent calls the existing bridge over the same network it always has.
BRIDGE_INTERNAL_URL = os.environ.get(
    "BRIDGE_INTERNAL_URL", "http://127.0.0.1:5000"
).rstrip("/")

# Shared secret the bridge uses to authenticate inbound proactive pushes
# to /api/teams/push. Bridge sets the same value as PUSH_SHARED_SECRET in its env.
PUSH_SHARED_SECRET = os.environ.get("PUSH_SHARED_SECRET", "").strip()


# --- Hosting ---------------------------------------------------------------

PORT = int(os.environ.get("PORT", "3978"))


def is_configured() -> bool:
    """Minimal sanity check; surfaces obvious misconfig early in `app.py`."""
    return bool(
        AZURE_BOT_APP_ID
        and AZURE_BOT_TENANT_ID
        and COPILOTSTUDIO_ENVIRONMENT_ID
        and COPILOTSTUDIO_SCHEMA_NAME
    )
