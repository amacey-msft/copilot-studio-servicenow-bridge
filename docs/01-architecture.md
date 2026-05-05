# 01 — Architecture

## The flow

```
                              ┌────────────────────────────┐
                              │   Browser (your web app)   │
                              │   ─ embeds Copilot Studio  │
                              │     Web Chat (Direct Line) │
                              └─────────────┬──────────────┘
                                            │
                          (1) user types    │
                                            ▼
                              ┌────────────────────────────┐
                              │   Copilot Studio agent     │
                              │   (your existing topics)   │
                              └─────────────┬──────────────┘
                                            │
                  (2) Escalate topic        │
                      delegates to teams_a2a│
                      Connected Agent       │
                      → POST /api/messages  │
                                            ▼
                ┌──────────────────────────────────────────────┐
                │  Flask bridge   (this repo: bridge/)        │
                │   POST /api/servicenow/agent/escalate        │
                │   POST /api/servicenow/user-message          │
                │   POST /api/servicenow/webhook    (from SN)  │
                │    WS  /ws/intranet/<sid>         (to UI)    │
                └─────────────┬──────────────────┬─────────────┘
                              │                  ▲
              (3) open chat   │                  │ (6) agent reply
              (4) user msg    │                  │     event
                              ▼                  │
                ┌──────────────────────────────────────────────┐
                │  ServiceNow                                  │
                │   Scripted REST  /intranet_bridge/open_chat  │
                │   Scripted REST  /intranet_bridge/send_msg   │
                │                                              │
                │   sys_cs_conversation                        │
                │   interaction (IMS####)                      │
                │   sys_cs_session  + sys_cs_session_binding   │
                │   awa_work_item   ───► AWA routes to Alex    │
                │                                              │
                │   Business Rule on sys_cs_message            │
                │       (after insert, async)                  │
                │       direction=outbound, is_agent=true      │
                │       → POST to bridge webhook   (6)         │
                └──────────────────────────────────────────────┘
                              ▲
              (5) agent types │
                  in SOW pane │
                ┌─────────────┴────────────────────────────────┐
                │  ServiceNow Agent Workspace (Alex)           │
                └──────────────────────────────────────────────┘
```

## The four moving parts

### 1. Browser webchat (`web/`)

Standard Copilot Studio Web Chat (`window.WebChat.createDirectLine`) plus a
small state machine that switches between three modes:

| Mode      | What the user sees        | Where their input goes                      |
| --------- | ------------------------- | ------------------------------------------- |
| `bot`     | Chat with Assistant       | Copilot Studio (Direct Line)                |
| `queued`  | "Connecting an agent…"    | (input disabled)                            |
| `live`    | Chat with `<Rep Name>`    | `POST /api/servicenow/user-message`         |
| `closed`  | "This chat has ended."    | (re-enable bot or refresh)                  |

The browser receives mode transitions and live-agent messages over a
WebSocket (`/ws/intranet/<sid>`) with HTTP polling
(`/api/servicenow/poll/<sid>`) as a fallback for hosts where the WS can't
get through.

### 2. Copilot Studio (existing agent + Escalate topic + Connected Agent)

You register **`teams_a2a`** as a Connected Agent on the agent
(“Add an agent → A2A (Bring your own)”, endpoint = the
`ca-cps-sn-skill` ACA app, no auth). The system Escalate topic is then
edited to delegate to that Connected Agent. The orchestrator routes
“talk to a person”-style turns to it based on the agent’s description.
`teams_a2a` is what actually calls the bridge — the CS agent never
calls the bridge directly any more.

The two CS agents in this repo:

| Agent | Auth | Channel |
| ----- | ---- | ------- |
| `awm_contosoithelp` | None (anonymous DL) | Web (intranet kiosk) |
| `crd20_itHelpDeskTriageAssistant` | Entra Agent ID | Teams (CS native channel) |

Both point at the same `teams_a2a` Connected Agent. See
[`04-copilot-studio.md`](04-copilot-studio.md) for the per-agent setup.

### 3. Flask bridge (`bridge/`)

A small Python service. It owns:

- An in-memory `BridgeSession` keyed by a session id (`sid`).
- HTTP endpoints for the browser (`/init-session`, `/user-message`, `/poll`)
  and for ServiceNow (`/webhook`).
- A WS push channel for the browser (`/ws/intranet/<sid>`).
- Outbound calls to the two Scripted REST endpoints in ServiceNow
  (`/open_chat`, `/send_message`).

The bridge **never** talks to ServiceNow's Table API directly — every
call goes through the Scripted REST API documented in
[`02-servicenow-setup.md`](02-servicenow-setup.md).

### 4. ServiceNow (configured via the web UI)

You add:

- A service account (`intranet.bridge`) and a custom role
  (`x_intranet_bridge_caller`).
- A custom string column `u_bridge_session_id` on `interaction`.
- A Scripted REST API `intranet_bridge` with two resources, `/open_chat`
  and `/send_message`.
- One Business Rule on `sys_cs_message` that POSTs outbound agent messages
  back to the bridge.
- Two `sys_properties` carrying the webhook URL and shared secret.

That's it on the SN side — no plugins beyond what comes with a CSM-flavoured
PDI, no studios, no managed updates.

## Why each piece exists (the short version)

| Piece                                                  | Why you can't skip it |
| ------------------------------------------------------ | --------------------- |
| Scripted REST `/open_chat`                             | AWA only routes interactions linked to a `sys_cs_conversation`; that table has no public create ACL. The script runs server-side and uses `sn_cs.VASystemObject.createConversation()` which populates the binary `context` blob downstream APIs require. |
| Scripted REST `/send_message`                          | Raw `sys_cs_message` inserts persist the row but never publish on AMB, so the agent's pane stays empty. `sn_cs.AgentChatScriptObject.send()` is the supported API that does insert + AMB publish. |
| `u_bridge_session_id` column on `interaction`          | Lets the outbound Business Rule correlate an agent reply back to the right browser session. |
| Outbound Business Rule on `sys_cs_message`             | Pushes agent replies to the bridge in near real-time. Filters on `direction=outbound^is_agent=true` plus a guard on `interaction.u_bridge_session_id` so OOB chats are ignored. |
| Custom role `x_intranet_bridge_caller`                 | Lets you grant exactly the permission the bridge needs (call the Scripted REST endpoints) without giving it `admin`. |
| Service account `intranet.bridge`                      | Runs the Scripted REST resources. Owns the basic-auth credentials the bridge presents. |
| Bridge `BridgeSession` store                           | Holds the mapping `bridge_session_id ↔ conversation_sys_id ↔ interaction_sys_id`, plus state for status push. In-memory in the reference implementation; swap to Redis for production. |

## Threat model (very short)

- **Agent → bridge**: protected by `X-Agent-Secret` header. Rotate.
- **Browser → bridge**: protected by ownership of the `bridge_session_id`
  (allocated by the bridge on page load, used as Direct Line `User.Id`).
- **ServiceNow → bridge**: protected by `X-Bridge-Secret` header sourced from
  a SN `sys_property`. Rotate.
- **Bridge → ServiceNow**: basic auth as `intranet.bridge`. Use a strong
  password and treat the Scripted REST resources as security-sensitive.

See [`09-production-hardening.md`](09-production-hardening.md) before
shipping.
