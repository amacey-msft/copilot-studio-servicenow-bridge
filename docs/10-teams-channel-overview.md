# 10 - Teams channel overview

This document is the Teams-branch analogue of [`01-architecture.md`](01-architecture.md).
The web channel architecture is unchanged; this is an additive front-end.

## Two implementations

There are **two parallel Teams implementations** in this repo. Pick one.

| Folder | SDK | Setup doc | Status |
| ------ | --- | --------- | ------ |
| [`teams_agent/`](../teams_agent/) | **M365 Agents SDK** (`microsoft-agents-*` 0.9.x) | [`13-teams-agent-setup.md`](13-teams-agent-setup.md) | **Recommended** |
| [`teams_bot/`](../teams_bot/) | Bot Framework (`botbuilder-python` 4.17.x) | [`11-teams-bot-setup.md`](11-teams-bot-setup.md) | Deprecated SDK; kept for rollback |

The **bridge endpoints** and **ServiceNow setup** are identical for both.
Only the Teams-facing process and its Azure Bot resource differ. The
flow diagram below applies to either implementation; substitute
`teams_bot/` ↔ `teams_agent/` in the box names.

A bridge env var `TEAMS_PUSH_TARGET` (`legacy` / `agent` / `both`)
chooses which implementation receives the proactive pushes from
ServiceNow webhooks, so cutover is a single env flip.

## Bot Framework SDK vs M365 Agents SDK — what actually differs

This is the question to answer before picking. Both end up serving
`/api/messages` to Teams; what changes is the SDK around it.

### Bot Framework SDK (`teams_bot/`, `botbuilder-python` 4.17.x)

- **Provenance.** Started life as Microsoft Bot Framework v3 (~2016),
  morphed into v4 (~2018). The current Python package is in
  *maintenance only*; Microsoft's public guidance points new work at the
  M365 Agents SDK.
- **Activity model.** `BotFrameworkAdapter` (older) or `CloudAdapter`
  (newer) wraps every inbound payload into a `TurnContext`. You subclass
  `ActivityHandler` / `TeamsActivityHandler` and override
  `on_message_activity`, `on_event_activity`, etc. Routing is by
  *activity type*; you write the dispatch.
- **State.** Pluggable `Storage` + `BotState` + `StatePropertyAccessor`
  pyramid. Powerful but verbose. We bypass it in `teams_bot/` and store
  per-session state in our own bridge dict; the BF state objects exist
  only for `ConversationReference` capture.
- **Proactive push.** `adapter.continue_conversation(reference, callback)`.
  Requires you to keep a `ConversationReference` per user.
- **Auth.** `MicrosoftAppCredentials` + an Azure Bot resource. JWT
  validation is done by the adapter on inbound, app id/password on
  outbound. SingleTenant / MultiTenant / UserAssignedMSI app types.
- **Web host.** Anything; we use Flask + `flask_sock` + a thread-pool
  bridge to the SDK's asyncio loop in `teams_bot/runtime.py`. That bridge
  exists *because* `botbuilder-python` is async-only and the rest of the
  bridge is sync Flask.
- **Distribution friction.** `botframework-connector` imports `aiohttp`
  transitively but doesn't declare it as a dependency; `CloudAdapter` and
  `ConfigurationBotFrameworkAuthentication` moved out of `botbuilder.core`
  into `botbuilder.integration.aiohttp` in 4.17 without a deprecation
  shim. Bumping minor versions has historically been painful.
- **Copilot Studio integration.** Done by hand via raw Direct Line
  REST/WebSocket calls in `teams_bot/directline.py`. SDK has no
  first-class concept of "talk to a CS agent."

### M365 Agents SDK (`teams_agent/`, `microsoft-agents-*` 0.9.x)

- **Provenance.** New in 2025; replacement for Bot Framework SDK.
  Repository: <https://github.com/microsoft/Agents>. Python, .NET, JS
  all in active development. Designed around the *agent* (single-purpose,
  may delegate) rather than the *bot* (multi-skill rule engine).
- **Activity model.** `AgentApplication` with **decorator routing**:
  `@app.activity("message")`, `@app.event("handoff.initiate")`,
  `@app.message("/reset")`. Closer to FastAPI than to old BF. The
  underlying transport is still Bot Framework / Azure Bot under the hood
  — the *channel* is the same; only the framing changed.
