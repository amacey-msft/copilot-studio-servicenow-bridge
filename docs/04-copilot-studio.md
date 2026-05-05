# 04 — Copilot Studio configuration

This repo runs **two Copilot Studio agents**, one per surface, that
share the same handoff backend:

| Agent (schema name) | Channel | Auth on Direct Line / channel |
| ------------------- | ------- | ----------------------------- |
| `awm_contosoithelp` | Browser webchat (`web/intranet.html`) | **None** — anonymous DL token via the CS-hosted token endpoint, suitable for unauthenticated kiosks. |
| `crd20_itHelpDeskTriageAssistant` | Microsoft Teams (CS native channel) | **Entra Agent ID** — user is signed in to Teams. |

Both agents are built with **generative orchestration**. Both delegate
the live-agent handoff to the same backend (`teams_a2a` /
`ca-cps-sn-skill`) through Copilot Studio's **Connected Agent**
("Add an agent → A2A") mechanism. Neither agent calls the bridge's HTTP
endpoints directly any more — the Connected Agent does that.

> The earlier "Escalate topic + Send an HTTP request" pattern was
> dropped on 2026-05-04. It cannot proactively push CSR replies back
> into the conversation, and it forced a separate `/api/servicenow/escalate`
> trust boundary on the browser. The Connected Agent owns the
> conversation for the duration of the live chat and renders rep
> replies inside CS via a signed `serviceUrl` proactive POST. See
> [`14-teams-a2a-setup.md`](14-teams-a2a-setup.md) for the agent itself.

## Prerequisites

- The bridge from [`03-bridge-backend.md`](03-bridge-backend.md) deployed
  on Azure Container Apps as `ca-cps-bridge` (or any HTTPS host CS can
  reach).
- The `teams_a2a` Connected Agent from
  [`14-teams-a2a-setup.md`](14-teams-a2a-setup.md) deployed on Azure
  Container Apps as `ca-cps-sn-skill`. You will need its public
  `/api/messages` URL.
- Two Copilot Studio agents in the same environment, both built with
  **generative orchestration**.

## 1. Register `teams_a2a` as a Connected Agent on each CS agent

Do this **on both agents** (`awm_contosoithelp` and
`crd20_itHelpDeskTriageAssistant`):

1. Open the agent → **Agents** tab → **+ Add an agent**.
2. Pick **A2A (Bring your own)**.
3. Endpoint: `https://ca-cps-sn-skill.<your-aca-domain>/api/messages`
4. Authentication: **None** (the A2A endpoint is unauthenticated; CS
   does not always send an auth header to A2A endpoints in the current
   preview, and the agent treats any caller from the CS UA prefix as
   trusted).
5. Description (this is what the orchestrator uses to dispatch):

   > Use this agent whenever the user asks to talk to a person, a
   > human, a live agent, support, or otherwise indicates the bot
   > can't help and they want a real human. This agent owns the
   > conversation for the entire duration of the live chat and will
   > end the conversation itself when the live chat closes.

6. Save.

Repeat on the second agent.

## 2. Wire the Escalate system topic to the Connected Agent

On each agent:

1. Topics → **System** → **Escalate**.
2. Replace the body of the topic with a **Redirect to Connected Agent**
   node pointing at the agent you registered in step 1.
3. Save and **Publish**.

(Generative orchestration will also auto-route "talk to a person"
turns to the Connected Agent without firing Escalate, but wiring
Escalate explicitly catches the deterministic-keyword path too.)

## 3. Web channel (awm_contosoithelp): Direct Line token endpoint

The browser fetches a Direct Line token from the bridge's
`/directline/token` route. The bridge proxies the **Copilot Studio
hosted DL token endpoint** for `awm_contosoithelp` (set via
`DIRECTLINE_TOKEN_ENDPOINT` in `bridge/.env`). This endpoint mints
anonymous DL tokens — no user sign-in needed.

```
DIRECTLINE_TOKEN_ENDPOINT=https://<env-host>.environment.api.powerplatform.com/powervirtualagents/botsbyschema/awm_contosoithelp/directline/token?api-version=2022-03-01-preview
POWERPLATFORM_BOT_ID=c5702a80-413f-f111-88b4-000d3a3421b2
POWERPLATFORM_BOT_SCHEMA=awm_contosoithelp
```

> **Important:** the schema name in the URL must match the agent that
> has the Connected Agent registered. If you point this at a different
> CS agent, escalation will silently no-op.

The browser passes the bridge's `session_id` as the Direct Line
`User.Id` so CS sees a stable id per user (see
[`05-browser-webchat.md`](05-browser-webchat.md)).

## 4. Teams channel (crd20_itHelpDeskTriageAssistant): native CS channel

The Teams channel is enabled directly on
`crd20_itHelpDeskTriageAssistant` from CS Settings → **Channels** →
**Microsoft Teams**. CS produces a Teams app package; sideload it.
This agent uses **Entra Agent ID** auth, so users are signed in via
their Teams identity automatically. There is no separate bot resource
to provision — CS owns the channel.

Same Connected Agent registration as the web agent (step 1) handles
escalation here.

## 5. Verify

For each agent:

1. Open the agent in the CS test pane (or in its real channel).
2. Type `talk to a human`.
3. The Connected Agent's first reply ("Connecting you to a live
   agent…") should render *as the CS agent*. Check the bridge logs:

   ```
   [agent] escalate hit headers=... body={"session_id":"...","opening_message":"..."}
   ```

4. Within a few seconds an SN test agent who is **Available** in
   Service Operations Workspace should see a chat invitation. Accept
   it and reply — the CSR reply should appear in the original CS
   conversation.

## Reference

- [Add an agent (Copilot Studio Connected Agents)](https://learn.microsoft.com/en-us/microsoft-copilot-studio/configuration/add-agent-microsoft-365-agents-sdk-agent)
- [Generative orchestration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions)
- The Connected Agent itself: [`14-teams-a2a-setup.md`](14-teams-a2a-setup.md)
