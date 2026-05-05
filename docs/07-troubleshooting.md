# 07 — Troubleshooting

Every entry below is a real failure mode encountered while building this
integration. They are grouped by where the symptom shows up.

## ServiceNow side

### `/open_chat` returns 200 but `awa_work_item` is never created

Either:

- The `interaction.assigned_to` was set to a non-empty value.
  `tools/sn_scripted_rest_open_chat.js` explicitly clears it because
  `sn_cs.VASystemObject.createConversation()` defaults `assigned_to` to the
  Virtual Agent system user, and AWA refuses to route an interaction that
  already has an assignee. If you customized the script and removed the
  clear, restore it.
- The `awa_work_item` insert ran but `state` was never explicitly set to
  `queued`. The "Try to assign workItem" OOB BR fires on **state
  CHANGES TO** `queued`, not on insert. You must insert with empty state
  and then update to `queued`. See the script.

### `/open_chat` returns 200, work_item exists, but the agent never gets a chat invite

- Agent isn't **Available** in AWA presence at this moment.
- Agent has no `awa_agent_capacity` row for the `Chat` channel — they need
  to sign in to Agent Workspace and toggle Available once.
- Agent isn't a member of the queue's eligible group.
- Wrong queue / channel sys_id passed to `/open_chat`.

### Agent's first reply errors with `ConversationContext.getBrandingKey()` NPE

You created the `sys_cs_conversation` row with a raw `GlideRecord.insert()`
or via the Table API, instead of via
`sn_cs.VASystemObject.createConversation()`. The OOB API populates a
~1.9KB compressed `context` blob that downstream APIs require; the raw
insert leaves it empty. Use the Scripted REST `/open_chat` script verbatim.

### Agent's first reply errors with `IChannelSession.isMessageBatching()` NPE

The `sys_cs_session_binding` row is missing or doesn't link to the right
session. The reference `/open_chat` script creates this in step 3.

### Consumer messages are inserted in `sys_cs_message` but the agent's pane stays empty

You called the Table API directly to insert into `sys_cs_message`. That
persists the row but does **not** publish on AMB, so the live pane never
updates. Use the Scripted REST `/send_message` script which calls
`sn_cs.AgentChatScriptObject.send()` (insert + AMB publish).

### `/send_message` returns `graphql.GraphQLException: Chat has ended`

The Virtual Agent timeout (~3 minutes idle by default) closed the
conversation server-side. The reference script bumps
`sys_cs_session_binding.last_client_activity_time` on every send to
prevent this, but if a user genuinely went idle for 3+ minutes the chat
is gone — open a new one via `/open_chat`.

### Consumer's avatar in SOW shows as `IB` (intranet.bridge) instead of e.g. `AE`

`GlideImpersonate` failed silently because `intranet.bridge` lacks the
`impersonator` role. Cosmetic only — the message still arrives. Either
grant the role (and accept the security implications) or live with the
placeholder avatar.

## Bridge ↔ ServiceNow

### Outbound BR logs `HTTP 403 forbidden`

The `intranet_bridge.outbound_webhook_secret` `sys_property` doesn't match
the bridge's `SN_WEBHOOK_SECRET` env var. Update one to match the other.
After updating the env var, restart the bridge process / container.

### Outbound BR logs `HTTP 0` or "No HTTP response"

The bridge isn't reachable from ServiceNow. Common causes:

- Your dev tunnel URL changed (VS Code dev tunnels pick a new subdomain
  each session unless persisted). Update
  `intranet_bridge.outbound_webhook_url`.
- Tunnel/ngrok process died. Restart it.
- Visibility is set to *Private*; flip to *Public*.
- A firewall is blocking outbound HTTPS from the SN PDI. PDIs can call
  out to the public internet by default; your corporate instance may not.

### Outbound BR returns `HTTP 404 session not found`

The BR fired and reached the bridge, but the bridge's in-memory
`BridgeSession` store doesn't contain that `bridge_session_id`. Causes:

- The bridge was restarted between session creation and the agent's
  reply. (Fix: persist sessions outside the process — see
  [`09-production-hardening.md`](09-production-hardening.md).)
- You called `/open_chat` directly with a synthetic `bridge_session_id`
  the bridge has no record of. Expected behaviour for that smoke test.
- Two bridge instances behind a load balancer without sticky sessions and
  without shared session storage.

## Bridge ↔ browser

### Browser receives `status: queued` but never `status: live`

