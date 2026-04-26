# teams_agent — M365 Agents SDK port of `teams_bot/`

**Status:** Stage 1 scaffold. Side-by-side with the existing `teams_bot/`
relay (which uses the deprecated `botbuilder-python` SDK). This folder uses
the supported `microsoft-agents-*` Python SDK and the *Genesys-style*
handoff pattern from the [M365 Agents SDK
samples](https://github.com/microsoft/Agents/tree/main/samples/dotnet/GenesysHandoff).

## Why this exists

The original Teams channel implementation, `teams_bot/`, is built on the deprecated
Bot Framework SDK. The replacement guidance is the
[Copilot Studio Samples — contact center](https://microsoft.github.io/CopilotStudioSamples/contact-center/)
collection. We chose the **Genesys** pattern (not `skill-handoff`) because
it matches what `teams_bot/` already does:

- Agent SDK app fronts the Teams channel (owns `/api/messages`)
- Forwards user turns to Copilot Studio via `CopilotClient`
- Catches an escalation **event** raised by the CS Escalate topic, then
  takes over the message path itself
- Receives live-agent replies via a webhook → proactively pushes them
  back into the Teams chat via `continue_conversation`

The legacy `teams_bot/` does the same conceptually but talks Direct Line
directly and uses `botbuilder-python`.

## What's in this folder (Stage 1)

| File | Purpose |
| ---- | ------- |
| `config.py` | Env-var loader (separate from `teams_bot/config.py`) |
| `state.py` | Per-conversation state: CS conversation id, escalation flag, CS reference |
| `agent.py` | `AgentApplication` subclass — message router + escalation handler |
| `dl.py` | Async Direct Line client (CS token from bridge, JWT user-id decode, map-dl-user) |
| `cs_client.py` | **Unused in DL-parity mode.** Legacy `CopilotClient`/OBO factory kept for reference if you ever switch to the SDK's delegated path |
| `bridge.py` | HTTP calls to the existing Flask bridge (init/escalate/user-message/reset) |
| `app.py` | aiohttp host: `/api/messages` + `/api/teams/push` proactive callback |
| `requirements.txt` | `microsoft-agents-*` 0.9.x + aiohttp |
| `Dockerfile` | Standalone container (port 3978) |
| `manifest/` | Teams app manifest skeleton with NEW bot id placeholders |

## What is NOT touched

- `teams_bot/` — left exactly as-is; runs in parallel until cutover
- `bridge/` — Flask code unchanged; we call its existing endpoints
- `web/` — browser webchat untouched (no Bot Framework in that path)
- `servicenow/` — AWA queue, BR, scripted REST untouched

## Rollback

```powershell
git checkout pre-agents-sdk-refactor
```

…or just stop the new container; the old `teams_bot/` keeps serving Teams
on its own bot id / app id.

## Required Azure setup before running

This runs in **Direct Line parity mode**: the agent talks to Copilot Studio
via the bridge's `/directline/token` proxy, not via the SDK's `CopilotClient`
+ OBO sign-in flow. **No user sign-in card. No Entra OBO app reg. No bot
OAuth connection.** End users open Teams, type, get answer.

1. **New** Azure Bot resource (don't reuse the legacy `teams_bot/` one):
   - Auth type: `SingleTenant` (recommended; set `--tenant-id`) or `MultiTenant`
     - `MultiTenant` is **deprecated** in `az bot create` since late 2024;
       use `SingleTenant` unless you specifically need it.
   - Messaging endpoint: `https://<agent-host>/api/messages`
   - Add Microsoft Teams channel (`az bot msteams create`)
2. Copilot Studio agent metadata (Settings → Advanced → Metadata):
   - Schema name → bridge `COPILOTSTUDIO_SCHEMA_NAME`
   - Environment id → bridge `COPILOTSTUDIO_ENVIRONMENT_ID`
   - These live on the **bridge** side, not in `teams_agent/.env`. The
     bridge mints a Direct Line token and the agent picks it up via
     `/directline/token`.
3. Wire the CS **Escalate** topic to call
   `<BRIDGE_PUBLIC_URL>/api/servicenow/agent/escalate` with a JSON body
   that includes `session_id`. The bridge resolves the CS-minted DL user
   UUID back to the agent's `sid` via the `/api/teams/map-dl-user` reverse
   index (registered automatically on every turn — see `dl.py`).

The `OBO_*` and `AZURE_BOT_OAUTH_CONNECTION_NAME` env vars are **read but
not used** in DL-parity mode; they're kept in `config.py` so you can flip
back to the SDK's delegated path without env churn.

Detailed walkthrough lives in [`docs/13-teams-agent-setup.md`](../docs/13-teams-agent-setup.md).

## User commands (chat-side)

The agent intercepts these phrases **before** routing to Copilot Studio
or the live-chat path. They work in any state (`bot`, `queued`, `live`,
`closed`):

| Phrase (case-insensitive) | Effect |
| ------------------------- | ------ |
| `new`, `new chat`, `start new chat` | Drop the bridge session, start a fresh bot conversation |
| `reset`, `-reset` | Same as `new` |
| `start over`, `restart` | Same as `new` |

Implementation: `RESET_COMMANDS` in `agent.py`. The agent calls
`POST /api/teams/reset-session` on the bridge, which removes the
in-memory `BridgeSession` and its `teams_user_key` index entry. The
SN-side `interaction` / `awa_work_item` records remain in SN as history.

## Session auto-recycle

Teams "Clear conversation" only clears the client. The bridge gets no
signal it happened, so without recycling the next user turn would
forward into a dead live-chat. The bridge auto-recycles a stale session
on the next `/api/teams/init-session` call when:

- `state == closed`, OR
- `state == live` and idle &gt; `TEAMS_LIVE_IDLE_RECYCLE_S` (default **900s** / 15 min), OR
- any non-`bot` state and idle &gt; `TEAMS_SESSION_IDLE_TIMEOUT_S` (default **3600s** / 1 hour)

If a real user steps away mid-live-chat for &gt;15 min, their next message
starts a fresh bot turn. Tell users to type `new` if they want to be
explicit, or raise `TEAMS_LIVE_IDLE_RECYCLE_S` for longer-running CSR
chats.
