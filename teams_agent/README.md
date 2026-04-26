# teams_agent — M365 Agents SDK port of `teams_bot/`

**Status:** Stage 1 scaffold. Side-by-side with the existing `teams_bot/`
relay (which uses the deprecated `botbuilder-python` SDK). This folder uses
the supported `microsoft-agents-*` Python SDK and the *Genesys-style*
handoff pattern from the [M365 Agents SDK
samples](https://github.com/microsoft/Agents/tree/main/samples/dotnet/GenesysHandoff).

## Why this exists

A Microsoft developer flagged that `teams_bot/` is built on the deprecated
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
| `cs_client.py` | Thin `CopilotClient` factory (uses OBO token from `UserAuthorization`) |
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

This is a delegated/OBO flow; **end users will see a Teams sign-in card
on first message**. Server-to-service (S2S) for Copilot Studio is not yet
supported as of `microsoft-agents-copilotstudio-client` 0.9.0.

1. **New** Azure Bot resource (don't reuse the old one):
   - Auth type: SingleTenant (recommended) or MultiTenant
   - Messaging endpoint: `https://<host>/api/messages`
   - Add Microsoft Teams channel
2. **New** Entra app reg for OBO (named e.g. `cps-handoff-obo`):
   - Single tenant
   - Redirect URI (Web): `https://token.botframework.com/.auth/web/redirect`
   - API permissions (Delegated): `Power Platform API → CopilotStudio.Copilots.Invoke`,
     `Microsoft Graph → User.Read`, `Dynamics CRM → user_impersonation`
   - Expose an API: `api://botid-<obo-app-id>` with scope `defaultScope`
   - Create a client secret
3. On the Azure Bot, add an OAuth Connection Setting:
   - Service Provider: Azure Active Directory v2
   - Client id / secret: from the OBO app reg above
   - Scope: `api://botid-<obo-app-id>/defaultScope`
   - Note its *connection name* — goes in env as `AZURE_BOT_OAUTH_CONNECTION_NAME`
4. Copilot Studio agent metadata (Settings → Advanced → Metadata):
   - Schema name → `COPILOTSTUDIO_SCHEMA_NAME`
   - Environment id → `COPILOTSTUDIO_ENVIRONMENT_ID`
5. (Optional, for full Genesys parity) edit the CS **Escalate** system topic:
   - Add an Event node named `ServiceNowHandoff` at the end
   - Set its value to a summary variable
   - This is what makes the agent take over the message path on escalation.
   - Without this, escalation still works because the CS topic also calls
     the bridge's `/api/servicenow/agent/escalate` HTTP action directly,
     and the bridge will push state via `/api/teams/push`.

Detailed walkthrough lives in `docs/13-teams-agent-setup.md` (Stage 2).

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
