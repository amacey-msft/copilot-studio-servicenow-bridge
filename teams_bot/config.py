"""Environment configuration for the Teams relay bot."""
from __future__ import annotations

import os


# --- Bot Framework / Azure Bot registration ---------------------------------

MS_APP_ID = os.environ.get("MS_APP_ID", "").strip()
MS_APP_PASSWORD = os.environ.get("MS_APP_PASSWORD", "").strip()
# "MultiTenant" | "SingleTenant" | "UserAssignedMSI". Default MultiTenant matches
# the simplest dev-PDI bot registration flow.
MS_APP_TYPE = os.environ.get("MS_APP_TYPE", "MultiTenant").strip()
MS_APP_TENANT_ID = os.environ.get("MS_APP_TENANT_ID", "").strip()


# --- Internal call to the bridge --------------------------------------------

# The relay bot calls the bridge's own HTTP routes (init-session, escalate,
# user-message, directline/token) over loopback by default.
BRIDGE_INTERNAL_URL = os.environ.get("BRIDGE_INTERNAL_URL", "http://127.0.0.1:5000").rstrip("/")


# --- Direct Line polling tunables -------------------------------------------

# How long to wait for a Copilot Studio reply burst on each user turn
# before returning control to Teams. Bot Framework's hard ceiling for a
# synchronous turn is ~15s; keep some headroom.
DIRECTLINE_TURN_TIMEOUT_S = float(os.environ.get("DIRECTLINE_TURN_TIMEOUT_S", "12"))
# Quiet period (no new activities) after which we consider the bot turn done.
DIRECTLINE_QUIET_PERIOD_S = float(os.environ.get("DIRECTLINE_QUIET_PERIOD_S", "1.5"))
DIRECTLINE_POLL_INTERVAL_S = float(os.environ.get("DIRECTLINE_POLL_INTERVAL_S", "0.5"))


def is_configured() -> bool:
    """The relay bot is only registered if a Bot Framework app id is present.
    Lets the existing web flow keep working in environments where the Teams
    branch isn't deployed."""
    return bool(MS_APP_ID)
