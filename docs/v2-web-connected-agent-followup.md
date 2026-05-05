# Follow-up — wire `Contoso IT Help` (web channel) to teams_a2a as a Connected Agent

> **Status:** planned (not yet implemented). Tracked outside PR #5.

## Why

The web channel currently runs on the `awm_contosoithelp` Copilot Studio
agent because the unified agent (`crd20_itHelpDeskTriageAssistant`) is
published with **Entra Agent ID** auth and the intranet kiosk page is
anonymous. Today `awm_contosoithelp` escalates by directly calling the
bridge from a CS HTTP-tool action (`EscalateToLiveITAgent`). The Teams
channel uses a different, cleaner pattern: a **Connected Agent** (the
`teams_a2a` M365 Agents SDK app, hosted as `ca-cps-sn-skill` on ACA)
owns the conversation for the duration of the live chat and proxies
both directions through the bridge.

This follow-up brings the web channel onto the same pattern so:

- One escalation code path lives in `teams_a2a/`, not split across an
  HTTP-tool action and a CS topic.
- Live-rep replies arrive via the existing A2A signed-URL push back
  into the orchestrator instead of bridge polling.
- Future bridge route changes don't require botcomponent YAML patches.

## Steps (Copilot Studio Studio UI)

Target agent: **Contoso IT Help** (`awm_contosoithelp`, botid
`c5702a80-413f-f111-88b4-000d3a3421b2`).

1. Open the agent in [Copilot Studio Studio](https://copilotstudio.microsoft.com/).
2. **Agents** tab → **Add an agent** → **A2A (Bring your own)**.
3. Endpoint:
   `https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io/api/messages`
4. Authentication: **None** (the `teams_a2a` app accepts unsigned
   requests from CS-A2A; see
   [`docs/14-teams-a2a-setup.md`](14-teams-a2a-setup.md)).
5. Description (paste verbatim — orchestrator routing depends on it):

   > Owns the conversation for the entire duration of any live agent
   > chat. Use this agent when the user asks to talk to a person,
   > escalate, or open an incident with the IT help desk.

6. **Remove** the existing HTTP-tool action
   `awm_contosoithelp.action.EscalateToLiveITAgent` once the connected
   agent is verified working. (Leave `CreateServiceNowIncident` — it's
   still a useful one-shot ticket-creation path that doesn't need
   live-agent state.)
7. Optionally add an **Escalate** topic that explicitly routes to the
   connected agent for keyword triggers like "speak to a human"; the
   generative orchestrator usually picks it up from the description
   alone.
8. Publish (or run the Dataverse `PvaPublish` action).

## Verification

- Browser → intranet page on `ca-cps-bridge` → "I need help with my
  laptop" → "speak to an agent". The Connected Agent should respond
  via the orchestrator.
- ServiceNow → AWA queue picks up the chat, CSR replies, browser sees
  rep messages routed via the same bridge polling path.
- Teams regression: send a turn to the Teams app, confirm
  `teams_a2a` still answers and SN escalation still works.

## Risks / things that bit us before

- Connected Agent registrations created via Dataverse API sometimes
  need a manual "remove + re-add" in the UI before the runtime picks
  them up. Doing this in UI from the start avoids the issue.
- The orchestrator may try to answer "talk to a person" itself if the
  connected agent's description isn't strong enough. Keep the
  "Owns the conversation for the entire duration" sentence.
- Don't re-publish without testing — this is the customer-facing web
  agent.
