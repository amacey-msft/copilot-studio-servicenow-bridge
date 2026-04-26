# 13 - Teams agent setup (M365 Agents SDK port)

This guide stands up the new `teams_agent/` service alongside the existing
`teams_bot/` relay so both can run in parallel during cutover. The new
service uses the supported [Microsoft 365 Agents SDK](https://github.com/microsoft/Agents)
in place of the deprecated `botbuilder-python` packages.

> **Read first:** [`teams_agent/README.md`](../teams_agent/README.md) for
> a high-level summary of why this exists and what changes from the
> legacy `teams_bot/`.

## What stays untouched

- `teams_bot/` keeps running on its own bot id / app id / Teams app id
- `bridge/` Flask code: only added a new env-gated push target
  (`TEAMS_PUSH_TARGET`); default is `legacy` so behavior is unchanged
- `web/` browser webchat
- `servicenow/` AWA queue, business rule, scripted REST

## Rollback at any point

```powershell
# Tag set before any of this work; one command full revert:
git checkout pre-agents-sdk-refactor
```

Or just leave `TEAMS_PUSH_TARGET=legacy` and stop the new container —
the old `teams_bot/` keeps serving Teams users.

## Prereqs

- Bridge already deployed and reachable (`BRIDGE_PUBLIC_URL` set; web
  channel green per [`06-end-to-end-test.md`](06-end-to-end-test.md))
- Permissions to create Azure Bot + Entra app registrations
- Copilot Studio agent already published (the same one used by the
  legacy `teams_bot/`)

> **Important UX change:** the M365 Agents SDK Copilot Studio client
> currently supports **delegated/OBO only** (S2S not yet shipped). End
> users will see a Teams sign-in card on first message. The legacy
> `teams_bot/` does not require this because it talks Direct Line with
> a server-side token.

## 1. Create a NEW Azure Bot resource

Do not reuse the legacy bot. We want true side-by-side so rollback is
trivial.

1. Azure portal -> Create -> **Azure Bot**.
2. Bot handle: e.g. `cps-sn-agent-dev`.
3. Type of App: **Single Tenant** (recommended for OBO scenarios).
4. Create a new Microsoft App ID. Save the **Application (client) ID** ->
   `AZURE_BOT_APP_ID`.
5. Configuration -> Manage Microsoft App ID -> Certificates & secrets ->
   New client secret. Save the *value* -> `AZURE_BOT_APP_PASSWORD`.
6. Configuration -> Messaging endpoint -> `https://<agent-host>/api/messages`.
7. Channels -> add **Microsoft Teams**. Accept terms.
8. Note the **Tenant ID** of your subscription -> `AZURE_BOT_TENANT_ID`.

## 2. Create the OBO app registration

This is what lets the agent exchange the user's Teams sign-in token for a
Copilot Studio-callable token. Same pattern as the .NET GenesysHandoff
sample.

1. Entra ID -> App registrations -> New registration.
2. Name: `cps-sn-agent-obo`.
3. Supported account types: **Single tenant**.
4. Register.
5. **Authentication** -> Add a platform -> **Web** -> Redirect URI:
   `https://token.botframework.com/.auth/web/redirect`.
6. Authentication -> Add a platform -> **Mobile and desktop applications**
   -> Redirect URI: `http://localhost`. (Required by the SDK auth flow.)
7. **API permissions** -> Add a permission. Add all three (Delegated):
   - **Power Platform API** -> `CopilotStudio.Copilots.Invoke`
   - **Microsoft Graph** -> `User.Read`
   - **Dynamics CRM** -> `user_impersonation`
   Then **Grant admin consent**.

   > If "Power Platform API" doesn't appear in the picker, follow
   > [Power Platform API Authentication, step 2](https://learn.microsoft.com/power-platform/admin/programmability-authentication-v2#step-2-configure-api-permissions)
   > to add it to your tenant.
8. **Expose an API** -> Set Application ID URI to
   `api://botid-<this-app's-client-id>` -> Add scope `defaultScope`,
   "Admins and users", fill required text, Add scope.
9. **Certificates & secrets** -> New client secret. Save the value ->
   `OBO_CLIENT_SECRET`. The app's Application (client) ID -> `OBO_CLIENT_ID`.

## 3. Wire the OAuth connection on the Azure Bot

1. Open the Azure Bot from step 1 -> **Configuration** -> **OAuth Connection
   Settings** -> **Add Setting**.
2. Name: `mcs` (this becomes `AZURE_BOT_OAUTH_CONNECTION_NAME`; any name
   works as long as it matches your env).
3. Service Provider: **Azure Active Directory v2**.
4. Client id: the OBO app's Application (client) ID.
5. Client secret: the secret from step 2.9.
6. Tenant ID: your tenant.
7. Scopes: `api://botid-<obo-app-client-id>/defaultScope`
8. Save -> click **Test connection** to verify.

## 4. Get Copilot Studio metadata

Open your existing CS agent in [copilotstudio.microsoft.com](https://copilotstudio.microsoft.com)
-> Settings -> Advanced -> **Metadata**:

- Schema name -> `COPILOTSTUDIO_SCHEMA_NAME`
- Environment ID -> `COPILOTSTUDIO_ENVIRONMENT_ID`

(No changes to the agent itself are required for Stage 1. The Stage 2
"Genesys-style escalation event" is optional and covered at the bottom.)

## 5. Configure `teams_agent/.env`

Copy `teams_agent/.env.example` to `teams_agent/.env` and fill in:

```dotenv
AZURE_BOT_APP_ID=...                   # from step 1.4
AZURE_BOT_APP_PASSWORD=...             # from step 1.5
AZURE_BOT_APP_TYPE=SingleTenant
AZURE_BOT_TENANT_ID=...                # from step 1.8
AZURE_BOT_OAUTH_CONNECTION_NAME=mcs    # from step 3.2

OBO_CLIENT_ID=...                      # from step 2.9
OBO_CLIENT_SECRET=...                  # from step 2.9
OBO_TENANT_ID=...                      # same tenant id

COPILOTSTUDIO_ENVIRONMENT_ID=...       # from step 4
COPILOTSTUDIO_SCHEMA_NAME=...          # from step 4
COPILOTSTUDIO_HANDOFF_EVENT_NAME=ServiceNowHandoff

BRIDGE_INTERNAL_URL=http://bridge:5000  # or http://127.0.0.1:5000 for local
PUSH_SHARED_SECRET=<long random string>

PORT=3978
LOG_LEVEL=INFO
```

## 6. Run `teams_agent/` locally

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

## 7. Wire the bridge's outbound push

Edit `bridge/.env` (the existing file used by the Flask bridge):

```dotenv
# Stage 1 cutover knob: send pushes to BOTH the legacy teams_bot.push
# in-process call AND the new teams_agent HTTP endpoint. This lets you
# run both Teams app installs in parallel and compare behavior.
TEAMS_PUSH_TARGET=both
TEAMS_AGENT_PUSH_URL=https://<your-agent-tunnel>-3978.<region>.devtunnels.ms
TEAMS_AGENT_PUSH_SECRET=<same long random string as PUSH_SHARED_SECRET above>
```

Restart the bridge container. Verify with:

```powershell
docker compose -f bridge/docker-compose.yml restart
docker compose -f bridge/docker-compose.yml logs -f bridge | Select-String "push"
```

When you only want the new path, set `TEAMS_PUSH_TARGET=agent`. To roll
back, set it to `legacy` (or unset; default is legacy).

## 8. Sideload the new Teams app

Build a *new* Teams app manifest with the **new** Azure Bot's app id (do
NOT edit the legacy `teams_bot/manifest/manifest.json`). Recommended layout:

```
teams_agent/manifest/
  manifest.json       # bot.id = AZURE_BOT_APP_ID
  color.png
  outline.png
```

Use the same icon files as the legacy bot if you like, but **change the
app `id` (top-level GUID)** so Teams treats it as a different app and you
can install both in parallel. Same goes for `name.short` (e.g. add
`(Agent SDK)` suffix during testing).

Sideload via Teams -> Apps -> Manage your apps -> Upload a custom app.

## 9. Smoke test

1. Open the new Teams app.
2. Send "hi" — you'll see a sign-in card. Sign in with the same tenant
   account you used to test the web channel.
3. After sign-in, expect an automated reply from your CS agent.
4. Type "talk to a human" (or whatever your Escalate trigger is). The CS
   topic still calls the bridge's existing `/api/servicenow/agent/escalate`
   HTTP action — no change required there.
5. Bridge state moves BOT -> QUEUED. The bridge fires the
   "Connecting an agent..." status push. With `TEAMS_PUSH_TARGET=both`
   you'll see it in BOTH the new and legacy Teams app installs.
6. In ServiceNow, accept the work item. Bridge -> LIVE. Type a reply on
   the agent side; it appears in the new Teams app via
   `/api/teams/push` -> `continue_conversation`.

If anything fails, check `docker logs` for both `bridge` and the new agent
container, then jump to [`07-troubleshooting.md`](07-troubleshooting.md).

## 10. (Optional, Stage 2+) Genesys-style escalation event

The Stage 1 setup leaves CS topic -> bridge HTTP action wiring intact.
The agent ALSO listens for an event activity named
`COPILOTSTUDIO_HANDOFF_EVENT_NAME` (default `ServiceNowHandoff`) — if you
add an Event node at the end of your CS Escalate topic, the agent will
catch it and call the bridge directly, fully matching the Genesys sample
pattern. This becomes useful if you ever want to remove the HTTP action
from CS and let the agent own the escalation API call.

## Cutover checklist (when ready to retire the legacy relay)

1. Set `TEAMS_PUSH_TARGET=agent` (not `both`) in `bridge/.env`. Restart.
2. Validate end-to-end again per step 9.
3. Uninstall legacy Teams app from your tenant (or just unpublish).
4. Stop / scale-to-zero the legacy `teams_bot/` container.
5. (Stage 3) Delete `teams_bot/`, remove `MS_APP_*` env vars, remove
   `_push_to_teams_legacy` from the bridge.

Until step 5 you can revert in seconds by flipping `TEAMS_PUSH_TARGET`
back to `legacy`.
