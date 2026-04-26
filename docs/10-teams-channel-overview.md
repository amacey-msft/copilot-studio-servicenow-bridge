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
