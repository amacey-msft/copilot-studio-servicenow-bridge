# 10 - Teams channel overview

This document is the Teams analogue of [`01-architecture.md`](01-architecture.md).
The web channel architecture in `01` is unchanged; everything below is
additive.

> **Update 2026-05-04:** the web channel and the Teams channel now
> share the **same Copilot Studio handoff path**. The browser reaches
> Copilot Studio via the bridge's Direct Line token relay; Teams
> reaches it via CS's native Teams channel. Either way, escalation
> runs through the **Connected Agent** ("ServiceNow Live Agent",
> A2A) that wraps `teams_a2a/api/messages`. The legacy Bot Framework
> Skill path was removed — see
> [`v3-skill-pattern-rejected.md`](v3-skill-pattern-rejected.md) —
> and the `teams_agent/` Genesys-style relay was removed once the
> Connected Agent reached parity (see "Why `teams_agent/` was
> dropped" below).

## Two channels in this repo

The repo ships **one web channel** and **one Teams channel**. They
both talk to the same bridge (`bridge/`) and the same ServiceNow
setup (`servicenow/`).

| Folder | Channel | SDK / pattern | Setup doc |
| ------ | ------- | ------------- | --------- |
| [`web/`](../web/) + [`bridge/`](../bridge/) | Browser webchat | BotFramework WebChat against Copilot Studio Direct Line (CS agent `awm_contosoithelp`) | [`05-browser-webchat.md`](05-browser-webchat.md) |
| [`teams_a2a/`](../teams_a2a/) | Microsoft Teams (1:1) via Copilot Studio (CS agent `crd20_itHelpDeskTriageAssistant`); also serves the web channel as a Connected Agent | **M365 Agents SDK** registered with Copilot Studio as an **A2A agent** ("Add an agent" connector) | [`14-teams-a2a-setup.md`](14-teams-a2a-setup.md) |

> Two predecessor folders were removed in earlier cleanups:
> - `teams_bot/` (botbuilder-python 4.17.x) — see "Why `teams_bot/`
>   was dropped" below.
> - `teams_agent/` (M365 Agents SDK Genesys-style relay) — see "Why
>   `teams_agent/` was dropped" below.

### Deployment topology

| Component | Hosting | Notes |
| --------- | ------- | ----- |
| `bridge/` | **Azure Container Apps** — `ca-cps-bridge` in `cae-cpv` (`rg-cpv-aca`) | Stable HTTPS endpoint reached by SN outbound BR, browser DL token relay, and `teams_a2a` push-back. Pinned `min=max=1` (in-memory session map). See [`03-bridge-backend.md`](03-bridge-backend.md). |
| `teams_a2a/` | **Azure Container Apps** — `ca-cps-sn-skill` in `cae-cpv` | Name is historical from the rejected v3 skill spike; kept stable so the CS A2A "Add an agent" registration doesn't have to be reissued. Renaming tracked as a follow-up. |
| Copilot Studio agent (web) | Microsoft-hosted (Power Platform) | `awm_contosoithelp` — anonymous DL token, serves the browser kiosk via the bridge's `/directline/token` proxy. |
| Copilot Studio agent (Teams) | Microsoft-hosted (Power Platform) | `crd20_itHelpDeskTriageAssistant` — Entra Agent ID auth, serves the Teams channel natively. |
| Connected Agent on both | n/a (registration) | `teams_a2a` (the `ca-cps-sn-skill` ACA app) is registered as an A2A Connected Agent on **both** CS agents and owns the live-chat handoff for both surfaces. |

## How the two channels relate

Both channels solve the same business problem — "let a user chat with
the CS agent and hand off to a live ServiceNow CSR" — and they share
the same handoff backend (`teams_a2a/`). They differ only in which CS
surface the user starts in:

- **Web (`web/` + `bridge/`):** browser kiosk talks to CS agent
  `awm_contosoithelp` via the bridge's DL token relay. CS routes
  escalations to the Connected Agent.
- **Teams (`teams_a2a/`):** Teams 1:1 chat talks to CS agent
  `crd20_itHelpDeskTriageAssistant` via CS's native Teams channel.
  CS routes escalations to the same Connected Agent.

Same backend, same SN wiring, same state machine — different end of
the hose.

## Channel-by-channel architecture

### Web (`web/` + `bridge/`)

```
+-----------------+       (1) Direct Line (browser)        +----------------+
|  intranet.html  |  <----------------------------------> | Copilot Studio |
|  WebChat widget |                                        | awm_contoso... |
+--------+--------+                                        +--------+-------+
         ^                                                          |
         |                              (2) Escalate / "talk to     |
         |                                  a person" turn          |
         |                                  dispatched to A2A       |
         |                                  Connected Agent         |
         |                                                          v
         |                              +-----------------------------+
         |                              | teams_a2a (ca-cps-sn-skill) |
         |                              | A2A endpoint /api/messages  |
         |                              +-------+---------------------+
         |                                      |
         |                                      | (3) /api/servicenow/agent/escalate
         |                                      | (4) subsequent /user-message turns
         | (5) WebSocket / poll                 v
         |     for status only           +----------------------------+
         +------------------------------ | Flask bridge (ca-cps-bridge)|
                                         | BridgeSession (BOT -> LIVE) |
                                         | /directline/token (CS DL)   |
                                         +-------+--------------------+
                                                 |
                                                 | sn_open_chat / sn_send_message
                                                 v
+----------------------+      sys_cs_message BR (async)     +-----------+
| ServiceNow AWA queue | ---------------------------------> | bridge    |
| + CSR in SOW pane    |  POST /api/servicenow/webhook      | webhook   |
+----------------------+                                    +-----+-----+
                                                                  |
                                       proactive POST to the      |
                                       signed CS serviceUrl       v
                                                            +-----------+
                                                            | teams_a2a |
                                                            | -> CS     |
                                                            | -> user   |
                                                            +-----------+
```

**Key property.** Single browser tab, single conversation. CS owns the
full transcript; rep replies render *as the CS agent* via the Connected
Agent's proactive push. The page JS no longer flips local "live" mode
on user input — every user turn goes back through CS, which the
orchestrator routes to the Connected Agent for the duration of the
live chat. The browser still subscribes to bridge `state` events for
status-line updates ("Connecting…" / "Chatting with Alex").

The Direct Line token's `user.id` is the bridge's session id, so the
Connected Agent can correlate CS turns to a `BridgeSession`.

See [`05-browser-webchat.md`](05-browser-webchat.md) for the page-side
state machine.

### Teams via Copilot Studio A2A (`teams_a2a/`)

```
+----------------------+    (1) user chats CS agent    +--------------------+
|  Teams 1:1 chat      | ----------------------------> | Copilot Studio     |
|  (CS native channel) |                               | orchestrator       |
+----------------------+                               +---------+----------+
                                                                 |
                              (2) orchestrator routes the turn   |
                                  to the registered A2A agent    |
                                  (description-based dispatch)   |
                                                                 v
+--------------------------------------------------------------------+
| teams_a2a/ (microsoft-agents-hosting-aiohttp, AgentApplication)  |
|   POST /api/messages  -- inbound from CS A2A connector             |
|   - validates JWT (audience = A2A_APP_ID)                        |
|   - synchronous reply on the same turn                             |
|   - records signed serviceUrl for later proactive push             |
|   - calls bridge for escalate / user-message                       |
+----------+----------------------------+----------------------------+
           |                            |
           | (3) /api/servicenow/*      | (4) on rep reply, proactive POST
           v                            v   <serviceUrl>/v3/conversations/.../activities
   +----------------+         +--------------------+         (proactive push)
   | bridge (Flask) | ------> | Copilot Studio     | -----> Teams 1:1 chat
   +-------+--------+         | (delivers as bot   |        (rendered as a CS bot reply)
           ^                  |  reply to the user)|
           |                  +--------------------+
   sys_cs_message BR
   (SN -> bridge webhook -> teams_a2a /api/sn-webhook)
```

**Key property.** The user never sees our agent. They chat with the
CS agent; the orchestrator decides (based on the A2A agent's natural
language description) when to dispatch to us. Our replies render under
the CS agent's name and avatar.

**When you want this.** You already have a Copilot Studio agent users
know. You want to add ServiceNow handoff as one capability among many.
You want CS to do the routing/intent detection rather than hard-coding
it in your own bot.

## Why `teams_bot/` was dropped

`teams_bot/` was the original Bot Framework SDK
(`botbuilder-python` 4.17.x) implementation. It worked, but:

1. **SDK is in maintenance.** Microsoft's public guidance for new
   work is the M365 Agents SDK; the BF Python packages get bug fixes
   only.
2. **Async/sync mismatch.** `botbuilder-python` is async-only and
   the bridge process is sync Flask. We bridged the gap with a
   thread-pool + asyncio-loop hack in `teams_bot/runtime.py`. That
   wart is gone in the Agents SDK code because the agent runs in its
   own aiohttp process.
3. **Distribution friction.** `botframework-connector` imported
   `aiohttp` transitively without declaring it; `CloudAdapter` and
   `ConfigurationBotFrameworkAuthentication` moved out of
   `botbuilder.core` between minor releases. Bumping the SDK was
   historically painful.
4. **No first-class A2A.** The A2A "Add an agent" path that
   `teams_a2a/` uses requires the M365 Agents SDK; the BF SDK has
   no equivalent integration.

## Why `teams_agent/` was dropped

`teams_agent/` was a Genesys-style server-side relay built on the
M365 Agents SDK. The Teams app **was** the bot; it proxied every
turn to a CS Direct Line session and listened on the DL stream for
a `ServiceNowHandoff` custom event emitted by the CS Escalate topic
to trigger the SN handoff.

It was removed in v2.3 because:

1. **Custom escalate event is no longer emitted.** Once the
   Connected Agent (`teams_a2a/`) was attached to `awm_contosoithelp`,
   the CS Escalate topic redirects to that Connected Agent instead
   of emitting `ServiceNowHandoff`. The event listener in
   `teams_agent/agent.py` became dead code.
2. **Redundant with `teams_a2a/`.** Both folders implemented the
   same SN handoff with the same SDK, against the same bridge. With
   the Connected Agent as the canonical handoff seam, there is no
   reason to keep two implementations.
3. **CS-invisible UX is not required.** The original justification —
   "Teams should be the canonical front end and CS should be
   invisible" — was a preference, not a requirement. The CS native
   Teams channel + A2A Connected Agent gives users a clean Teams
   experience without a separate bot identity.

The bridge's `/api/teams/*` routes (`init-session`, `reset-session`,
`map-dl-user`), the `_push_to_teams_agent` dispatcher branch, the
`teams_user_key` / `teams_conversation_reference` / `channel`
session fields, and the `TEAMS_AGENT_PUSH_*` env vars were removed
along with the folder.

