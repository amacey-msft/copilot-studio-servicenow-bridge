# Outbound Business Rule — Skill Fan-Out (v3 spike)

This is a **second, independent** Business Rule that runs alongside the
existing `outbound_business_rule.md` BR. It posts every CSR message to
the v3 skill's webhook (`/api/sn-webhook` on the ACA-hosted
`teams_a2a`) so handoffs that originated in the new Copilot Studio
agent get the rep's reply.

The original bridge BR is **not modified**. Both BRs fire on every
qualifying `sys_cs_message` insert. The skill silently no-ops on
conversations it doesn't own (it looks up an in-process registry keyed
by `sys_cs_conversation.sys_id` and returns 200 with `matched=false`),
so cross-traffic is safe.

## Where to install

- **Table:** `sys_cs_message`
- **Application:** Global
- **Name:** `Intranet Bridge Outbound (Skill Fan-Out)`
- **Order:** 210 (one slot after the existing BR — order doesn't
  matter functionally because both are async, but separating them
  makes log triage easier)
- **Active:** true
- **Advanced:** true

## When to run

Same trigger as the existing BR:

| Field             | Value                                                                              |
| ----------------- | ---------------------------------------------------------------------------------- |
| When              | `async` *(must be async — sync after-insert BRs cannot make outbound HTTP)*        |
| Insert            | true                                                                               |
| Update            | false                                                                              |
| Delete            | false                                                                              |
| Query             | false                                                                              |
| Filter conditions | `direction is outbound` AND `is_agent is true` AND `q_data_message_type is one of: systemTextMessage, consumerTextMessage` |

## Script

Paste verbatim into **Advanced > Script**:

```javascript
(function executeRule(current, previous /*null when async*/) {
  try {
    if (current.getValue("direction") !== "outbound") return;
    if (current.getValue("is_agent") != "1" && String(current.getValue("is_agent")) !== "true") return;
    var qd = String(current.getValue("q_data_message_type") || "");
    if (qd !== "systemTextMessage" && qd !== "consumerTextMessage") return;

    var convId = current.getValue("conversation");
    if (!convId) return;

    var ix = new GlideRecord("interaction");
    ix.addQuery("channel_metadata_document", convId);
    ix.orderByDesc("sys_created_on");
    ix.setLimit(1);
    ix.query();
    if (!ix.next()) return;
    var bridgeSid = ix.getValue("u_bridge_session_id");
    // We still post for non-bridged convos too so the skill webhook can
    // match on conversation_sys_id; only skip if there's literally no
    // interaction.

    var url    = gs.getProperty("intranet_bridge.skill_webhook_url");
    var secret = gs.getProperty("intranet_bridge.skill_webhook_secret");
    if (!url) return;

    // Resolve the agent's display name so the skill can render
    // "**{rep_name}:** {text}" in Copilot Studio.
    var repName = "";
    var senderId = current.getValue("sender");
    if (senderId) {
      var u = new GlideRecord("sys_user");
      if (u.get(senderId)) repName = String(u.getValue("name") || u.getValue("user_name") || "");
    }

    var payload = {
      bridge_session_id:   bridgeSid || "",
      conversation_sys_id: convId,
      interaction_sys_id:  ix.getUniqueValue(),
      interaction_number:  ix.getValue("number"),
      message_sys_id:      current.getUniqueValue(),
      sender_sys_id:       senderId,
      rep_name:            repName,
      q_data_message_type: qd,
      text:                String(current.getValue("payload") || ""),
      event:               "reply",
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
      gs.warn("[intranet_bridge.skill] webhook " + url + " returned HTTP " + sc + " body=" + String(resp.getBody() || "").substring(0, 500));
    }
  } catch (e) {
    gs.error("[intranet_bridge.skill] BR threw: " + e + (e.stack ? "\n" + e.stack : ""));
  }
})(current, previous);
```

## Required `sys_properties`

Create both as **string** type under **System Properties > All Properties**:

| Name                                       | Value                                                                                                       |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `intranet_bridge.skill_webhook_url`        | `https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io/api/sn-webhook`                   |
| `intranet_bridge.skill_webhook_secret`     | Same value as the existing `intranet_bridge.outbound_webhook_secret` (both webhooks share the bridge `SN_WEBHOOK_SECRET`). For production set as `type=password2`. |

**Why two properties instead of reusing the existing ones?** So you can
later point the skill at a different host (e.g. devtunnel for local
dev) without disturbing the legacy bridge endpoint, and so disabling
the skill BR is a one-flag change (clear `skill_webhook_url`).

## Verifying

1. Trigger a handoff from the new Copilot Studio agent (`IT Help Desk
   Triage Assistant`). The skill should reply with `your ticket is
   IMS####`.
2. Open that interaction in SOW, accept it, type a reply.
3. In Copilot Studio test pane you should see `**{Agent Name}:**
   {reply text}` appear within ~1s.
4. Tail the skill logs:
   ```powershell
   az containerapp logs show -n ca-cps-sn-skill -g rg-cpv-aca --follow --tail 0
   ```
   Look for `[skill] pushed to CS convo=...` for matched conversations
   and `[skill] webhook: no active handoff for ...` for legacy-bridge
   conversations (expected — those are routed by the original BR).

## Removing

Either:
- Set the BR `Active = false`, OR
- Clear `intranet_bridge.skill_webhook_url`.

Either change disables the skill fan-out without touching the legacy
bridge path.
