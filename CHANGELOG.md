# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **`teams_skill/`**: third channel built on the Microsoft 365 Agents
  SDK and registered with Copilot Studio via the **A2A "Add an agent"**
  connector. CS dispatches turns to the skill based on a natural-language
  agent description; the skill replies synchronously and proactively
  pushes ServiceNow CSR messages back to the signed CS `serviceUrl`.
  See [`docs/14-teams-skill-setup.md`](docs/14-teams-skill-setup.md)
  and [`docs/v3-skill-pattern-rejected.md`](docs/v3-skill-pattern-rejected.md)
  for why the classic Bot Framework skill protocol was abandoned in
  favour of A2A.
- **`teams_agent/`**: M365 Agents SDK Teams relay (Genesys-style
  handoff). See [`docs/13-teams-agent-setup.md`](docs/13-teams-agent-setup.md).
- **Direct Line user-id mapping** (`teams_agent/dl.py` +
  `bridge.servicenow_bridge.map_dl_user`): the agent decodes the DL token
  JWT after each token mint and registers the `dl_user_id -> sid` mapping
  with the bridge via `POST /api/teams/map-dl-user`. This is what makes
  the Copilot Studio "Escalate to live agent" HTTP tool work end-to-end:
  CS exposes `System.Activity.From.Id` (which DL has rewritten to a
  CS-minted UUID) as the `session_id` on the escalate POST; the bridge
  resolves it back to our internal sid via the new reverse index.
- **Live-state idle recycle** (`TEAMS_LIVE_IDLE_RECYCLE_S`, default 900s).
  Teams "Clear conversation" is client-only — the bridge gets no signal,
  so without this the next user turn forwards into a dead live-chat. The
  bridge now auto-recycles a stale `live` session into a fresh `bot`
  session after 15 min idle (or immediately on a `closed` state, or after
  the existing 1-hour catch-all for any non-bot state).
- Bridge env-gated push to `teams_agent/`: `TEAMS_AGENT_PUSH_URL`,
  `TEAMS_AGENT_PUSH_SECRET`.

### Removed
- **`teams_bot/`** (Bot Framework `botbuilder-python` 4.17.x relay) and
  its docs (`docs/11-teams-bot-setup.md`, `docs/12-teams-end-to-end-test.md`).
  Microsoft put `botbuilder-python` into maintenance mode and replaced it
  with `microsoft-agents-*`; once `teams_agent/` reached parity, keeping
  the legacy SDK around only added cutover knobs and contributor
  confusion. Both surviving Teams channels (`teams_agent/`, `teams_skill/`)
  use the supported Agents SDK. See
  [`docs/10-teams-channel-overview.md`](docs/10-teams-channel-overview.md)
  for the full rationale.
- `TEAMS_PUSH_TARGET` bridge env var. With `teams_bot/` gone the
  dispatcher always pushes to `teams_agent/` when configured.

### Fixed
- Agent SDK message route used a regex catch-all (`@app.message(re.compile(".*"))`)
  which silently failed to match text containing newlines. Switched to
  `@app.activity("message")` so every message activity is dispatched.
- A2A connector raised `aiohttp.ContentTypeError` on Copilot Studio's
  empty 200 ack to proactive POSTs. Monkey-patched in
  `teams_skill/app.py::_patch_mcs_connector()`.

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