- **State.** First-class `TurnState` you read/write directly; conversation
  state is a normal dict scoped to the activity. Less ceremony.
- **Proactive push.** `app.adapter.continue_conversation(reference, ...)`
  — same primitive as BF, just exposed off the app.
- **Auth.** `MsalConnectionManager` + Azure Bot resource (or, for some
  scenarios, no Azure Bot at all — the SDK has a "developer" auth path).
  Same `SingleTenant` / `MultiTenant` / `UserAssignedMsi` app types.
- **Web host.** Bundled aiohttp host; we just call
  `app.run(host=..., port=3978)`. No sync/async impedance because there
  is no sync Flask in the agent process — the agent calls the bridge
  over HTTP exactly like any other client.
- **Copilot Studio integration.** First-class. Two paths:
  1. **Delegated `CopilotClient`** with OBO (`microsoft-agents-copilotstudio-client`).
     User sees a sign-in card; the SDK exchanges tokens for you. This is
     the path the .NET GenesysHandoff sample uses.
  2. **Server-side Direct Line** (what we do in `teams_agent/dl.py`).
     The bridge mints a CS Direct Line token from a server-side secret;
     the agent talks Direct Line directly. **No user sign-in card.**
     We chose this because it preserves the legacy `teams_bot/` UX and
     because S2S CS auth isn't yet shipped on the SDK as of 0.9.0.
- **Distribution.** Single coherent set of packages
  (`microsoft-agents-hosting-aiohttp`, `microsoft-agents-authorization-msal`,
  `microsoft-agents-copilotstudio-client`). Less version-skew risk than BF.

### Side-by-side

| Concern | `teams_bot/` (BF SDK) | `teams_agent/` (Agents SDK) |
| ------- | --------------------- | --------------------------- |
| Routing | `ActivityHandler` overrides | `@app.activity` / `@app.event` decorators |
| Async story | async-only SDK in a sync Flask host (thread bridge) | uniformly async (aiohttp) |
| State | pluggable but verbose `BotState` | direct `TurnState` dict |
| CS integration | hand-rolled Direct Line REST | first-class via `CopilotClient` *or* DL parity |
| User sign-in | none (server-side DL token) | none in our DL-parity build (default to OBO if you use `CopilotClient`) |
| Push to user | `adapter.continue_conversation` | `app.adapter.continue_conversation` |
| Manifest | unchanged Teams app manifest | unchanged Teams app manifest |
| Future-proofing | maintenance mode | active investment path |

### What's actually better in the Agents SDK build

1. **One async loop, end to end.** Removes the
   `runtime.process_activity_sync` thread-bridge wart in `teams_bot/`.
   That wart was needed because Flask is sync and the SDK is async; in
   `teams_agent/` the agent is its own aiohttp process so there's
   nothing to bridge. Easier to reason about, no mystery hangs from
   queueing a coroutine onto the wrong loop.
2. **Decorator routing.** `@app.activity("message")` is impossible to
   silently miss; the legacy `teams_bot/relay.py` had a regex
   catch-all (`@app.message(re.compile(".*"))`) that quietly dropped any
   message containing a newline because Python `.` doesn't match `\n`.
   See [`07-troubleshooting.md`](07-troubleshooting.md). This class of
   bug is structurally harder to write in the new SDK.
3. **First-class Copilot Studio path.** `CopilotClient` exists; if you
   ever want to switch from server-side DL to delegated OBO sign-in the
   plumbing is already in `teams_agent/cs_client.py` — just stop calling
   `dl.py` and route through `CopilotClient`.
4. **Cleaner package surface.** No undeclared `aiohttp` transitive,
   no `botbuilder.integration.aiohttp` mystery move; everything lives
   under `microsoft-agents-*`. Easier to pin / upgrade.
5. **Forward investment.** Microsoft's public roadmap puts new features
   (multi-agent orchestration, A2A, MCP integration) in the Agents SDK.
   Bot Framework gets bug fixes only.

### What's the same (good and bad)

- **Same Azure Bot resource shape** (just a new instance). Same
  channel-add command (`az bot msteams create`). Same Teams app manifest
  schema. Same JWT validation story.
- **Same proactive push primitive** — both call
  `adapter.continue_conversation` under the hood. Bridge code that
  POSTs to `/api/teams/push` doesn't care which SDK is on the other end.
