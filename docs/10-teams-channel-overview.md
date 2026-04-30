# 10 - Teams channel overview

This document is the Teams analogue of [`01-architecture.md`](01-architecture.md).
The web channel architecture in `01` is unchanged; everything below is
additive.

## Three channels in this repo

The repo ships **one web channel** and **two Teams channels**. They all
talk to the same bridge (`bridge/`) and the same ServiceNow setup
(`servicenow/`). Pick whichever fits your front end.

| Folder | Channel | SDK / pattern | Setup doc |
| ------ | ------- | ------------- | --------- |
| [`web/`](../web/) + [`bridge/`](../bridge/) | Browser webchat | BotFramework WebChat against Copilot Studio Direct Line | [`05-browser-webchat.md`](05-browser-webchat.md) |
| [`teams_agent/`](../teams_agent/) | Microsoft Teams (1:1) | **M365 Agents SDK**, Genesys-style server-side handoff | [`13-teams-agent-setup.md`](13-teams-agent-setup.md) |
| [`teams_a2a/`](../teams_a2a/) | Microsoft Teams (1:1) via Copilot Studio | **M365 Agents SDK** registered with Copilot Studio as an **A2A agent** ("Add an agent" connector) | [`14-teams-a2a-setup.md`](14-teams-a2a-setup.md) |

> A previous `teams_bot/` folder built on `botbuilder-python` 4.17.x
> was removed in v3 because Microsoft put that SDK into maintenance
> mode and replaced it with `microsoft-agents-*`. Both surviving Teams
> channels now use the supported SDK. See "Why `teams_bot/` was
> dropped" below for the rationale.

## Why two Teams channels?

They solve the same business problem — "let a Teams user chat with the
CS agent and hand off to a live ServiceNow CSR" — at two different
*integration seams* with Copilot Studio:

- **`teams_agent/` is Teams-first.** The Teams app **is** the bot.
  Copilot Studio sits *behind* it on Direct Line; the agent process
  proxies turns, watches for the escalate event, and pushes rep
  replies into the same Teams 1:1 chat via
  `adapter.continue_conversation`.

- **`teams_a2a/` is Copilot-Studio-first.** The user already chats
  with a Copilot Studio agent (in Teams via CS's own channel, or
  anywhere CS is reachable). When the CS orchestrator decides the
  user wants a human, it dispatches the activity to our process via
  the **A2A "Add an agent → Microsoft 365 Agents SDK"** connector. We
  reply synchronously, and proactively push later rep messages to the
  signed `serviceUrl` on the activity.

Same backend, same SN wiring, same state machine — different end of
the hose.

## Channel-by-channel architecture

### Web (`web/` + `bridge/`)

```
+-----------------+       (1) Direct Line (browser)        +----------------+
|  intranet.html  |  <----------------------------------> | Copilot Studio |
|  WebChat widget |                                        | (DL channel)   |
+--------+--------+                                        +--------+-------+
         |                                                          |
         | (2) /api/servicenow/agent/escalate (HTTP from CS topic)  |
         | (3) /api/servicenow/user-message (HTTP from page JS)     |
         | (4) WebSocket / poll for rep replies                     |
         v                                                          v
+----------------------------------------------------------------------+
|                      Flask bridge (servicenow_bridge.py)             |
|   BridgeSession state (BOT -> QUEUED -> LIVE -> CLOSED)              |
|   /directline/token (mints CS DL token, binds session id as user.id) |
+--------+-------------------------------------------------------------+
         |
         | sn_open_chat / sn_send_message (Scripted REST)
         v
+----------------------+      sys_cs_message BR (async)     +-----------+
| ServiceNow AWA queue | ---------------------------------> | bridge    |
| + CSR in SOW pane    |  POST /api/servicenow/webhook      | webhook   |
+----------------------+                                    +-----------+
```

**Key property.** Single browser tab, single conversation. The page JS
flips between "talking to bot" and "talking to rep" *client-side* based
on `state` payloads pushed by the bridge. The Direct Line token's
`user.id` is the bridge's session id, so CS can pass it back through
the Escalate HTTP tool as the correlation key.

See [`05-browser-webchat.md`](05-browser-webchat.md) for the page-side
state machine.

### Teams via M365 Agents SDK (`teams_agent/`)

```
+----------------------+   (1) Teams activity   +-------------------------+
|  Teams 1:1 chat      | ---------------------> |  Azure Bot resource     |
|  "IT Helper" app     |                        |  (channel = Teams)      |
+----------+-----------+                        +------------+------------+
                                                              |
                                                (2) POST /api/messages
                                                              v
+--------------------------------------------------------------------+
| teams_agent/ (microsoft-agents-hosting-aiohttp, AgentApplication)  |
|   - per-Teams-user session via /api/teams/init-session             |
|   - DL parity client (teams_agent/dl.py) talks to CS Direct Line   |
|   - Genesys-style handoff event listener                           |
+----------+--------------------------------+------------------------+
           |                                |
   (3) DL turn                              | (4) /api/servicenow/* on bridge
           v                                v
   +----------------+               +-----------------------------+
   | Copilot Studio |               | bridge (Flask)              |
   | (DL channel)   |               | shared with web channel     |
   +----------------+               +--------------+--------------+
                                                   |
                                  rep replies / status / typing
                                                   v
                                  POST /api/teams/push (signed)
                                                   |
                                                   v
                          adapter.continue_conversation -> Teams 1:1
```