- The agent never accepted the invite (still in `offered` state in
  `awa_work_item`).
- Agent accepted but the auto-greeting BR didn't fire (check System Logs
  for `intranet_bridge.outbound` entries). The bridge transitions to
  `live` on the first agent message OR an explicit `event: claimed`
  webhook — the reference setup uses the first message path.

### Browser is stuck in `queued` even though Alex accepted

WebSocket isn't getting server-pushed frames (common with default
gunicorn `gthread` worker behind some proxies), and the polling fallback
isn't running. Verify:

- The polling timer is started during page bootstrap, not after escalate.
- Polling actually drains events (call `GET /api/servicenow/poll/<sid>`
  manually with a browser dev tool and check `events`).

### Browser receives duplicate messages

You're rendering both WS frames *and* polled events. The reference
implementation drains the poll queue after every poll, so each event is
delivered exactly once across both channels combined — but if you
modified the bridge to keep events in the queue, the client must
deduplicate.

## Copilot Studio side

### `Topic.escalateResp` is empty / HTTP action returned an error

- Bridge URL is wrong, not HTTPS, or not publicly reachable.
- Missing or wrong `X-Agent-Secret` header. Check
  `AGENT_API_SECRET` matches the bridge's env.
- Body JSON is malformed. The Copilot Studio body editor sometimes
  inserts smart quotes — make sure all quotes are plain `"`.

### Bridge returns 404 "unknown session"

Your topic is passing `{x:User.Id}` but the browser didn't use the bridge
session id as the Direct Line user id. See
[`05-browser-webchat.md`](05-browser-webchat.md) §1: the
`/directline/token` request must include `{ user_id: <session_id> }` and
every `directLine.postActivity` call must include `from: { id: session_id }`.

### Trigger never fires

If you're using **classic** orchestration, add explicit trigger phrases.
If you're using **generative** orchestration, the *Description* field is
what the orchestrator uses — make it specific (mention "human", "agent",
"live person", "support rep").

## Tools that helped diagnose these

- **System Logs → All** with filter `Message contains intranet_bridge`
  (BR script logs land here).
- **`awa_work_item` table list** filtered to your `interaction_sys_id`
  (proves AWA actually created a routing item).
- **Network tab in Edge DevTools** on the webchat page (catches missing
  `X-Bridge-Secret`, malformed bodies, CORS).
- **`docker logs -f <bridge container>`** (or whatever process supervisor
  you use) — most issues surface here within a few seconds of the root
  cause.

## Teams: bot stops responding after I cleared the conversation

**Symptom:** You used the live-chat handoff, the CSR ended (or just
walked away), then later you "Clear conversation" in Teams, reopen, and
the bot ignores your messages. Bridge logs show
`[webhook] dropping user-text echo for session=… text='…'`.

**Cause:** Teams "Clear conversation" is client-only. The bridge still
has your `BridgeSession` in `state=live` and forwards every new turn to
the dead ServiceNow `interaction`. The webhook drops the message as a
duplicate of itself.

**Fix:**

1. **As a user:** type `new` (or `reset`, `restart`, `start over`). The
   agent calls `/api/teams/reset-session` and the next turn allocates a
   fresh bot session.
2. **Automatic:** the bridge recycles a stale `live` session on the next
   `init-session` call once it's been idle for `TEAMS_LIVE_IDLE_RECYCLE_S`
   seconds (default **900s** / 15 min). Tune via env var.

See `teams_agent/README.md` → "User commands" and
"Session auto-recycle" for the full list of reset phrases and recycle
thresholds.

## Teams (Agents SDK): Copilot Studio escalate tool returns "session not found"

**Symptom:** The CS Escalate topic fires (you see the typing indicator,
the "Connecting you with a live agent..." message), but no SN
interaction is created. Bridge logs show
`[agent] escalate hit ... body={"session_id":"<UUID>", ...}` followed by
no further log lines (silent 404 from `_get_session`).

**Cause:** Direct Line rewrites `from.id` on every user activity to the
`user` claim encoded in the DL token (a UUID minted by Copilot Studio
when the token endpoint is hit). CS then surfaces *that* UUID as
`System.Activity.From.Id` inside topics. The CS Escalate HTTP tool
typically passes `session_id: System.Activity.From.Id`, so the bridge
receives a CS-minted UUID it has never seen, not the hex sid it
allocated for the user.

