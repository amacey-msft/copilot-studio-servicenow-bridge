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
