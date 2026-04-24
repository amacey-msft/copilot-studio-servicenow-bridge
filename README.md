# Copilot Studio ↔ ServiceNow Live Agent Bridge

A reference implementation that lets a **Microsoft Copilot Studio** webchat
hand a conversation off to a real **ServiceNow Agent Workspace** chat session
— bidirectionally, with no page refresh and no context switch for the user.

```
Browser webchat ──► Copilot Studio ──► (handoff) ──► Flask bridge ──► ServiceNow
                                                                         │
                Browser webchat ◄──── (live messages both ways) ─────────┘
                               (Service Now Agent Workspace pane)
```

This repo packages the result of figuring out — the hard way — exactly which
ServiceNow tables, APIs, and configuration steps are required to make
Advanced Work Assignment (AWA) actually route a chat that originated outside
ServiceNow, and how to push messages into a live chat so the agent's pane
updates instantly.

## What's in here

| Path                                            | Purpose                                                      |
| ----------------------------------------------- | ------------------------------------------------------------ |
| [`docs/01-architecture.md`](docs/01-architecture.md)             | Architecture, data flow, why each piece exists.              |
| [`docs/02-servicenow-setup.md`](docs/02-servicenow-setup.md)     | Step-by-step ServiceNow web UI setup. The big one.           |
| [`docs/03-bridge-backend.md`](docs/03-bridge-backend.md)         | Flask bridge: env vars, endpoints, deployment.               |
| [`docs/04-copilot-studio.md`](docs/04-copilot-studio.md)         | Wiring the Copilot Studio Escalate topic to call the bridge. |
| [`docs/05-browser-webchat.md`](docs/05-browser-webchat.md)       | Browser-side state machine and reference snippets.           |
| [`docs/06-end-to-end-test.md`](docs/06-end-to-end-test.md)       | Verification probe and what success looks like.              |
| [`docs/07-troubleshooting.md`](docs/07-troubleshooting.md)       | Symptom → cause → fix table built from the dead ends.        |
| [`docs/08-api-reference.md`](docs/08-api-reference.md)           | Every ServiceNow API and table touched, with rationale.      |
| [`docs/09-production-hardening.md`](docs/09-production-hardening.md) | Checklist before promoting beyond a dev PDI.             |
| `servicenow/`                                   | Three Scripted REST scripts to paste into ServiceNow.        |
| `bridge/`                                       | Reference Flask bridge (`servicenow_bridge.py`).             |
| `web/`                                          | Reference HTML page with the bot ↔ live agent state machine. |

## Quick start

1. Read [`docs/01-architecture.md`](docs/01-architecture.md) (5 min).
2. Work through [`docs/02-servicenow-setup.md`](docs/02-servicenow-setup.md)
   in your dev PDI. End state: `tools/probe_open_chat.ps1` returns a routed
   `IMS#######` and your test agent gets a chat invite in their Inbox.
3. Run the bridge ([`docs/03-bridge-backend.md`](docs/03-bridge-backend.md))
   locally, exposed via a tunnel.
4. Configure your Copilot Studio Escalate topic
   ([`docs/04-copilot-studio.md`](docs/04-copilot-studio.md)) to POST to the
   bridge.
5. Run the end-to-end probe in [`docs/06-end-to-end-test.md`](docs/06-end-to-end-test.md).

## Important: the bridge must be reachable from ServiceNow and from Copilot Studio

ServiceNow's outbound Business Rule and Copilot Studio's HTTP action both
call the bridge over the public internet. Localhost won't work. You need
**one HTTPS URL** (call it `BRIDGE_PUBLIC_URL`) that points at the bridge,
and you set it in three places:

| Where                                  | What                                                     |
| -------------------------------------- | -------------------------------------------------------- |
| ServiceNow `sys_property` `intranet_bridge.outbound_webhook_url` | `<BRIDGE_PUBLIC_URL>/api/servicenow/webhook` |
| Copilot Studio Escalate topic HTTP action URL                    | `<BRIDGE_PUBLIC_URL>/api/servicenow/agent/escalate` |
| The browser (intranet page)                                      | Served from the same origin so relative paths work, **or** updated to use the absolute `<BRIDGE_PUBLIC_URL>`. |

For local development a VS Code Dev Tunnel works fine. Helper
scripts are checked in under [`scripts/`](scripts/devtunnel-README.md):

```powershell
# 1. Start the bridge container (publishes container :5000 on host :5001)
docker compose -f bridge/docker-compose.yml up -d --build

# 2. Create a persistent named tunnel (one-time)
.\scripts\devtunnel-create.ps1

# 3. Host it (Ctrl+C to stop; the tunnel itself persists)
.\scripts\devtunnel-host.ps1
```

Once the tunnel is up, set `BRIDGE_PUBLIC_URL` in `bridge/.env` to its
HTTPS URL (e.g. `https://<id>-5001.use.devtunnels.ms`) and run:

```powershell
# Pushes BRIDGE_PUBLIC_URL into ServiceNow's sys_property and into the
# Copilot Studio HTTP-tool botcomponents in one shot.
.\scripts\sync-bridge-url.ps1
```

That eliminates the manual UI edits whenever the tunnel URL changes (or
whenever a different developer clones the repo and starts their own
tunnel).

Or do it by hand:

```powershell
devtunnel host --port-numbers 5001 --allow-anonymous
```

Copy the `https://<id>-5001.use.devtunnels.ms` URL it prints into the
table above. **Tunnel URLs change when you restart the tunnel** unless
you used a persistent named tunnel (the helper script does this for
you) — when they do change, update the ServiceNow sys_property and the
Copilot Studio HTTP action URL.

For anything beyond local dev, host the bridge on a real platform
(Azure App Service, Azure Container Apps, Cloud Run, Fly.io, etc.) and
use that platform's HTTPS URL.

If anything goes sideways, jump to
[`docs/07-troubleshooting.md`](docs/07-troubleshooting.md) — every entry in
that table is a real failure mode that cost time to diagnose.

## Why this repo exists

The official ServiceNow guidance for "external chat → AWA-routable
interaction" is incomplete. Specifically:

- AWA routing only fires when an `interaction` is linked to a
  `sys_cs_conversation`. Direct Table API inserts on `interaction` produce
  IMS records that **never get routed**.
- `sys_cs_conversation` has zero out-of-the-box create ACLs, so external
  callers cannot insert into it directly.
- The conversation must be created via `sn_cs.VASystemObject.createConversation()`
  (which populates a binary `context` blob); raw GlideRecord inserts produce
  conversations whose first agent reply throws a `NullPointerException` in
  `ConversationContext.getBrandingKey()`.
- Pushing a consumer message into a live chat via raw `sys_cs_message` insert
  persists the row but **never publishes on AMB**, so the agent's pane stays
  empty. The supported API is `sn_cs.AgentChatScriptObject.send()`.

The Scripted REST scripts in [`servicenow/`](servicenow/) encapsulate every
one of those discoveries so you don't have to make them again.

## Verified against

- ServiceNow Yokohama (PDI, April 2026)
- Microsoft Copilot Studio (April 2026)
- Direct Line Channel (Bot Framework v3)
- Python 3.12 / Flask 3.x / flask-sock
- VS Code Dev Tunnels (for local development)

## License

MIT — see [`LICENSE`](LICENSE).
