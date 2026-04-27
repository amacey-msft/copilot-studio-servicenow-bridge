# teams_skill/

A Microsoft 365 Agents SDK service that registers with Copilot Studio
as an **A2A** ("Add an agent → Microsoft 365 Agents SDK") sub-agent
and brokers a ServiceNow live-chat handoff on behalf of the parent CS
agent.

See [`docs/14-teams-skill-setup.md`](../docs/14-teams-skill-setup.md)
for setup, [`docs/10-teams-channel-overview.md`](../docs/10-teams-channel-overview.md)
for how this fits with the other channels, and
[`docs/v3-skill-pattern-rejected.md`](../docs/v3-skill-pattern-rejected.md)
for why the classic Bot Framework "skill" protocol was abandoned in
favour of A2A.

## Pattern

```
user ---> Copilot Studio orchestrator ---(A2A dispatch)---> teams_skill /api/messages
                                                                  |
                                                                  v
                                                             bridge (Flask)
                                                                  |
                                                                  v
                                                              ServiceNow
                                                                  |
                                          rep reply / sys_cs_message BR
                                                                  v
            CS <--- POST <serviceUrl>/v3/conversations/.../activities (proactive)
              |
              v
          user (rendered as a CS bot reply)
```

The user never sees this service directly. They chat with the parent
CS agent in whatever channel CS is exposed on (most commonly Teams
via CS's native Teams channel). When the orchestrator decides the
user needs a human, it dispatches the turn to us. We open the SN
chat, reply synchronously, and proactively push later CSR messages
back to CS.

## Files

| File | Role |
| ---- | ---- |
| `app.py` | Aiohttp host, AgentApplication wiring, `/api/messages` and `/api/sn-webhook` routes, `_patch_mcs_connector()` (SDK quirk fix), `_push_to_cs()` (proactive POST). |
| `state.py` | Per-conversation `ActiveHandoff` dataclass: signed serviceUrl, pending replies, recent user texts, dedupe lock. |
| `sn_client.py` | Thin client over the bridge's `/api/servicenow/*` endpoints. |
| `Dockerfile` | aiohttp container; runs as PORT=3979. |
| `requirements.txt` | `microsoft-agents-*` 0.9.x + aiohttp + msal. |

## Required env

See [`docs/14-teams-skill-setup.md`](../docs/14-teams-skill-setup.md#2-configure-teams_skill-env)
for the full list. At minimum: `SKILL_APP_ID`, `SKILL_APP_PASSWORD`,
`SKILL_TENANT_ID`, `SKILL_PUBLIC_URL`, `BRIDGE_INTERNAL_URL`,
`SN_WEBHOOK_SECRET`.

## Status

Production-candidate. Working end-to-end against an Entra-Agent-ID
parent agent in CS, deployed on Azure Container Apps. The two SDK
gotchas hit during the spike (empty-200 ContentTypeError; missing
proactive push to signed serviceUrl) are baked into `app.py`.
