# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Removed
- **`teams_agent/` reference implementation.** The Genesys-style
  M365 Agents SDK relay (Teams app = bot, server-side handoff
  driven by a `ServiceNowHandoff` Direct Line custom event) is
  gone. The Connected Agent (`teams_a2a/`) attached to
  `awm_contosoithelp` made the CS Escalate topic redirect to the
  Connected Agent instead of emitting `ServiceNowHandoff`, so
  `teams_agent/`'s escalate listener became dead code. With
  `teams_a2a/` covering the Teams handoff via the canonical
  Connected Agent path, keeping a parallel implementation only
  added cutover knobs and confusion. Removed:
  - `teams_agent/` (folder)
  - `scripts/provision-teams-agent.ps1`,
    `scripts/teardown-teams-agent.ps1`
  - `docs/13-teams-agent-setup.md`
  - Bridge `/api/teams/init-session`, `/api/teams/reset-session`,
    `/api/teams/map-dl-user` routes
  - Bridge `_push_to_teams_agent` dispatcher branch
  - `BridgeSession.channel`, `.teams_user_key`,
    `.teams_conversation_reference`, `.last_activity_at` fields
  - `_by_teams_user`, `_by_dl_user` reverse indexes
  - `TEAMS_AGENT_PUSH_URL`, `TEAMS_AGENT_PUSH_SECRET`,
    `TEAMS_AGENT_PUSH_TIMEOUT_S`, `TEAMS_SESSION_IDLE_TIMEOUT_S`,
    `TEAMS_LIVE_IDLE_RECYCLE_S` env vars (and matching wiring in
    `scripts/deploy-bridge-aca.ps1`)

### Added
- **Bridge hosted on Azure Container Apps** as `ca-cps-bridge` in the
  `cae-cpv` environment / `rg-cpv-aca` resource group. Image
  `acrcpvb0c139ea.azurecr.io/bridge:latest`. Stable HTTPS URL replaces
  the laptop-bound dev tunnel for any non-local consumers (ServiceNow
  outbound webhook, Copilot Studio HTTP tools).
- `scripts/deploy-bridge-aca.ps1` — full ACR build + ACA create/update
  with secrets sourced from `bridge/.env`. Runs `/healthz` smoke check
  on the new revision before reporting success.
- **Caveats:** the ACA app runs `min=max=1` because the bridge holds
  session state in process memory. Any revision swap (image push or
  secret rotation) drops active live-chat sessions. Externalising state
  to Redis is tracked as a follow-up. The local Docker compose stack
  remains for development only.

### Changed
- **Two-CS-agent topology, unified handoff backend.** The web kiosk
  and the Teams channel run on **separate** Copilot Studio agents
  because they have incompatible auth requirements:
  - Web (`web/intranet.html`) → `awm_contosoithelp`, **anonymous**
    Direct Line via the CS-hosted token endpoint
    `https://<env-host>.environment.api.powerplatform.com/powervirtualagents/botsbyschema/awm_contosoithelp/directline/token?api-version=2022-03-01-preview`.
    Suitable for unauthenticated kiosks.
  - Teams → `crd20_itHelpDeskTriageAssistant`, **Entra Agent ID**
    auth via CS's native Teams channel.

  Both agents register the same `teams_a2a` deployment
  (`ca-cps-sn-skill` ACA app) as an A2A **Connected Agent**, and both
  Escalate system topics delegate to it. Result: one shared handoff
  backend, one shared bridge, one shared ServiceNow integration; only
  the per-channel auth model differs. The earlier attempt to unify
  on a single CS agent was reverted because Entra Agent ID forces an
  MSAL sign-in in the browser.
- **Web channel escalation now uses the Connected Agent path** (same
  as Teams). The legacy "Escalate topic + Send an HTTP request"
  pattern was removed from `awm_contosoithelp`. The Connected Agent
  owns the conversation for the duration of the live chat and pushes
  CSR replies back into CS via the signed `serviceUrl` proactive POST,
  so reps render *as the CS agent* without any client-side mode
  switching.
- `bridge/.env.sample` `POWERPLATFORM_BOT_SCHEMA` corrected to
  `awm_contosoithelp` (was previously left pointing at the unified
  `crd20_itHelpDeskTriageAssistant`, so `sync-bridge-url.ps1` was
  looking for HTTP-tool botcomponents under the wrong schema and
  silently skipping them).
- The Bot Framework Skill registration on `teams_a2a /api/messages`
  was **removed**. It only worked on classic-app-reg agents, not
  Entra Agent ID, and consistently failed with
  `SkillNotSuccesfulResponseCode` / `401 Unauthorized` on the
  `pvaruntime/skillsV2` callback. See
  [`docs/v3-skill-pattern-rejected.md`](docs/v3-skill-pattern-rejected.md).

### Documentation
- Two-agent + Connected Agent topology now reflected throughout
  `docs/00-architecture-overview.md`, `docs/01-architecture.md`,
  `docs/04-copilot-studio.md` (full rewrite),
  `docs/10-teams-channel-overview.md`,
  `docs/14-teams-a2a-setup.md`, and the top-level `README.md`.
  The interim planning note `docs/v2-web-connected-agent-followup.md`
  was removed (work landed).

### Removed
- `teams_a2a/IT Help Desk Triage Assistant/skills/ServiceNowLiveAgentHandoffSkill.mcs.yml`
  and the two paired environment-variable yml files.
- `InvokeSkillAction` node from the Escalate topic.

### Added
- **`teams_a2a/`**: third channel built on the Microsoft 365 Agents
  SDK and registered with Copilot Studio via the **A2A "Add an agent"**
  connector. CS dispatches turns to the agent based on a natural-language
  agent description; the agent replies synchronously and proactively
  pushes ServiceNow CSR messages back to the signed CS `serviceUrl`.
  See [`docs/14-teams-a2a-setup.md`](docs/14-teams-a2a-setup.md)
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
  confusion. Both surviving Teams channels (`teams_agent/`, `teams_a2a/`)
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
  `teams_a2a/app.py::_patch_mcs_connector()`.

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
- Documentation suite under `docs/`:
  - `01-architecture.md`, `02-servicenow-setup.md`,
    `03-bridge-backend.md`, `04-copilot-studio.md`,
    `05-browser-webchat.md`, `06-end-to-end-test.md`,
    `07-troubleshooting.md`, `08-api-reference.md`,
    `09-production-hardening.md`.
