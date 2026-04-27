# Why the Skill Pattern Was Rejected (v3 Spike Decision Record)

**Date:** 2026-04-27
**Branch:** `feat/v3-cs-skill-spike`
**Decision:** Abandon the Bot Framework "skill" pattern. Pivot to the
A2A ("Add an agent → Microsoft 365 Agents SDK") connector.

---

## TL;DR

The CopilotStudioSamples
[contact-center/skill-handoff](https://github.com/microsoft/CopilotStudioSamples/tree/main/contact-center/skill-handoff)
sample (`HandoverAgentSample.zip`) cannot work against an existing
Copilot Studio agent that uses **Entra Agent ID** identity. Its own
README explicitly flags itself as the legacy approach and recommends
"multi-agent orchestration over Agents SDK Agents" instead. We took
that recommendation.

---

## What the skill sample requires

The sample is a classic Bot Framework v4 skill consumer. For the
parent CS agent to call a skill — and for the skill to call back into
`pvaruntime/.../skillsV2/.../activities` — both sides need a
**classic AAD app registration** with:

- a client secret (so the skill can mint outbound tokens),
- `appRoles` and/or `oauth2PermissionScopes` (so AAD will issue a
  token whose audience is the *parent* CS agent's app id), and
- a discoverable AAD app object (`az ad app show --id <appId>`
  returns a row).

The skill calls `continue_conversation_with_claims(audience=<csAppId>)`
to push proactive replies. AAD rejects with HTTP 401 unless the above
is true.

## What the existing CS agent actually has

The IT Help Desk Triage Assistant uses **Entra Agent ID**, not a
classic app reg. Confirmed by:

```text
$ az ad sp show --id 23e0502e-1c27-47af-9923-5e415009d612 \
    --query "{spType:servicePrincipalType,appRoles:appRoles,oauth2Perms:oauth2PermissionScopes}"
{
  "appRoles": [],
  "oauth2Perms": [],
  "spType": "ServiceIdentity"
}

$ az ad app list --filter "appId eq '23e0502e-1c27-47af-9923-5e415009d612'"
[]                                # no underlying app registration exists
```

That means:

| Skill requirement                           | Available on Entra Agent ID? |
|---------------------------------------------|------------------------------|
| App registration object                     | **No** (only an SP exists)   |
| Client secret                               | **No** (cannot be created)   |
| `appRoles` to grant skill caller            | **No** (empty)               |
| `oauth2PermissionScopes` to grant           | **No** (empty)               |
| `signInAudience`                            | **null**                     |
| AAD will mint token with `aud=<csAppId>`    | **No**                       |

There is no admin action that adds these to a `ServiceIdentity` SP.
The Entra Agent ID identity is intentionally opaque — agents are not
classic AAD apps and cannot be retrofitted into the role.

## Symptom we hit

Inbound activity from CS to the skill works (CS *does* mint a token
with `aud=<skillAppId>`). The 401 surfaces on the **callback**:

```text
ERROR teams_skill.app skill process failed: 401, message='Unauthorized',
url='https://pvaruntime.us-il102.gateway.prod.island.powerapps.com/api/runtime/
bots/308bbcd1-.../skillsV2/v3/conversations/.../activities'
```

Tried both `audience=23e0502e-...` and `audience=app://23e0502e-...`,
proactive `continue_conversation_with_claims` and in-turn
`send_activity`. All 401. Root cause is the identity model, not the
audience format or the secret value.

## What the sample README says

From the [skill-handoff README](https://github.com/microsoft/CopilotStudioSamples/blob/main/contact-center/skill-handoff/README.md):

> **The skills feature in Microsoft Copilot Studio is being deprecated.
> Please use multi-agent orchestration over Agents SDK Agents (A2A)
> instead.**

So even on a CS agent that *did* have a classic app reg, this sample
is on the deprecation path. It is not the recommended pattern going
forward.

## Decision

Use the A2A connector documented at
[Add an agent → Microsoft 365 Agents SDK agent](https://learn.microsoft.com/en-us/microsoft-copilot-studio/configuration/add-agent-microsoft-365-agents-sdk-agent).

Properties:

- CS orchestrator dispatches activities to our `/api/messages` based
  on the agent's natural-language description; we respond
  **synchronously**.
- No skill manifest. No `audience=<csAppId>` token. No callback into
  `pvaruntime/skillsV2`. The 401 root cause does not exist in this
  pattern.
- Auth is a single classic app reg + secret on the connector side
  (ours: `SKILL_APP_ID` / `SKILL_APP_PASSWORD`). CS configures it via
  the "Connection string" credential blade.
- Async rep replies from ServiceNow are **buffered** in
  `ActiveHandoff.pending_replies` (a `deque` in
  [teams_skill/state.py](../teams_skill/state.py)) and drained on the
  next user turn. There is no proactive push because A2A is
  request/response.

## What was kept / what was cut

- **Kept:** Backing infrastructure — ACA app `ca-cps-sn-skill`, ACR
  `acrcpvb0c139ea`, `SKILL_APP_ID`/`SKILL_APP_PASSWORD`, ServiceNow
  client (`teams_skill/sn_client.py`), webhook endpoint
  (`/api/sn-webhook`), state store.
- **Cut:** Skill manifest (`teams_skill/manifest.py` + `manifest()`
  route + `/skill-manifest.json`), `_push_to_cs()`,
  `continue_conversation_with_claims` calls, `CS_PARENT_APP_ID` env,
  inbound JWT diagnostic block.
- **Unused, can delete:** App reg `21c72915-3534-4941-9c17-42d1984b3bb3`
  (created speculatively to test bring-your-own-app-reg path before
  pivot), `HandoverAgentSample.zip` at repo root.

## CS-side wiring

In Copilot Studio → IT Help Desk Triage Assistant → Agents → **Add an
agent** → **Microsoft 365 Agents SDK**:

| Field            | Value                                                                       |
|------------------|------------------------------------------------------------------------------|
| Endpoint URL     | `https://<aca-fqdn>/api/messages` (get with `az containerapp show`)         |
| Name             | `ServiceNow Live Agent`                                                     |
| Description      | `Use when the user asks to talk to a person, escalate to a human, get a ticket created in ServiceNow, or wants live support.` |
| Connection auth  | Client secret                                                               |
| Tenant ID        | `19e783ae-da17-4c69-8118-d15b80b10d3b`                                      |
| Client ID        | `SKILL_APP_ID` (from ACA env)                                               |
| Client secret    | `SKILL_APP_PASSWORD` (from ACA env)                                         |

Publish, retest in Teams.

## Lesson (recorded in user memory `copilot-studio-skill-handoff.md`)

Existing CS agents on Entra Agent ID cannot be retrofitted into the
classic skill model. For new escalation/handoff scenarios on CS, start
with the A2A connector — the skill pattern is on the deprecation list
even when it works.