**Fix:** The agent decodes the DL token JWT after each mint and
registers the `dl_user_id -> sid` mapping via
`POST /api/teams/map-dl-user`. The bridge falls back to that reverse
index when the direct sid lookup fails. If you see this symptom on a
fresh deploy, verify:

1. `teams_agent/dl.py` `_decode_dl_user_id()` is being called (look for
   `[teams] map-dl-user dl=… -> sid=…` in bridge logs after each new
   conversation).
2. The agent container can reach the bridge over `BRIDGE_INTERNAL_URL`
   (the mapping is registered via that URL).
3. The CS escalate tool's `session_id` parameter is bound to
   `System.Activity.From.Id` (or a stable user id you also send via
   `channelData`), not to a per-turn activity id.

## Teams (Agents SDK): bot ignores messages with newlines

**Symptom:** Single-line text like "hi" works; multi-line text (paste
from Outlook etc.) gets a typing indicator but no reply. Logs show the
activity arriving at `/api/messages` but no handler firing.

**Cause:** `@app.message(re.compile(".*"))` is a regex catch-all that
silently fails to match text containing newlines because Python's `.`
doesn't match `\n` without `re.DOTALL`.

**Fix:** Use `@app.activity("message")` instead. Already applied in
`teams_agent/app.py`; do not reintroduce the regex form.

## Devtunnels: ID vs slug confusion

**Symptom:** `devtunnel host <something>` fails with "tunnel not found",
or the URL you put in the Azure Bot messaging endpoint never reaches
your container.

**Cause:** The devtunnel CLI uses two different identifiers:

- **Tunnel ID** (e.g. `jolly-river-lw1s3ms`): the auto-generated friendly
  name. This is what `devtunnel host` accepts.
- **URL slug** (e.g. `pbqgkr6d`): a separate short token embedded in
  the public hostname `https://<slug>-<port>.<region>.devtunnels.ms`.
  This is what goes in the Azure Bot endpoint.

`devtunnel list` shows the tunnel ID; the URL slug only appears in the
hostname. Don't confuse them. See [`scripts/devtunnel-README.md`](../scripts/devtunnel-README.md).

## Azure Container Apps (`ca-cps-bridge`)

### Bridge env var or secret change isn't visible in the running container

**Symptom:** `az containerapp secret set` returns success, you restart
the revision, but the new value isn't picked up.

**Cause:** Restarting an existing revision does not re-resolve
`secretref:` env bindings. Containers cache the secret value at start.

**Fix:** Force a brand-new revision:

```pwsh
az containerapp update -n ca-cps-bridge -g rg-cpv-aca `
  --revision-suffix "v$(Get-Date -Format MMddHHmm)"
az containerapp revision list -n ca-cps-bridge -g rg-cpv-aca `
  --query "[?properties.active]" -o table
```

Confirm the new revision is `Healthy`, traffic is `100`, and the old
revision has drained before retesting.

### Deploy script complains about metacharacters in a secret value

**Symptom:** `deploy-bridge-aca.ps1` warns that one of the SN_* values
contains characters in `[&}%<>|^]` and routes that secret through a
temporary `.cmd` file.

**Cause:** The `az.cmd` Windows wrapper hands its arguments to
`cmd.exe`, which interprets `}` `&` `%` etc. as command separators —
the secret value gets truncated mid-string and the resulting key is
silently wrong.

**Fix:** Already handled: the deploy script writes a single-line
`.cmd` file under `$env:TEMP`, runs it via `cmd /c`, then re-applies
the env-var binding `SN_PASSWORD=secretref:sn-password`. Verify after
deploy with:

```pwsh
az containerapp secret list -n ca-cps-bridge -g rg-cpv-aca -o table
az containerapp show -n ca-cps-bridge -g rg-cpv-aca `
  --query "properties.template.containers[0].env" -o json
```

### Live-chat sessions vanish after a deploy

**Symptom:** Browser users sitting in `state=live` get bumped back to
the bot or see the chat freeze immediately after a new revision goes
active.

**Cause:** `ca-cps-bridge` runs at `min=max=1` and the bridge holds
session state in process memory (`BridgeSession` map). Any revision
swap (image push, env change, secret rotation) is a hard restart and
loses every in-flight session.

**Fix:** Accept it for the demo workload — schedule deploys around
known idle windows. Long-term, externalise state to Redis (see
[`09-production-hardening.md`](09-production-hardening.md) → "Bridge
state externalisation").
