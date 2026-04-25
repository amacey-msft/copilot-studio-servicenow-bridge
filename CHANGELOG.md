# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Microsoft Teams channel** as a first-class second front-end alongside
  the browser webchat. A new `teams_bot/` package adds a Bot Framework
  relay that proxies Teams 1:1 chats to the same Copilot Studio agent
  and ServiceNow live-chat handoff used by the web UI:
  - `teams_bot/runtime.py` -- `CloudAdapter` singleton with sync wrappers
    around `process_activity` / `continue_conversation` so Flask routes
    can drive an async botbuilder pipeline.
  - `teams_bot/relay.py` -- `TeamsRelayBot` state machine
    (BOT / QUEUED / LIVE / CLOSED) mirroring the web flow, including
    Adaptive Cards for status changes, typing indicators, and a
    universal `new` / `reset` / `start over` escape command from any
    state.
  - `teams_bot/directline.py` -- per-session Direct Line client to
    Copilot Studio. Always starts the conversation explicitly so we
    capture the regional DL gateway from `streamUrl` (CS tokens are
    bound to e.g. `unitedstates.directline.botframework.com`, not the
    global host). Filters bot replies by the activity IDs we posted so
    DL's `from.id` rewrite doesn't leak the user's own messages back.
  - `teams_bot/push.py` -- proactive push of rep replies / status /
    typing into the Teams chat via `continue_conversation`.
  - `teams_bot/blueprint.py` -- Flask blueprint exposing
    `/api/messages` for the Bot Framework channel.
  - `teams_bot/manifest/` -- v1.16 Teams app manifest, build script
    (`build.ps1`), and placeholder icon generator
    (`make-placeholder-icons.ps1`).
- `bridge/servicenow_bridge.py` channel-aware `BridgeSession`:
  `channel`, `teams_user_key`, `teams_conversation_reference`,
  and a `_push_to_user` dispatcher that routes outbound events to
  the right front-end (WebSocket for web, `continue_conversation` for
  Teams).
- `/api/teams/init-session` and `/api/teams/reset-session` Flask routes
  for the Teams relay's idempotent per-AAD-user session lookup.
- `TEAMS_SESSION_IDLE_TIMEOUT_S` env var (default 3600s) plus
  `last_activity_at` on `BridgeSession`. The Teams init route auto-recycles
  any non-BOT session that is `closed` or has been idle past the timeout
  so a user who escalated days ago isn't stuck talking to a long-dead
  live chat.
- Docs: `docs/10-teams-channel-overview.md`,
  `docs/11-teams-bot-setup.md`, `docs/12-teams-end-to-end-test.md`.

### Added
- `BRIDGE_PUBLIC_URL` env var (in `bridge/.env`) is now the single source
  of truth for the bridge's public HTTPS URL. `scripts/sync-bridge-url.ps1`
  reads it and patches the ServiceNow `sys_property` and the Copilot Studio
  HTTP-tool botcomponents in one shot, eliminating manual UI edits when
  the tunnel URL changes.
- Persistent VS Code Dev Tunnel helper scripts under `scripts/`:
  `devtunnel-create.ps1`, `devtunnel-host.ps1`, `devtunnel-delete.ps1`,
  and `devtunnel-README.md`.
- Lived-in reference intranet page (`web/intranet.html`) with a richer
  Contoso Connect layout, chat launcher with unread badge, quick replies,
  in-panel restart/end confirm modal, and full bot-to-rep handoff state
  machine.
- `bridge/docker-compose.yml` now mounts `web/` and `bridge/*.py` as
  read-only volumes, so edits to the reference page or bridge code apply
  on `docker compose restart` without rebuilding the image.
- `/` route on the bridge sets `Cache-Control: no-store` on the reference
  page so dev edits show up without a hard refresh.

### Changed
- `/directline/token` now uses the Copilot Studio token endpoint with
  HTTP `GET` and forwards the bridge session id as a `userId` query
  parameter (matches the working Copilot Studio Direct Line contract);
  falls back to the legacy `DIRECTLINE_SECRET` if `DIRECTLINE_TOKEN_ENDPOINT`
  is unset.
- `/api/servicenow/escalate` is now idempotent: if the session already has
  an interaction, it returns the existing one instead of opening a second
  ServiceNow chat (covers the agent-tool + `handoff.initiate` race).
- The reference page passes `session_id` on `/api/servicenow/escalate`
  so the server-side idempotency check actually matches.
- The reference page filters Direct Line activity echoes by exact text
  against a per-session buffer of recent user sends, so the user's own
  message no longer renders as an Assistant bubble (Copilot Studio mints
  its own user GUID at token-mint time, defeating id-only filters).
- The bridge webhook drops rep-reply pushes whose text matches a recent
  user send on the same session, so the user's live-chat messages no
  longer come back to them as agent replies (the SN `sys_cs_message`
  Business Rule fires on user inserts too).
- The reference page no longer shows a fake "Agent typing" indicator on
  user send in live mode; only real rep typing events drive the dots.

## [0.1.0] - 2026-04-24

### Added
- Initial public release.
- ServiceNow Scripted REST resources `open_chat` and `send_message`.
- Outbound Business Rule on `sys_cs_message` that posts agent replies to
  a configurable bridge webhook.
- Flask bridge (`servicenow_bridge.py`) that translates between
  Copilot Studio (Direct Line) and ServiceNow Conversation APIs, with
  per-session state machine and WebSocket / long-poll fan-out to the
  browser.
- Minimal Flask host (`bridge/app.py`) that registers the bridge,
  serves the reference intranet page, and stubs `/directline/token`.
- Reference intranet page (`web/intranet.html`) with self-contained chat
  UI that drives Direct Line directly and switches transparently to the
  ServiceNow rep once a handoff completes.
- End-to-end smoke-test (`tools/probe_e2e.ps1`).
- Documentation suite under `docs/`:
  - `01-architecture.md`, `02-servicenow-setup.md`,
    `03-bridge-backend.md`, `04-copilot-studio.md`,
    `05-browser-webchat.md`, `06-end-to-end-test.md`,
    `07-troubleshooting.md`, `08-api-reference.md`,
    `09-production-hardening.md`.
