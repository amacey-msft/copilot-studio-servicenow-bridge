# 03 — Bridge backend (Flask reference)

The bridge is a small Flask app that mediates between the browser, Copilot
Studio, and ServiceNow. The reference implementation lives in
[`bridge/servicenow_bridge.py`](../bridge/servicenow_bridge.py).

You can re-implement it in any language/framework; the contract is what
matters.

## Environment variables

| Variable                       | Required | Purpose                                                              |
| ------------------------------ | :------: | -------------------------------------------------------------------- |
| `SN_INSTANCE`                  | ✅       | e.g. `https://dev379803.service-now.com` (no trailing slash).        |
| `SN_USER`                      | ✅       | `intranet.bridge`.                                                   |
| `SN_PASSWORD`                  | ✅       | Service account password.                                            |
| `SN_BRIDGE_API_BASE`           | ✅       | e.g. `/api/1833944/intranet_bridge` (the namespace SN assigns).      |
| `SN_DEFAULT_USER_SYS_ID`       | ✅       | `sys_user.sys_id` for the consumer until you wire real auth.         |
| `SN_DEFAULT_QUEUE_SYS_ID`      | ✅       | `awa_queue.sys_id` (e.g. `IT Help Chat`).                            |
| `SN_DEFAULT_CHANNEL_SYS_ID`    | ✅       | `awa_service_channel.sys_id` (e.g. `Chat`).                          |
| `SN_WEBHOOK_SECRET`            | ✅       | Must match `intranet_bridge.outbound_webhook_secret` in SN.          |
| `AGENT_API_SECRET`             | ✅       | Shared secret the Copilot Studio HTTP action sends in `X-Agent-Secret`. |
| `SN_REQUEST_TIMEOUT`           | optional | Seconds; default `15`.                                               |

Sample `.env`:

```bash
SN_INSTANCE=https://dev379803.service-now.com
SN_USER=intranet.bridge
SN_PASSWORD=<paste>
SN_BRIDGE_API_BASE=/api/1833944/intranet_bridge
SN_DEFAULT_USER_SYS_ID=e23081fb3b580310e4058e0f23e45a88
SN_DEFAULT_QUEUE_SYS_ID=3787b03b3b180310e4058e0f23e45ad0
SN_DEFAULT_CHANNEL_SYS_ID=27f675e3739713004a905ee515f6a7c3
SN_WEBHOOK_SECRET=<openssl rand -base64 32>
AGENT_API_SECRET=<openssl rand -base64 32>
```

## HTTP / WS surface

### Browser-facing

| Method | Path                                | Body                                                              | Returns                                              |
| ------ | ----------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------- |
| POST   | `/api/servicenow/init-session`      | `{ user_email?, user_display_name? }`                             | `{ session_id, state: "bot" }`                       |
| POST   | `/api/servicenow/escalate`          | `{ session_id, opening_message }`                                 | `{ session_id, state, interaction_number, interaction_sys_id }` |
| POST   | `/api/servicenow/user-message`      | `{ session_id, text }`                                            | `{ ok: true }`                                       |
| GET    | `/api/servicenow/poll/<sid>`        | —                                                                 | `{ state, rep_name, interaction_number, events: [] }` |
| WS     | `/ws/intranet/<sid>`                | (server pushes JSON frames)                                       | n/a                                                  |

### Copilot Studio-facing

| Method | Path                                  | Required header                | Body                                              |
| ------ | ------------------------------------- | ------------------------------ | ------------------------------------------------- |
| POST   | `/api/servicenow/agent/escalate`      | `X-Agent-Secret`               | `{ session_id, opening_message }`                 |
| POST   | `/api/servicenow/agent/create-ticket` | `X-Agent-Secret`               | `{ short_description, description?, caller_email? }` |

### ServiceNow-facing

| Method | Path                          | Required header                | Body sent by the BR                                              |
| ------ | ----------------------------- | ------------------------------ | ---------------------------------------------------------------- |
| POST   | `/api/servicenow/webhook`     | `X-Bridge-Secret`              | `{ bridge_session_id, conversation_sys_id, interaction_sys_id, interaction_number, message_sys_id, sender_sys_id, q_data_message_type, text, send_time, sys_created_on }` |

## State machine

```
                 ┌────────┐  init-session
        START ──►│  BOT   │
                 └───┬────┘
                     │ /escalate (or /agent/escalate)
                     ▼
                 ┌────────┐  webhook event=claimed (or first agent reply)
                 │ QUEUED │ ────────────────────────────────────────►
                 └───┬────┘                                          │
                     │                                               ▼
                     │                                          ┌────────┐
                     │                                          │  LIVE  │
                     │                                          └───┬────┘
                     │                                              │
                     ▼                                              │
                 ┌────────┐  webhook event=closed   ◄───────────────┘
                 │ CLOSED │
                 └────────┘
```

