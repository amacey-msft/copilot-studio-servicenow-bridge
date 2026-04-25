"""Asyncio runtime + Bot Framework adapter wiring.

botbuilder-python is asyncio-native, but the bridge's Flask app runs under
gevent gunicorn (sync request handlers). We resolve the impedance mismatch by
hosting a single dedicated asyncio loop in a daemon thread, then submitting
coroutines from the Flask request thread via `run_async`.

This module exposes:
    get_adapter()      -> the singleton CloudAdapter
    run_async(coro)    -> blocks the caller until the coroutine completes
    process_activity_sync(...)  -> Flask-side wrapper for /api/messages
    continue_conversation_sync(...) -> push wrapper for the bridge dispatcher
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import Future
from typing import Any, Awaitable, Callable

from botbuilder.core import TurnContext
from botbuilder.integration.aiohttp import (
    CloudAdapter,
    ConfigurationBotFrameworkAuthentication,
)
from botbuilder.schema import Activity, ConversationReference

from . import config


_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background asyncio loop
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _start_loop() -> asyncio.AbstractEventLoop:
    """Lazily start the dedicated asyncio loop on first use."""
    global _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        loop = asyncio.new_event_loop()

        def _runner():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_runner, name="teams-bot-loop", daemon=True)
        t.start()
        _loop = loop
        return loop


def run_async(coro: Awaitable[Any], timeout: float | None = 30.0) -> Any:
    """Submit a coroutine to the background loop and block until it returns."""
    loop = _start_loop()
    fut: Future = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class _BotFrameworkConfig:
    """Minimal config object the way ConfigurationBotFrameworkAuthentication
    and ConfigurationServiceClientCredentialFactory expect.

    The credential factory uses `hasattr(cfg, 'APP_ID')` + `cfg.APP_ID` and
    expects `APP_TYPE` to be one of `"MultiTenant"`, `"SingleTenant"`,
    `"UserAssignedMsi"`. The auth class additionally pulls a few optional
    keys via `getattr(cfg, 'CHANNEL_SERVICE', None)` etc. — we expose
    everything as plain attributes plus a `get()` for forward compat.
    """

    def __init__(self) -> None:
        # Keys the credential factory cares about (attribute access).
        self.APP_TYPE = config.MS_APP_TYPE or "MultiTenant"
        self.APP_ID = config.MS_APP_ID or ""
        self.APP_PASSWORD = config.MS_APP_PASSWORD or ""
        self.APP_TENANTID = config.MS_APP_TENANT_ID or ""

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


_adapter: CloudAdapter | None = None
_adapter_lock = threading.Lock()


def get_adapter() -> CloudAdapter:
    """Singleton CloudAdapter wired to env-derived BF auth + an error handler."""
    global _adapter
    with _adapter_lock:
        if _adapter is not None:
            return _adapter
        auth = ConfigurationBotFrameworkAuthentication(_BotFrameworkConfig())
        adapter = CloudAdapter(auth)

        async def _on_error(turn_context: TurnContext, error: Exception):
            _log.exception("Unhandled bot error: %s", error)
            try:
                await turn_context.send_activity(
                    "Sorry, something went wrong on our end. Please try again."
                )
            except Exception:  # noqa: BLE001
                pass

        adapter.on_turn_error = _on_error
        _adapter = adapter
        return adapter


# ---------------------------------------------------------------------------
# Sync wrappers used from Flask code paths
# ---------------------------------------------------------------------------

def process_activity_sync(
    auth_header: str,
    body: dict,
    logic: Callable[[TurnContext], Awaitable[None]],
) -> Any:
    """Hand off an inbound /api/messages POST to the adapter.

    Returns the InvokeResponse if the activity was an invoke, else None.
    """
    activity = Activity().deserialize(body)
    adapter = get_adapter()
    return run_async(
        adapter.process_activity(auth_header, activity, logic),
        timeout=config.DIRECTLINE_TURN_TIMEOUT_S + 5,
    )


def continue_conversation_sync(
    reference_dict: dict,
    logic: Callable[[TurnContext], Awaitable[None]],
    *,
    timeout: float = 15.0,
) -> None:
    """Push a proactive activity into a previously-captured Teams 1:1 chat.

    `reference_dict` is the JSON-serialisable form of `ConversationReference`
    (what `TurnContext.get_conversation_reference(...).serialize()` returns).
    """
    reference = ConversationReference().deserialize(reference_dict)
    adapter = get_adapter()
    run_async(
        adapter.continue_conversation(reference, logic, bot_app_id=config.MS_APP_ID),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def serialize_reference(reference: ConversationReference) -> dict:
    """ConversationReference -> JSON-safe dict (for storage on BridgeSession)."""
    return json.loads(json.dumps(reference.serialize()))
