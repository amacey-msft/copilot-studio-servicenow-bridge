# 12 - Teams end-to-end test

Manual recipe; assumes Phase 5 setup ([`11-teams-bot-setup.md`](11-teams-bot-setup.md))
is complete and an automated probe analogous to `tools/probe_e2e.ps1` for
the web flow has not yet been written for Teams.

## Setup checklist

- [ ] `MS_APP_ID` / `MS_APP_PASSWORD` set in `bridge/.env`.
- [ ] Bridge container restarted; logs show `[teams_bot] /api/messages endpoint registered`.
- [ ] Teams app sideloaded; bot installed in personal scope.
- [ ] A test agent is **Available** in ServiceNow with chat capacity in the
      configured queue.
- [ ] Web-flow probe (`tools/probe_e2e.ps1`) still passes (no regression).

## Steps

### 1. Welcome turn captures the conversation reference

Open the 1:1 chat with the bot. Send: `hi`.

Expected:
- Welcome message from the bot.
- Bridge logs show `init_session_teams` was called once and the session has
  `channel="teams"` and a `teams_conversation_reference` populated.

### 2. BOT mode round trip

Send: `how do I reset my password?` (or any in-scope CS question).

Expected:
- Typing indicator briefly visible.
- Plain-text reply from CS appears in the Teams chat within ~5s.

### 3. Escalation -> queued status card

Send: `talk to a human`.

Expected:
- Bridge logs show `[agent] escalate hit` (HTTP-action path) **or**
  `handoff.initiate` event being processed by `relay._dispatch_directline_activity`.
- Adaptive Card appears in Teams: title "Connecting an agent...", body
  text including the IMS interaction number.
- ServiceNow Service Operations Workspace inbox shows the test agent has
  a chat invite.

### 4. Live mode round trip

Test agent claims the chat and types `Hi, this is Alex. What's up?`.

Expected:
- Adaptive Card in Teams: "You're now chatting with Alex".
- Followed by a plain text bubble prefixed with `**Alex:**` and the message.

User replies in Teams: `My password expired, can you help?`.

Expected:
- SN agent sees the message in their SOW chat pane within 1-2s.
- No echo loop in Teams (bridge dedupes via `recent_user_texts`).

### 5. Typing indicator (optional, requires SN BR change)

Only works if the outbound SN Business Rule is extended to fire `typing`
events. If not configured, skip.

### 6. Close

Test agent ends the chat in SOW.

Expected:
- Adaptive Card in Teams: "This chat has ended. Type **new** to start over."
- Sending `new` allocates a fresh session; the next message routes to BOT
  mode again.

### 7. Web channel regression

Run `tools/probe_e2e.ps1` from the repo root. Expected: same pass result
as before the Teams branch was merged.

## Pass criteria

- All seven steps succeed with no manual intervention beyond what's listed.
- No errors in `docker compose logs bridge` other than expected per-turn
  info logs.
- Reply latency: <5s for BOT replies, <2s for rep replies in either direction.

## Known gaps

- File attachments user <-> rep are not implemented in this branch.
  Sending a file in Teams will be ignored by the bot; SN-side attachments
  on the interaction won't surface in Teams.
- Group / channel scope is not supported (manifest is `personal` only).