**Key property.** Teams owns the surface. Users see no sign-in card
(server-side DL token). Bridge pushes rep messages into Teams via the
agent process, which holds the AAD-issued `ConversationReference`. The
agent IS the messaging-endpoint bot for Teams; CS is invisible to the
user as a separately addressable thing.

**When you want this.** Teams should be the canonical front end and
Copilot Studio should be a backend cognition engine. The user never
needs to see "Copilot Studio" anywhere.

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

## Comparison

| Concern | `teams_agent/` (Genesys-style) | `teams_a2a/` (A2A) |
| ------- | ------------------------------ | -------------------- |
| Who owns the Teams app | Our agent (Azure Bot) | Copilot Studio (its native channel) |
| Inbound transport | Bot Framework / Teams channel | A2A connector from CS |
| Outbound to user | `adapter.continue_conversation` (Teams push) | Synchronous reply + proactive POST to signed CS `serviceUrl` |
| User identity in CS | DL token's `user.id` we mint | CS-minted; we read from `from.id` / `channelData` |
| User-visible identity | Our bot's name/avatar | CS agent's name/avatar |
| CS auth | None (server-side DL token) | Classic AAD app reg + client secret on the connector |
| Routing decision | Our agent state machine | CS orchestrator (LLM, intent-based) |
| When CS decides to dispatch | Always (we proxy every turn) | Per turn, per agent description |
| Multi-agent composition | No (we are the only bot) | Yes — CS can mix us with other A2A agents, prompts, flows |
| Sample provenance | Genesys handoff sample (.NET, ported) | Microsoft Learn: ["Add an agent → M365 Agents SDK"](https://learn.microsoft.com/en-us/microsoft-copilot-studio/configuration/add-agent-microsoft-365-agents-sdk-agent) |

## Why `teams_bot/` was dropped

`teams_bot/` was the original Bot Framework SDK
(`botbuilder-python` 4.17.x) implementation. It worked, but:

1. **SDK is in maintenance.** Microsoft's public guidance for new
   work is the M365 Agents SDK; the BF Python packages get bug fixes
   only.
2. **Async/sync mismatch.** `botbuilder-python` is async-only and
   the bridge process is sync Flask. We bridged the gap with a
   thread-pool + asyncio-loop hack in `teams_bot/runtime.py`. That
   wart is gone in `teams_agent/` because the agent runs in its own
   aiohttp process.
3. **Distribution friction.** `botframework-connector` imported
   `aiohttp` transitively without declaring it; `CloudAdapter` and
   `ConfigurationBotFrameworkAuthentication` moved out of
   `botbuilder.core` between minor releases. Bumping the SDK was
   historically painful.
4. **No first-class A2A.** The A2A "Add an agent" path that
   `teams_a2a/` uses requires the M365 Agents SDK; the BF SDK has
   no equivalent integration.
5. **One implementation per SDK is enough.** Once `teams_agent/`
   reached parity, keeping `teams_bot/` only added cutover knobs,
   parallel infra, and confused new contributors.

The bridge dispatcher used to support a `TEAMS_PUSH_TARGET`
(`legacy` / `agent` / `both`) flag for cutover. With `teams_bot/`
gone the dispatcher always pushes to `teams_agent/`; the env var was
removed.

## What's reused across all three channels

| Reused (no change required by channel)                          |
| --------------------------------------------------------------- |
| ServiceNow Scripted REST APIs (`open_chat`, `send_message`)     |
| Outbound Business Rule + webhook contract                       |
| `_escalate_session`, `BridgeSession` core fields, recent-text echo dedupe |
| `/api/servicenow/agent/escalate`, `/user-message`, `/webhook`   |

## State machine (same for all three channels)

| State    | What the user sees                                       | Where their input goes                |
| -------- | -------------------------------------------------------- | ------------------------------------- |
| `bot`    | Replies authored by the Copilot Studio agent.            | Direct Line (web, `teams_agent/`) or A2A inbound (`teams_a2a/`) |
| `queued` | "Connecting an agent..." + IMS#                          | Suppressed (canned reply)             |
| `live`   | "You're now chatting with `<rep>`", then plain replies prefixed with rep name | `POST /api/servicenow/user-message` |
| `closed` | "This chat has ended." Type **new** to reset.            | Suppressed                            |

## Identity

All three channels resolve the user to a `sys_user.sys_id` via the SN
Table API
(`/api/now/table/sys_user?sysparm_query=email=<x>^ORuser_name=<x>`).

- **Web:** the page JS supplies the email at `init-session` time (or a
  test email is hard-coded for local dev).
- **`teams_agent/`:** the AAD `userPrincipalName` from the Teams
  activity's `from.aadObjectId` is resolved.
- **`teams_a2a/`:** the email from `from.email` / `channelData` on
  the inbound A2A activity is resolved.

In all three cases, if no SN user matches, the bridge falls back to a
configurable `SN_DEFAULT_REQUESTOR_SYSID` so the chat still gets
opened.
