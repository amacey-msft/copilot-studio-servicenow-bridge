# Outbound Business Rule

This Business Rule fires whenever a ServiceNow agent sends a message in an
SOW conversation that was opened via this bridge. It posts the agent's
text to the bridge's `/api/servicenow/webhook` endpoint, which forwards
it to the original requester (the browser tab).

## Where to install

- **Table:** `sys_cs_message`
- **Application:** Global
- **Order:** 200
- **Active:** true
- **Advanced:** true (so the script field is editable)

## When to run

| Field          | Value                                                                              |
| -------------- | ---------------------------------------------------------------------------------- |
| When           | `async`                                                                            |
| Insert         | true                                                                               |
| Update         | false                                                                              |
| Delete         | false                                                                              |
| Query          | false                                                                              |
| Filter conditions | `direction is outbound` AND `is_agent is true` AND `q_data_message_type is one of: systemTextMessage, consumerTextMessage` |

The filter is critical. Without `q_data_message_type` filtering you'll
get spurious BR fires on every internal SOW state event.

## Script

Paste this verbatim into the **Advanced > Script** field:

```javascript
(function executeRule(current, previous /*null when async*/) {
  try {
    if (current.getValue("direction") !== "outbound") return;
    if (current.getValue("is_agent") != "1" && String(current.getValue("is_agent")) !== "true") return;
    var qd = String(current.getValue("q_data_message_type") || "");
    if (qd !== "systemTextMessage" && qd !== "consumerTextMessage") return;

    var convId = current.getValue("conversation");
    if (!convId) return;

    // Find the interaction by channel_metadata_document = convId, and ensure
    // it has u_bridge_session_id (only convos opened via /open_chat are bridged).
    var ix = new GlideRecord("interaction");
    ix.addQuery("channel_metadata_document", convId);
    ix.orderByDesc("sys_created_on");
    ix.setLimit(1);
    ix.query();
    if (!ix.next()) return;
    var bridgeSid = ix.getValue("u_bridge_session_id");
    if (!bridgeSid) return;

    var url    = gs.getProperty("intranet_bridge.outbound_webhook_url");
    var secret = gs.getProperty("intranet_bridge.outbound_webhook_secret");
    if (!url) return;

    var payload = {
      bridge_session_id:   bridgeSid,
      conversation_sys_id: convId,
      interaction_sys_id:  ix.getUniqueValue(),
      interaction_number:  ix.getValue("number"),
      message_sys_id:      current.getUniqueValue(),
      sender_sys_id:       current.getValue("sender"),
      q_data_message_type: qd,
      text:                String(current.getValue("payload") || ""),
      send_time:           current.getValue("send_time"),
      sys_created_on:      current.getValue("sys_created_on")
    };

    var rm = new sn_ws.RESTMessageV2();
    rm.setEndpoint(url);
    rm.setHttpMethod("POST");
    rm.setRequestHeader("Content-Type", "application/json");
    rm.setRequestHeader("Accept", "application/json");
    if (secret) rm.setRequestHeader("X-Bridge-Secret", secret);
    rm.setRequestBody(JSON.stringify(payload));
    rm.setHttpTimeout(8000);
    var resp = rm.execute();
    var sc = resp.getStatusCode();
    if (sc < 200 || sc >= 300) {
      gs.warn("[intranet_bridge.outbound] webhook " + url + " returned HTTP " + sc + " body=" + String(resp.getBody() || "").substring(0, 500));
    }
  } catch (e) {
    gs.error("[intranet_bridge.outbound] BR threw: " + e + (e.stack ? "\n" + e.stack : ""));
  }
})(current, previous);
```

## Required `sys_properties`

Create both as **string** type under **System Properties > All Properties**:

| Name                                         | Value                                            |
| -------------------------------------------- | ------------------------------------------------ |
| `intranet_bridge.outbound_webhook_url`       | `https://<your-bridge-host>/api/servicenow/webhook` |
| `intranet_bridge.outbound_webhook_secret`    | A long random string. Must match `SN_WEBHOOK_SECRET` in the bridge env. |

For production, set `intranet_bridge.outbound_webhook_secret` as
**type=password2** so it's masked in the UI.

## Required custom column

The BR depends on `interaction.u_bridge_session_id`. Create it once via
**System Definition > Tables > interaction > Columns > New**:

- Column label: `Bridge session id`
- Column name: `u_bridge_session_id` (auto-generated from label)
- Type: String
- Max length: 64

The `open_chat` Scripted REST resource populates this column when it
creates the interaction.

## Verifying

After installing:

1. Run the bridge's `tools/probe_e2e.ps1` script.
2. In SOW, accept the chat as the test agent and reply with "hello from
   probe".
3. Re-run `Invoke-RestMethod -Uri http://<bridge>/api/servicenow/poll/<sid>`.
4. The poll response should contain your "hello from probe" text.

If the BR doesn't fire, see [`docs/07-troubleshooting.md`](../docs/07-troubleshooting.md).
