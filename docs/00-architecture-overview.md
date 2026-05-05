# Architecture (one-page)

This is a guided tour of the whole solution: Microsoft side, ServiceNow
side, and the bridge in the middle. Other docs go deeper; this one is
just to make the picture click.

## The 30-second pitch

> A user chats with a **Copilot Studio** agent. When the agent can't
> help, the conversation is handed off to a real **ServiceNow CSR**
> who works in their normal SN UI. Both sides keep using their native
> tools; a small **bridge** in the middle wires the two together.

There are three front-end channels (browser, Teams-as-bot, Teams-as-CS-tool)
but only **one bridge** and **one ServiceNow setup**.

## The components

### Microsoft side

| Component | What it is | What work it does |
| --- | --- | --- |
| **Copilot Studio agent** (one per channel) | The bot the user talks to. Two agents share the backend: `awm_contosoithelp` (no auth) for the **browser** channel, `crd20_itHelpDeskTriageAssistant` (Entra Agent ID) for the **Teams** channel. | Answers normal questions. The system **Escalate** topic delegates to the `teams_a2a` Connected Agent when the user asks for a human. |
| **`teams_a2a` Connected Agent** ([`teams_a2a/`](../teams_a2a/), M365 Agents SDK) | A single A2A agent registered on **both** Copilot Studio agents above. Hosted as `ca-cps-sn-skill` on Azure Container Apps. | Owns the conversation for the duration of the live chat. Calls the bridge's `/api/servicenow/agent/escalate` and `/user-message` endpoints; pushes rep replies back into CS via the signed `serviceUrl` proactive POST. |
| **Front end** (one of two) | `web/` browser page, or `teams_a2a/` (when surfaced through CS native Teams channel). | Renders the chat. Shows bot replies, then CSR replies, in the same window. |
| **Bridge** ([`bridge/`](../bridge/), Flask) | Stateful HTTP/WebSocket service. Hosted as `ca-cps-bridge` on Azure Container Apps. | The brain. Keeps a `BridgeSession` per user (state = `bot` / `queued` / `live` / `closed`). Routes messages between `teams_a2a`, the front end, and ServiceNow. |

### ServiceNow side

| Component | What it is | What work it does |
| --- | --- | --- |
| **Scripted REST API** ([`servicenow/open_chat.js`](../servicenow/open_chat.js), [`send_message.js`](../servicenow/send_message.js)) | Two custom endpoints under `/api/.../intranet_bridge/`. | `open_chat` creates a real SN live-chat conversation and queues it to AWA. `send_message` posts user turns into that conversation so the CSR sees them live. |
| **AWA queue + CSR workspace** | Out-of-box ServiceNow. | Routes the queued chat to an available CSR. CSR replies in the SOW chat pane like any other chat. |
| **Outbound Business Rule** ([`servicenow/outbound_business_rule.md`](../servicenow/outbound_business_rule.md)) | One async BR on `sys_cs_message`. | This is the only custom server-side script SN runs on its own. It fires on every CSR reply and does one job: HTTP POST that reply to the bridge's `/api/servicenow/webhook`. Without it, CSR replies stay trapped inside ServiceNow. |

That's it. **Three SN artifacts total**: 2 scripted REST resources +
1 business rule. No store apps, no plugins beyond OOB.

## End-to-end flow (the only diagram you need)

```
       Microsoft side                              ServiceNow side
       =============                               ===============

   user                                          CSR in SOW chat pane
    |                                                ^
    | 1. types                                       |
    v                                                |
  front end                                          |
    |                                                |
    | 2. DL/A2A turn                                 |
    v                                                |
  Copilot Studio  ----+                              |
    |                 |                              |
    | 3. bot reply    | 4. Escalate topic            |
    v                 |    delegates to              |
  user                |    teams_a2a Connected       |
                      |    Agent                     |
                      v                              |
                  +-----------+   POST   +---------+ |
                  | teams_a2a | -------> | bridge  | |
                  | (ACA)     |          | (Flask) | |
                  +-----------+          +---------+ |
                                          |     ^    |
                                  5./6.  |     |    |
                                  open / |     |    |
                                  msg    v     |    |
                  +-------------+   +-------------+ |
                  | SN Scripted |<--+ creates conv +-+
                  |    REST     |   + AWA work item
                  +-------------+
                          |
                          v
                  CSR claims chat
                  CSR types reply
                          |
                          v
            BR posts /api/servicenow/webhook
                          |
                          v
                  bridge -> teams_a2a -> CS -> user
```

Read it as two phases:

- **Phase A: bot mode (steps 1-3).** Pure CS; bridge and `teams_a2a`
  are idle. The user sees normal bot replies.
- **Phase B: live mode (steps 4-8).** CS Escalate → dispatches to the
  `teams_a2a` Connected Agent → `teams_a2a` opens an SN chat via the
  bridge → user turns now go to SN via `send_message` → CSR turns
  come back via the BR webhook → bridge pushes them to `teams_a2a`
  → `teams_a2a` proactively renders them inside CS.

## "Wait, what does the Business Rule actually do?"

The BR is one-way plumbing for **CSR → user** messages.

ServiceNow has no idea our bridge exists. When a CSR types a reply, SN
just inserts a row in `sys_cs_message`. The BR is a tiny script
attached to that table that says:

> "If this row is a CSR reply on a conversation that was opened by the
> bridge, HTTP POST the text to the bridge's webhook URL."

That's the entire job. The bridge does the rest (find the right user
session, push it to the browser/Teams chat).

We need this because ServiceNow's chat machinery doesn't have a
"webhook on outbound message" feature out of the box. The BR is how
we add one.

| Direction | Mechanism |
| --- | --- |
| user → CSR | Bridge calls SN scripted REST `send_message` (HTTP, sync). |
| CSR → user | SN BR calls bridge `/api/servicenow/webhook` (HTTP, async). |

## Who owns the user identity?

- **Microsoft side** identifies the user by Direct Line `user.id`
  (web) or A2A `from.id` (`teams_a2a`).
- **ServiceNow side** needs a real `sys_user.sys_id`. The bridge
  resolves the user by email at handoff time and falls back to a
  configured default if nothing matches.
- The bridge keeps both IDs glued together in the `BridgeSession`.

## Where each piece runs

| Process | Lives in | Hosts |
| --- | --- | --- |
| Copilot Studio agents | Microsoft cloud (Power Platform) | Bot logic, Escalate topic, Connected Agent registration. Two agents: `awm_contosoithelp` (web), `crd20_itHelpDeskTriageAssistant` (Teams). |
| Front end | Browser tab / Teams client | UI only. |
| Bridge | Azure Container Apps `ca-cps-bridge` | All session state, all routing. |
| `teams_a2a` Connected Agent | Azure Container Apps `ca-cps-sn-skill` | A2A endpoint registered on both CS agents. Calls the bridge over HTTP. |
| ServiceNow | Your SN instance | Scripted REST + BR + the chat itself. |

## Where to go next

- Channel-by-channel diagrams: [`10-teams-channel-overview.md`](10-teams-channel-overview.md)
- Web channel deep dive: [`01-architecture.md`](01-architecture.md), [`05-browser-webchat.md`](05-browser-webchat.md)
- ServiceNow setup: [`02-servicenow-setup.md`](02-servicenow-setup.md), [`outbound_business_rule.md`](../servicenow/outbound_business_rule.md)
- Bridge HTTP contract: [`08-api-reference.md`](08-api-reference.md)
