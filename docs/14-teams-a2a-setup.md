# 14 - Teams A2A setup (Copilot Studio A2A "Add an agent" connector)

This guide stands up the [`teams_a2a/`](../teams_a2a/) service and
registers it with a Copilot Studio agent as an **A2A** ("Add an agent →
Microsoft 365 Agents SDK") sub-agent. End state: a user chatting with
the CS agent — in **Teams** (CS's native channel) **or in the browser**
(via the bridge's Direct Line relay, see
[`05-browser-webchat.md`](05-browser-webchat.md)) — can ask to "talk to a
person", and CS dispatches the conversation to our agent, which opens a
ServiceNow live chat and round-trips messages.

> **Note:** historically this guide was titled "Teams skill setup" and
> the Escalate topic also called this same endpoint as a Bot Framework
> Skill. The Skill path was removed on 2026-05-04 because Entra Agent
> ID agents cannot authenticate the `pvaruntime/skillsV2` callback —
> see [`v3-skill-pattern-rejected.md`](v3-skill-pattern-rejected.md).
> The Connected Agent (A2A) path is now the sole handoff mechanism.

> **Read first:** [`10-teams-channel-overview.md`](10-teams-channel-overview.md)
> for the architectural difference between this and `teams_agent/`.
> The two Teams options are alternatives, not layers.

## What stays untouched

- `bridge/` Flask code: `teams_a2a/` calls the same
  `/api/servicenow/agent/escalate` and `/api/servicenow/user-message`
  endpoints the web channel uses.
- `web/` browser webchat.
- `servicenow/` AWA queue, scripted REST. The outbound BR has a
  parallel companion (see
  [`servicenow/outbound_business_rule_a2a.md`](../servicenow/outbound_business_rule_a2a.md))
  that POSTs to the A2A agent's `/api/sn-webhook` so this channel gets
  rep replies. The original web-channel BR is unchanged.

## Prereqs

- Bridge already deployed and reachable per
  [`03-bridge-backend.md`](03-bridge-backend.md).
- A Copilot Studio agent **using generative orchestration** (classic
  orchestration cannot dispatch to A2A agents).
- Permission to create an Azure AD app registration (single-tenant) and
  an Azure Container Apps deployment (or any HTTPS host with public
  ingress).

## 1. Provision the AAD app

The A2A connector authenticates inbound requests with a client-credentials
flow. Create a classic AAD app reg (NOT an Entra Agent ID — A2A needs
the classic shape):

```powershell
$tenant = "<your tenant guid>"
$app = az ad app create --display-name cps-sn-skill --sign-in-audience AzureADMyOrg | ConvertFrom-Json
$secret = az ad app credential reset --id $app.appId --years 2 | ConvertFrom-Json
"A2A_APP_ID=$($app.appId)"
"A2A_APP_PASSWORD=$($secret.password)"
"A2A_TENANT_ID=$tenant"
```

No API permissions, no Azure Bot resource, no Teams app manifest —
none of that is needed because **CS owns the Teams surface**.

## 2. Configure `teams_a2a/` env

Required:

```dotenv
A2A_APP_ID=<from step 1>
A2A_APP_PASSWORD=<from step 1>
A2A_TENANT_ID=<from step 1>
A2A_PUBLIC_URL=https://<your-host>          # public HTTPS base URL
BRIDGE_INTERNAL_URL=https://<your-bridge-host> # bridge reachable from this process
SN_WEBHOOK_SECRET=<long random string>         # shared with the SN BR
PORT=3979
LOG_LEVEL=INFO
```

## 3. Deploy

The repo's reference deployment is Azure Container Apps. Build + push
via ACR:

```powershell
az acr build --registry <acr-name> --image teams-a2a:latest `
    --file teams_a2a/Dockerfile .

az containerapp update -n ca-cps-sn-skill -g <rg> `
    --image <acr-name>.azurecr.io/teams-a2a:latest `
    --revision-suffix vMMDDHHMM
```

Always pass `--revision-suffix` — re-pushing the same `:latest` tag
does not roll a new ACA revision. After update, verify:

```powershell
az containerapp revision list -n ca-cps-sn-skill -g <rg> `
    --query "[?properties.active].{name:name,health:properties.healthState,traffic:properties.trafficWeight}"
```

You want one active revision, `Healthy`, `100` traffic. Smoke test:

```powershell
curl https://<host>/healthz
```

## 4. Register as an A2A agent in Copilot Studio

In CS Studio, open the parent agent → **Agents** tab → **Add an agent**
→ **Microsoft 365 Agents SDK**:

| Field            | Value                                                                       |
|------------------|-----------------------------------------------------------------------------|
| Endpoint URL     | `https://<your-host>/api/messages`                                          |
| Name             | `ServiceNow Live Agent` (or whatever; user-visible in CS overview only)     |
| Description      | Natural-language description of WHEN to dispatch — see note below.          |
| Connection auth  | Client secret                                                               |
| Tenant ID        | `A2A_TENANT_ID`                                                           |
| Client ID        | `A2A_APP_ID`                                                              |
| Client secret    | `A2A_APP_PASSWORD`                                                        |

**Description matters.** This is what the CS orchestrator's LLM reads
to decide whether to dispatch the current turn to your agent. Be
specific. Example:

> Use when the user asks to talk to a person, escalate to a human, get
> a ticket created in ServiceNow, or wants live support. Do NOT use
> for general IT questions — those should be answered by the parent
> agent's own knowledge.

Save, then **Publish** the parent agent. A2A wiring only takes effect
after publish.

## 5. ServiceNow-side companion BR

Add the parallel outbound BR documented in
[`servicenow/outbound_business_rule_a2a.md`](../servicenow/outbound_business_rule_a2a.md).
It posts every CSR message to `https://<your-host>/api/sn-webhook` with
header `X-Webhook-Secret: <SN_WEBHOOK_SECRET>`. The A2A agent silently
no-ops on conversations it doesn't own, so it's safe to leave the
original web-channel BR enabled at the same time.

## 6. Smoke test

1. Open the CS agent in Teams (or in CS test pane).
2. Say "hi" — expect a reply from the parent CS agent.
3. Say "I need to talk to a person." The orchestrator should dispatch
   to the A2A agent. Expected reply: confirmation that a ServiceNow
   chat has been opened, with the IMS#.
4. In ServiceNow SOW, accept the work item. Type a reply.
5. The reply should appear in the same Teams conversation, rendered
   under the CS agent's name (because we POST it back through the
   signed `serviceUrl` — CS delivers it as if it came from itself).

## Behavior notes

### Synchronous reply + proactive push

The A2A protocol is request/response: each `/api/messages` POST gets
one synchronous reply. But ServiceNow rep replies arrive
asynchronously (whenever the CSR types). To deliver them without
making the user send another turn, the agent records the signed
`serviceUrl` from each inbound activity, then on every `/api/sn-webhook`
event POSTs back to that URL directly. CS accepts the HMAC-signed
proactive push and renders it as a new bot message.

### SDK quirk: empty 200 ack

CS's A2A external-agent endpoint replies to our proactive POST with
an empty `200 OK` body and no `Content-Type` header. The default
`microsoft-agents-*` connector tries to JSON-decode the body and
raises `aiohttp.ContentTypeError`. We monkey-patch the connector's
response handler to tolerate empty 200s. See `_patch_mcs_connector()`
in [`teams_a2a/app.py`](../teams_a2a/app.py).

### User identity

CS sends the user's email in `from.email` (and sometimes
`channelData.<key>`). The agent resolves it to a `sys_user.sys_id`
via the same SN Table API call the web channel uses; falls back to
`SN_DEFAULT_REQUESTOR_SYSID` if unmatched.

### Why this isn't a Bot Framework "skill"

The classic BF skill protocol (`/skill-manifest.json`,
`continue_conversation_with_claims`, `audience=<csAppId>`) requires a
classic AAD app registration on the *parent* CS agent — which Entra
Agent ID agents don't have. The A2A connector is the supported
replacement; see [`v3-skill-pattern-rejected.md`](v3-skill-pattern-rejected.md)
for the full decision record.