## What's reused across both channels

| Reused (no change required by channel)                          |
| --------------------------------------------------------------- |
| ServiceNow Scripted REST APIs (`open_chat`, `send_message`)     |
| Outbound Business Rule + webhook contract                       |
| `_escalate_session`, `BridgeSession` core fields, recent-text echo dedupe |
| `/api/servicenow/agent/escalate`, `/user-message`, `/webhook`   |

## State machine (same for both channels)

| State    | What the user sees                                       | Where their input goes                |
| -------- | -------------------------------------------------------- | ------------------------------------- |
| `bot`    | Replies authored by the Copilot Studio agent.            | Direct Line (web) or A2A inbound (`teams_a2a/`) |
| `queued` | "Connecting an agent..." + IMS#                          | Suppressed (canned reply)             |
| `live`   | "You're now chatting with `<rep>`", then plain replies prefixed with rep name | `POST /api/servicenow/user-message` |
| `closed` | "This chat has ended." Type **new** to reset.            | Suppressed                            |

## Identity

Both channels resolve the user to a `sys_user.sys_id` via the SN
Table API
(`/api/now/table/sys_user?sysparm_query=email=<x>^ORuser_name=<x>`).

- **Web:** the page JS supplies the email at `init-session` time (or a
  test email is hard-coded for local dev).
- **`teams_a2a/`:** the email from `from.email` / `channelData` on
  the inbound A2A activity is resolved.

In both cases, if no SN user matches, the bridge falls back to a
configurable `SN_DEFAULT_REQUESTOR_SYSID` so the chat still gets
opened.
