# v3 spike status — Phase 6 partial verdict

**Branch:** `feat/v3-cs-skill-spike`  
**Tag pre-spike:** `v2.1.0-stable`  
**Last commit:** see `git log feat/v3-cs-skill-spike`

## What's live

| Resource | Value |
|---|---|
| Subscription | `b0c139ea-82e8-4e26-94cc-d8e6dda0c4ec` |
| Tenant | `19e783ae-da17-4c69-8118-d15b80b10d3b` |
| Resource Group | `rg-cpv-aca` (eastus2) |
| ACA Env | `cae-cpv` |
| ACA App | `ca-cps-sn-skill` (revision `ca-cps-sn-skill--v04262133`, Healthy, 100% traffic) |
| Public URL | https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io |
| Manifest URL | https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io/skill-manifest.json |
| Messages endpoint | https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io/api/messages |
| Health | https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io/healthz |
| ACR | `acrcpvb0c139ea` (admin enabled) |
| Image | `acrcpvb0c139ea.azurecr.io/teams-skill:latest` (also tag `v04262120`) |
| AAD App | `cps-sn-skill` (`ba5ad5d6-49b0-4d7d-b215-79bbd30fa424`, SingleTenant) |
| AAD SP Object | `25e65e97-94c2-4954-ad33-c740eb7ceb84` |
| Secret | stored in ACA secret store as `skill-app-password`, expires 2027-04-26 |
| Azure Bot | `bot-cps-sn-skill` (SingleTenant, F0, NO Teams channel — CS owns Teams in skill model) |

## Phase 6 probe finding

`tools/probe_skill_auth.py` posted three skill-protocol event activities to `/api/messages`:

1. NO Authorization header
2. Real client-credentials JWT (audience = skill app id)
3. Garbage JWT

**All three reached the same runtime failure** — the agent pipeline dispatched the activity, hit the message handler, then `on_turn_error` tried to `connector_client.conversations.reply_to_activity` against the test serviceUrl `https://test.invalid` and crashed with DNS error.

### What that means

- M365 Agents SDK 0.9.x with `AgentApplication(options=ApplicationOptions(adapter, bot_app_id=SKILL_APP_ID, storage))` does **not** enforce JWT validation on `/api/messages` in the configuration used. The pipeline accepts the activity regardless of caller identity.
- This is **likely a config gap** (we are not wiring an explicit auth/claims validator), not a fundamental SDK break. SDK pre-1.0 docs are sparse on this.
- The skill-protocol pipeline (activity dispatch, handler routing, send_activity callback) **does work** end-to-end up to the connector callback step.

### Verdict

- **YELLOW**, leaning RED for production. Inconclusive without a real CS-issued JWT in flight.
- The fastest way to settle the verdict is to do Phases 4-5 manually in Copilot Studio and watch container logs during a real CS skill invocation (see "Hand-back to user" below).

## Hand-back to user — Phases 4-5 (CS UI work, agent cannot drive)

### Phase 4: register skill in Copilot Studio

1. In CS, open the agent that should escalate.
2. Settings → Skills → Add a new skill.
3. Paste manifest URL: `https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io/skill-manifest.json`
4. CS will read the manifest, create a child app reg under your tenant, and surface the skill's actions.
5. **Capture the CS parent agent's app reg id** (Settings → Advanced → Metadata → Agent App ID, or whatever your CS surface labels it). That's `CS_PARENT_APP_ID`.
6. Send `CS_PARENT_APP_ID` back to the agent so we can patch it into ACA env vars.

### Phase 5: author CS topic that invokes the skill

1. Create or edit a CS topic with trigger phrase like "talk to a person".
2. Add an action node → choose the registered skill `ServiceNowHandoffSkill`.
3. Choose action `endConversation`.
4. Bind input `userEmail` from `System.User.Email`.
5. Optionally bind `initialQuery` from the user's last utterance.
6. Publish the agent.

### Phase 6 (real test)

After 4-5 done:

```pwsh
# tail skill logs in one terminal
az containerapp logs show -n ca-cps-sn-skill -g rg-cpv-aca --follow --tail 50
```

Then in the CS test pane (or Teams once channel re-attached) say "talk to a person". Watch logs for:

- A real `POST /api/messages` from CS
- Whether JWT validation accepts/rejects
- Whether the event handler fires for `endConversation`

**If accepted + handler fires**: GREEN — Python skill protocol works with CS.  
**If 401 with claims-validation message**: YELLOW — need to wire `AllowedCallersClaimsValidator` (skill-side config) with the CS parent app id. Solvable.  
**If 500 / unhandled**: RED — port the skill to .NET (where this pattern is mature and well-trodden).

## Repo state

```
teams_skill/__init__.py        (Phase 1, committed 78a9568)
teams_skill/manifest.py        (Phase 1, committed 78a9568)
teams_skill/app.py             (Phase 1, committed 78a9568)
teams_skill/requirements.txt   (Phase 1, committed 78a9568)
teams_skill/README.md          (Phase 1, committed 78a9568)
teams_skill/Dockerfile         (Phase 2, committed 382fb57)
scripts/deploy-skill-aca.ps1   (Phase 2, committed 382fb57)
tools/probe_skill_auth.py      (Phase 6, this commit)
docs/v3-spike-status.md        (this commit)
```

## What's blocked

- No further automated work possible until user supplies `CS_PARENT_APP_ID` from Phase 4.
- Phases 7+ (bridge inbound endpoint, reverse push via `continue_conversation`, end-to-end test in Teams) wait on Phase 6 verdict.
