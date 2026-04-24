# 06 — End-to-end test

This walks through verifying the full round-trip: browser → bridge →
ServiceNow → live agent → bridge → browser. If everything in this doc
passes, your integration works.

## Prerequisites

- ServiceNow side from [`02-servicenow-setup.md`](02-servicenow-setup.md)
  is complete (including §11 smoke test passing).
- Bridge from [`03-bridge-backend.md`](03-bridge-backend.md) is running and
  reachable from the SN instance.
- Test agent (`alex.rep`) is signed in to Agent Workspace and set
  **Available**.

## The probe

The repo ships with [`tools/probe_e2e.ps1`](../tools/probe_e2e.ps1). It
exercises the bridge as if it were the browser:

```powershell
.\tools\probe_e2e.ps1 -BaseUrl http://localhost:5000
```

Steps it performs:

1. `POST /api/servicenow/init-session` → gets a `session_id`.
2. `POST /api/servicenow/escalate` → asks the bridge to open a SN chat.
3. Waits 5 seconds for AWA routing.
4. `POST /api/servicenow/user-message` → sends a follow-up message.
5. `GET /api/servicenow/poll/<sid>` → drains queued events.

## Expected output

```text
=== 1. init-session ===
{
  "session_id": "aeac2bdc950b4d8f93b1f9b2bdbee7d5",
  "state":      "bot"
}

=== 2. escalate ===
{
  "session_id":         "aeac2bdc950b4d8f93b1f9b2bdbee7d5",
  "interaction_number": "IMS00000XX",
  "interaction_sys_id": "...",
  "state":              "queued"
}

=== 3. waiting 5s for AWA ===

=== 4. user-message ===
{ "ok": true }

=== 5. poll ===
{
  "state":              "live",
  "rep_name":           null,
  "interaction_number": "IMS00000XX",
  "events": [
    { "type":"status",  "state":"queued" },
    { "type":"status",  "state":"live" },
    { "type":"message", "from":"rep",
      "text":"Thank you for contacting support. I am looking into your question now and will be with you shortly." }
  ]
}
```

The `message` frame in step 5 is the agent's auto-greeting being relayed
through the outbound Business Rule. Receiving it proves:

- The Scripted REST `/open_chat` worked (a routable interaction was created).
- AWA routed it (the agent received the chat).
- The agent reply produced a `sys_cs_message` insert.
- The Business Rule fired and POSTed to the bridge webhook with the
  correct shared secret.
- The bridge matched the `bridge_session_id` to a live session and pushed
  the message into the per-session queue.

## What if the auto-greeting doesn't appear?

Run through these in order:

1. **Did `/open_chat` succeed?** Step 2 returned an `interaction_number` —
   if not, look at bridge logs for the SN response body.

2. **Did AWA route it?** In ServiceNow open `awa_work_item` and filter
   `Document = <interaction_sys_id>`. Within ~5 seconds it should appear
   with `state = offered` or `accepted`.

3. **Did the agent see it?** Check the AWA Inbox in Agent Workspace as
   `alex.rep`. They must be **Available** at the moment of the call.

4. **Did the BR fire?** *System Logs → All*, filter
   `Message contains intranet_bridge`. You want a row like:

   ```
   [intranet_bridge.outbound] webhook https://<host>/api/servicenow/webhook returned HTTP 200 body={"ok": true}
   ```

   If you see `HTTP 403`, the secrets don't match. If `HTTP 0`, the URL
   is unreachable. If `HTTP 404`, the BR fired but the bridge has no
   session for that `bridge_session_id` — make sure the bridge wasn't
   restarted between steps 2 and 5.

5. **Did the bridge receive it?** Tail the bridge process / container
   logs. You should see a webhook handler log line.

## Manual end-to-end test

Once the probe is green:

1. Open your browser webchat.
2. Type `talk to a person`.
3. Watch the UI switch to "Connecting an agent…".
4. As `alex.rep`, accept the chat invitation in Agent Workspace.
5. Watch the UI switch to "Chat with Alex Rep".
6. Type a message in the browser → confirm it appears live in Alex's pane.
7. Type a reply as Alex → confirm it appears live in the browser.
8. Close the chat from Alex's side → if you installed the optional
   close-event BR, confirm the browser shows "This chat has ended."

That's the full loop.
