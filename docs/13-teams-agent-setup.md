# 13 - Teams agent setup (M365 Agents SDK, Genesys-style)

This guide stands up the [`teams_agent/`](../teams_agent/) service: a
Microsoft Teams 1:1 bot that proxies turns to Copilot Studio over
Direct Line and pushes ServiceNow CSR replies into the same Teams
conversation via `adapter.continue_conversation`. This is one of two
Teams channels in the repo — see
[`10-teams-channel-overview.md`](10-teams-channel-overview.md) for the
difference between this and `teams_skill/` (the A2A path).

It uses the supported [Microsoft 365 Agents SDK](https://github.com/microsoft/Agents)
(`microsoft-agents-*`); the older `botbuilder-python`-based
`teams_bot/` was removed in v3.

> **Read first:** [`teams_agent/README.md`](../teams_agent/README.md) for
> a high-level summary of why this exists and the design decisions.

## What stays untouched

- `bridge/` Flask code: this channel uses the
  `TEAMS_AGENT_PUSH_URL` / `TEAMS_AGENT_PUSH_SECRET` env vars on the
  bridge to receive proactive push requests.
- `web/` browser webchat.
- `servicenow/` AWA queue, business rule, scripted REST.

## Rollback

Point the bridge at a different push URL (or stop this container) and
the web channel keeps working untouched. The bridge has no
teams_agent-specific behavior beyond the optional push fan-out.

## Prereqs

- Bridge already deployed and reachable (`BRIDGE_PUBLIC_URL` set; web
  channel green per [`06-end-to-end-test.md`](06-end-to-end-test.md))
- Copilot Studio agent already published with the **Escalate topic
  wired to the bridge HTTP action** per
  [`04-copilot-studio.md`](04-copilot-studio.md). The exact same topic
  works for Teams — no Teams-specific changes are required there.
- Permissions to create an Azure Bot resource

> If you skipped the web channel and came straight to Teams: you still
> need [`02-servicenow-setup.md`](02-servicenow-setup.md) (AWA queue +
> outbound webhook) and [`04-copilot-studio.md`](04-copilot-studio.md)
> (Escalate topic). The Teams path reuses both. Only `04` step 2
> (the HTTP action) is required; you can skip the
> `Bot.SendActivity` for the browser — it's harmless when invoked from
> Teams since the agent never reads it.

> **No user sign-in card.** This service runs in **Direct Line parity
> mode**: the agent calls the bridge's `/directline/token` proxy, which
> mints a server-side Copilot Studio Direct Line token. End users open
> Teams, type, get answer. No OBO. No Entra app for delegation. No bot
> OAuth Connection.

## 1. Create an Azure Bot resource

The provisioning script
[`scripts/provision-teams-agent.ps1`](../scripts/provision-teams-agent.ps1)
automates this; the manual steps are listed below for reference.

1. Azure portal -> Create -> **Azure Bot**.
2. Bot handle: e.g. `cps-sn-agent-dev`.
3. Type of App: **Single Tenant** (recommended; `MultiTenant` is
   deprecated in `az bot create` since late 2024).
4. Create a new Microsoft App ID. Save the **Application (client) ID** ->
   `AZURE_BOT_APP_ID`.
5. Configuration -> Manage Microsoft App ID -> Certificates & secrets ->
   New client secret. Save the *value* -> `AZURE_BOT_APP_PASSWORD`.
6. Configuration -> Messaging endpoint -> `https://<agent-host>/api/messages`.
7. Channels -> add **Microsoft Teams** (`az bot msteams create`). Accept
   terms.
8. Note the **Tenant ID** of your subscription -> `AZURE_BOT_TENANT_ID`.

## 2. Configure `teams_agent/.env`

Copy `teams_agent/.env.example` to `teams_agent/.env` and fill in:

```dotenv
AZURE_BOT_APP_ID=...                   # from step 1.4
AZURE_BOT_APP_PASSWORD=...             # from step 1.5
AZURE_BOT_APP_TYPE=SingleTenant
AZURE_BOT_TENANT_ID=...                # from step 1.8

# Bridge callback. From inside Docker on Windows/macOS use
# host.docker.internal; pass --add-host host.docker.internal:host-gateway
# on docker run for Linux containers.
BRIDGE_INTERNAL_URL=http://host.docker.internal:5001
PUSH_SHARED_SECRET=<long random string>

PORT=3978
LOG_LEVEL=INFO
```

> **`OBO_*` and `AZURE_BOT_OAUTH_CONNECTION_NAME` are not required.**
> They're left in `config.py` as opt-in placeholders if you ever want to
> flip back to the SDK's delegated `CopilotClient` path; the current
> `dl.py` Direct Line parity flow ignores them.

## 3. Run `teams_agent/` locally

```powershell
# From repo root
docker build -f teams_agent/Dockerfile -t cps-sn-teams-agent:dev .
docker run --rm -p 3978:3978 --env-file teams_agent/.env cps-sn-teams-agent:dev

# Sanity check
curl http://localhost:3978/healthz
```

Expose it via a second devtunnel (don't reuse the bridge tunnel — they need
separate hostnames so the Azure Bot messaging endpoint and the bridge's
`TEAMS_AGENT_PUSH_URL` can each resolve correctly):

```powershell
devtunnel create cps-sn-agent --allow-anonymous
devtunnel port create cps-sn-agent -p 3978
devtunnel host cps-sn-agent
```

Update the Azure Bot from step 1 -> Configuration -> Messaging endpoint to
`https://<your-agent-tunnel>-3978.<region>.devtunnels.ms/api/messages` and
**Apply**.

## 4. Wire the bridge's outbound push

Edit `bridge/.env` (the existing file used by the Flask bridge):

```dotenv
TEAMS_AGENT_PUSH_URL=https://<your-agent-tunnel>-3978.<region>.devtunnels.ms
TEAMS_AGENT_PUSH_SECRET=<same long random string as PUSH_SHARED_SECRET above>
```

Restart the bridge container. Verify with:

```powershell
docker compose -f bridge/docker-compose.yml restart
docker compose -f bridge/docker-compose.yml logs -f bridge | Select-String "push"
```

When the env vars are unset the bridge logs a warning per Teams push
but keeps running — the web channel is unaffected.

## 5. Sideload the Teams app

Build a Teams app manifest with the Azure Bot's app id from step 1.
Recommended layout:

```
teams_agent/manifest/
  manifest.json       # bot.id = AZURE_BOT_APP_ID
  color.png
  outline.png
```

Sideload via Teams → Apps → Manage your apps → Upload a custom app.

## 6. Smoke test

1. Open the new Teams app.
2. Send "hi" — expect an automated reply from your CS agent (no sign-in
   card; this is DL parity mode).
3. Type "talk to a human" (or whatever your Escalate trigger is). The CS
   topic calls the bridge's `/api/servicenow/agent/escalate` HTTP action
   exactly like the web channel does — no Teams-specific changes there.
   See [`04-copilot-studio.md`](04-copilot-studio.md) if you haven't
   wired it yet.
4. Bridge state moves BOT → QUEUED. The bridge fires the
   "Connecting an agent..." status push, which appears in the Teams app.
5. In ServiceNow, accept the work item. Bridge → LIVE. Type a reply on
   the agent side; it appears in the Teams app via
   `/api/teams/push` → `continue_conversation`.

If anything fails, check `docker logs` for both `bridge` and the agent
container, then jump to [`07-troubleshooting.md`](07-troubleshooting.md).

## 7. (Optional) Genesys-style escalation event

The baseline setup leaves the CS topic → bridge HTTP action wiring
intact. The agent ALSO listens for an event activity named
`COPILOTSTUDIO_HANDOFF_EVENT_NAME` (default `ServiceNowHandoff`) — if
you add an Event node at the end of your CS Escalate topic, the agent
will catch it and call the bridge directly, fully matching the Genesys
sample pattern. This becomes useful if you ever want to remove the
HTTP action from CS and let the agent own the escalation API call.

## Cutover-from-step-zero checklist

1. `TEAMS_AGENT_PUSH_URL` / `TEAMS_AGENT_PUSH_SECRET` set in
   `bridge/.env`. Bridge restarted.
2. Smoke test per step 6 passes.
3. Teams app installed in your tenant.

## Behavior notes (gotchas baked into the implementation)

These are documented here so you don't have to spelunk through the code
to understand non-obvious behaviors.

### Direct Line user-id mapping (Copilot Studio escalate tool)

When the agent mints a Direct Line token from the Copilot Studio token
endpoint, the token's `user` claim is the id Direct Line will rewrite
`from.id` to on every user activity — and that's what CS exposes as
`System.Activity.From.Id` inside topics. So when the CS Escalate HTTP
tool sends `session_id = System.Activity.From.Id` to the bridge, the
value is a CS-minted UUID, **not** any sid the bridge ever knows about.

Fix: `teams_agent/dl.py` decodes the DL token JWT after each token
mint, extracts the `user` claim, and registers the
`dl_user_id -> sid` mapping with the bridge via
`POST /api/teams/map-dl-user`. The bridge keeps an in-memory
`_by_dl_user` reverse index and falls back to it when
`/api/servicenow/agent/escalate` arrives with a sid the direct lookup
can't resolve.

### Live-state idle recycle

Teams "Clear conversation" (the user's chat menu) is **client-only** —
the bridge gets no signal it happened. Without a recycle, the user's
next message would forward into a dead live-chat (the SN interaction
the CSR walked away from) and you'd see nothing.

The bridge auto-recycles a stale `live` session into a fresh `bot`
session on the next `/api/teams/init-session` call when:

- `state == closed`, OR
- `state == live` and idle &gt; `TEAMS_LIVE_IDLE_RECYCLE_S` (default 900s
  / 15 min), OR
- any non-`bot` state and idle &gt; `TEAMS_SESSION_IDLE_TIMEOUT_S`
  (default 3600s / 1 hour).

Tune `TEAMS_LIVE_IDLE_RECYCLE_S` in `bridge/.env` for your CSR-chat
duration. Users can always type `new` (or `reset`, `restart`) for an
explicit reset; see [`teams_agent/README.md`](../teams_agent/README.md)
"User commands".

### Agent SDK message-route gotcha

The first iteration of `teams_agent/app.py` used
`@app.message(re.compile(".*"))` to catch all message activities.
This silently fails to match text containing newlines (Python regex
`.` doesn't match `\n` by default and the SDK doesn't add `re.DOTALL`).
Switched to `@app.activity("message")` which dispatches every message
activity unconditionally — keep it that way.

### `BRIDGE_INTERNAL_URL` from inside a Docker container

When the agent runs in Docker on the host's docker engine and the
bridge runs *also* in Docker (compose) or on the host directly, set:

```dotenv
BRIDGE_INTERNAL_URL=http://host.docker.internal:5001
```

and pass `--add-host host.docker.internal:host-gateway` on
`docker run` so Linux containers can resolve it. The
`http://bridge:5000` form only works if both containers share the
same compose network.