- **Same user-visible behavior in this repo.** We deliberately kept the
  DL parity path so end users notice nothing on cutover (no sign-in
  card). `TEAMS_PUSH_TARGET=both` lets you run them in parallel.

### What's worse / where the Agents SDK still has rough edges

- **0.9.x is pre-1.0.** API surface is still settling; expect to bump
  minor versions and re-read changelogs.
- **No native server-to-server CS auth** as of 0.9.0. To call CS without
  a user sign-in we had to mint Direct Line tokens server-side via the
  bridge (`/directline/token`). That works but isn't the SDK's default
  path; the canonical samples assume OBO.
- **Sparser docs.** Bot Framework has 10 years of Stack Overflow; the
  Agents SDK does not yet.
- **Behavioral parity gaps.** A few small things (typing indicator
  cadence, Adaptive Card refresh semantics) needed manual matching to
  feel identical to the legacy bot.

### Decision matrix

| If you... | Use |
| --------- | --- |
| Are starting fresh | `teams_agent/` (Agents SDK) |
| Need a server-side-only auth flow | `teams_agent/` (DL parity) |
| Want to plug into multi-agent orchestration / A2A / MCP later | `teams_agent/` |
| Need to ship today and your CI already pins `botbuilder-python` | `teams_bot/` is fine; switch when you next touch it |
| Are doing a one-off skill dialog and don't care about CS | either; BF has more SO answers |

## The flow

```
                              +----------------------------+
                              |   Microsoft Teams (1:1)    |
                              |   "IT Helper" app          |
                              +-------------+--------------+
                                            |
                          (1) user types    |
                                            v
                              +----------------------------+
                              |  Bot Framework             |
                              |  service / adapter         |
                              +-------------+--------------+
                                            |
                  (2) POST /api/messages    |
                                            v
                +-----------------------------------------------+
                |  Flask bridge process (this repo)             |
                |    teams_bot/blueprint.py  /api/messages      |
                |    teams_bot/relay.py      TeamsActivityHandler|
                |    bridge/servicenow_bridge.py  (state)       |
                +--+----------------------+---------------------+
                   |                      |
          (3) BOT  |             (3) LIVE |
                   v                      v
        +-------------------+   +------------------------+
        | Copilot Studio    |   | ServiceNow             |
        | (Direct Line)     |   | (Scripted REST + AWA)  |
        +---------+---------+   +-----------+------------+
                  |                         ^
        (4) reply | activities              | (5) rep types in
                  v                         |     SOW pane
        +-------------------+               |
        | turn_context.send |               |
        | _activity (Teams) |               |
        +-------------------+   +-----------+------------+
                                | SN sys_cs_message BR   |
                                | -> bridge webhook      |
                                +-----------+------------+
                                            |
                  (6) adapter.continue_conversation pushes
                      rep reply / status into the SAME Teams 1:1 chat
```

## Why a custom relay bot (and not the Copilot Studio Teams channel)?

The web channel has a single browser tab where mode switches happen
client-side. To keep that single-thread feel in Teams we must push rep
replies *into the same 1:1 chat the user is already in*. Microsoft offers
exactly one ToS-compliant way to do that from a backend:

> **Bot Framework `adapter.continue_conversation()`** with a stored
> `ConversationReference`. This is what `teams_bot/push.py` calls.

Other options were considered and rejected:

- **Copilot Studio's native Teams channel.** Has no proactive backend hook;
  the bridge cannot push a SN rep reply into the chat.
- **Microsoft Graph `POST /chats/{id}/messages`.** Microsoft's docs
  explicitly call this out as *"a violation of the terms of use to use
  Microsoft Teams as a log file"* and the only application permission
  (`Teamwork.Migrate.All`) is intended for migration, not live chat.

So this branch publishes a custom Bot Framework bot as the Teams app, and
that bot is the single user-facing surface. Copilot Studio sits behind it
on Direct Line.

## State machine (mirrors the web flow)

| State    | What the user sees in Teams                       | Where their input goes                |
| -------- | ------------------------------------------------- | ------------------------------------- |
| `bot`    | Replies authored by the Copilot Studio agent.     | Direct Line via `teams_bot/directline.py`. |
| `queued` | Adaptive Card "Connecting an agent..." with IMS#. | Suppressed (canned reply).            |
| `live`   | Adaptive Card "You're now chatting with `<rep>`", then plain text replies prefixed with rep name. | `POST /api/servicenow/user-message` (same as web). |
| `closed` | Adaptive Card "This chat has ended." Type **new** to reset. | Suppressed.                           |