## Pushed event frames

The bridge pushes JSON frames to the browser over the WS and accumulates
identical frames for HTTP polling clients.

| Type        | Shape                                                            | When                                                          |
| ----------- | ---------------------------------------------------------------- | ------------------------------------------------------------- |
| `status`    | `{ type:"status", state:"queued"|"live"|"closed", rep_name? }`   | On state transitions.                                         |
| `message`   | `{ type:"message", from:"rep", rep_name, text }`                 | Every outbound agent message that the SN BR forwards.         |

The browser is expected to render `message` frames inline in the same chat
window the user was already typing into, and to switch the input box to
"talking to a person" mode on the first `status` with `state="queued"`.

## Outbound calls (bridge → ServiceNow)

```
POST {SN_INSTANCE}{SN_BRIDGE_API_BASE}/open_chat
Authorization: Basic base64(SN_USER:SN_PASSWORD)
Content-Type:  application/json

{
  "user_sys_id":       "...",
  "short_description": "...",
  "bridge_session_id": "<sid>",
  "channel_sys_id":    "...",
  "queue_sys_id":      "...",
  "first_message":     "<opening message>"
}
```

```
POST {SN_INSTANCE}{SN_BRIDGE_API_BASE}/send_message
Authorization: Basic base64(SN_USER:SN_PASSWORD)
Content-Type:  application/json

{
  "conversation_sys_id": "...",
  "user_sys_id":         "...",
  "text":                "<user message>"
}
```

## Running locally

```bash
python -m venv .venv
. .venv/Scripts/activate         # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app app:app run --port 5000 --debug
```

Or with Docker:

```bash
docker compose up -d --build
```

## Exposing it to ServiceNow during development

ServiceNow's outbound BR has to reach your bridge over the public internet.
Two easy options:

- **VS Code dev tunnels** (recommended for local dev): `Ports` panel → click
  Forward, expose port 5000, set Visibility to *Public*. Copy the
  `https://<id>-5000.use.devtunnels.ms` URL into the SN sys_property
  `intranet_bridge.outbound_webhook_url` (append `/api/servicenow/webhook`).
- **ngrok**: `ngrok http 5000`. Same idea.

For production, host the bridge behind a stable HTTPS endpoint
(App Service, Container Apps, Cloud Run, etc.) and update the sys_property
exactly once.

## Hosted on Azure Container Apps

The reference deployment of the bridge runs in
[`cae-cpv`](https://portal.azure.com) (`rg-cpv-aca`) as the container app
**`ca-cps-bridge`**, image `acrcpvb0c139ea.azurecr.io/bridge:latest`.

Deploy / update with:

```pwsh
./scripts/deploy-bridge-aca.ps1                  # full ACR build + ACA deploy
./scripts/deploy-bridge-aca.ps1 -SkipBuild       # update existing image only
```

The script reads `bridge/.env`, ships the secrets via `--secrets`, sets
the env vars listed above, runs `/healthz` against the new revision and
prints the public FQDN.

After deploy, point downstream consumers at the new FQDN:

```pwsh
# 1. Update bridge/.env
BRIDGE_PUBLIC_URL=https://ca-cps-bridge.<env-suffix>.eastus2.azurecontainerapps.io

# 2. Patch SN sys_property + any CS HTTP-tool botcomponents in one shot
./scripts/sync-bridge-url.ps1
```

### Single-replica caveat

`ca-cps-bridge` runs at `min=max=1`. The bridge keeps session state in
process memory (`BridgeSession` map), so any new revision (image push,
env change, secret rotation) drops in-flight live-chat sessions. This is
acceptable for the current demo workload but blocks horizontal scaling
and zero-downtime deploys. See **Persistence** below for the planned
fix.

## Persistence

The reference `BridgeSession` store is in-memory. That's fine for one
worker; it falls apart with multiple workers / restarts. Before going to
production, swap to:

- Redis with a TTL of a few hours per session, or
- A small relational table keyed by `bridge_session_id`.

The state you need to persist per session: `state`, `interaction_sys_id`,
`interaction_number`, `conversation_sys_id`, `sn_user_sys_id`,
`user_email`, `user_display_name`, `rep_name`. Pending events can stay
in-memory if you have a sticky load balancer; otherwise put them in Redis
streams or similar.