Status transitions are pushed via `adapter.continue_conversation`. Plain
rep replies and typing indicators use the same path.

## What's reused vs what's new

| Reused (no change required)                                     | New (Teams branch)                          |
| --------------------------------------------------------------- | ------------------------------------------- |
| ServiceNow Scripted REST APIs (`open_chat`, `send_message`).    | `teams_bot/relay.py` (TeamsActivityHandler).|
| Outbound Business Rule + webhook contract.                      | `teams_bot/runtime.py` (asyncio loop, adapter, sync wrappers). |
| `_escalate_session`, `BridgeSession` core fields, recent-text echo dedupe. | `teams_bot/directline.py` (per-session DL conversation, polling). |
| `/api/servicenow/agent/escalate`, `/user-message`, `/webhook`.  | `teams_bot/push.py` (Adaptive Card status + plain text + typing). |
| `/directline/token` route (relay bot calls it internally).      | `teams_bot/blueprint.py` (`/api/messages`).  |
|                                                                 | `/api/teams/init-session`, `/api/teams/reset-session` on the bridge. |
|                                                                 | `_push_to_user(...)` dispatcher.            |

## File map

| Path                                                  | Role                                                  |
| ----------------------------------------------------- | ----------------------------------------------------- |
| [`teams_bot/blueprint.py`](../teams_bot/blueprint.py) | Flask blueprint: `POST /api/messages`.                |
| [`teams_bot/relay.py`](../teams_bot/relay.py)         | TeamsActivityHandler: state-aware turn handler.       |
| [`teams_bot/runtime.py`](../teams_bot/runtime.py)     | Background asyncio loop; `process_activity_sync`, `continue_conversation_sync`. |
| [`teams_bot/directline.py`](../teams_bot/directline.py) | Per-session Copilot Studio Direct Line client.      |
| [`teams_bot/push.py`](../teams_bot/push.py)           | Outbound rep-reply / status / typing push.            |
| [`teams_bot/config.py`](../teams_bot/config.py)       | Env-derived config.                                   |
| [`teams_bot/manifest/`](../teams_bot/manifest/)       | Teams app manifest + `build.ps1` zip helper.          |
| [`bridge/servicenow_bridge.py`](../bridge/servicenow_bridge.py) | Adds `channel`, `teams_user_key`, `teams_conversation_reference` to `BridgeSession`; adds `_push_to_user` dispatcher; adds `/api/teams/{init,reset}-session`. |
| [`bridge/app.py`](../bridge/app.py)                   | Registers the Teams blueprint when `MS_APP_ID` is set. |

## Identity

The relay bot resolves the AAD `userPrincipalName`/email to a
`sys_user.sys_id` via the SN Table API (`/api/now/table/sys_user?sysparm_query=email=<x>^ORuser_name=<x>`).
The result is cached per process for the lifetime of the bridge. On no-match
we fall back to `SN_DEFAULT_USER_SYS_ID` so the demo flow keeps working.

The `intranet.bridge` service account therefore needs read access to
`sys_user`. The simplest dev grant: extend the `x_intranet_bridge_caller`
role with a read ACL on `sys_user`. For production, use a dedicated
read-only account or OAuth.

## Security boundary

| Channel                                  | Trust check                                                            |
| ---------------------------------------- | ---------------------------------------------------------------------- |
| Teams -> bridge (`/api/messages`)        | Bot Framework JWT validated by `CloudAdapter`.                         |
| Bridge -> Teams (`continue_conversation`) | App password / single-tenant secret on the Azure Bot resource.         |
| Bridge -> ServiceNow                     | Existing basic auth as `intranet.bridge`.                              |
| ServiceNow -> bridge                     | Existing `X-Bridge-Secret` header.                                     |
| Copilot Studio -> bridge                 | Existing `X-Agent-Secret` header (only used if you keep the HTTP-action escalation; the `handoff.initiate` event path is also wired in `relay.py`). |

## Out of scope for this branch

- File attachments user <-> rep. Plumbing point is in `relay.py` and
  `push.py`; expand when needed. SN side will need a `sn_upload_attachment`
  scripted REST resource.
- End-of-chat survey.
- Group / channel scope. Manifest currently ships `personal` only.
