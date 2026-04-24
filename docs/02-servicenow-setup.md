# 02 — ServiceNow setup (web UI walkthrough)

This is the entire ServiceNow side, done through the regular web UI. No
Background Scripts, no `/exec`, no update sets. Steps are in order; do not
skip.

> Verified end-to-end on a **ServiceNow Yokohama** Personal Developer Instance
> (PDI), April 2026. Steps should be identical or very close on Vancouver,
> Washington DC, and Xanadu.

## Time required

About 30–45 minutes the first time, ~15 minutes once you've done it once.

## Prerequisites

- A ServiceNow instance you have **`admin`** on (PDI is fine).
- A test consumer user (e.g. `alice.employee`) and a test live agent
  (e.g. `alex.rep`) with the AWA setup described in §3.
- The reference Scripted REST source files from this repo:
  - [`servicenow/open_chat.js`](../servicenow/open_chat.js)
  - [`servicenow/send_message.js`](../servicenow/send_message.js)

You will copy/paste those two files verbatim into ServiceNow.

---

## 1. Verify required plugins

*System Definition → Plugins.* Search for each and confirm **Active**:

| Plugin ID                                | Display name                            |
| ---------------------------------------- | --------------------------------------- |
| `com.glide.interaction`                  | Interaction (base)                      |
| `com.glide.interaction.awa`              | Advanced Work Assignment                |
| `com.glide.cs.collab`                    | Collaborative Chat Server               |
| `com.glide.cs.custom.adapter`            | Conversational Custom Chat Integration  |
| `com.glide.ccci.clients.utils`           | CCCI client utilities                   |
| `com.glide.service-portal.agent-chat`    | Service Portal Agent Chat               |

On a CSM-flavoured PDI all six are usually already Active. If any are not,
activate them now. Activation is free on PDIs.

---

## 2. Create the custom role

*User Administration → Roles → New.*

| Field         | Value                                                      |
| ------------- | ---------------------------------------------------------- |
| Name          | `x_intranet_bridge_caller`                                 |
| Description   | `Allowed to call the intranet_bridge Scripted REST API.`   |

Submit.

---

## 3. Create the service account

*User Administration → Users → New.*

| Field                          | Value                          |
| ------------------------------ | ------------------------------ |
| User ID                        | `intranet.bridge`              |
| First / Last name              | `Intranet` / `Bridge`          |
| Internal Integration User      | ✅                              |
| Web service access only        | ✅ (recommended)               |
| Password                       | (generate something strong)    |

Submit, then re-open the user record. In the **Roles** related list, add:

| Role                              | Reason                                                                   |
| --------------------------------- | ------------------------------------------------------------------------ |
| `snc_internal`                    | Baseline internal API access.                                            |
| `interaction_integration_user`    | Insert/update on `interaction`.                                          |
| `awa_manager`                     | Read on `awa_service_channel`, `awa_queue`, etc.                         |
| `chat_admin`                      | Read on `sys_cs_conversation`, `sys_cs_session`, `sys_cs_session_binding`. |
| `x_intranet_bridge_caller`        | The custom role from §2 — gates the Scripted REST resources.             |

> **Do not** try to add `interaction_admin`. It does not exist on Yokohama PDIs.

Save the password somewhere your bridge backend can read it (env var
`SN_PASSWORD`).

---

## 4. Look up AWA configuration sys_ids

You will need these as **input** to the bridge (env vars or request body).
Don't recreate them — use what your instance already has.

For each row, navigate to the listed table, open the record, and copy its
`sys_id` from the URL (`...sys_id=<copy-this>`) or from *Show XML*.

| Table                  | Filter / record                       | Variable name                       |
| ---------------------- | ------------------------------------- | ----------------------------------- |
| `awa_service_channel`  | `Name = Chat`                         | `SN_DEFAULT_CHANNEL_SYS_ID`         |
| `awa_queue`            | `Name = IT Help Chat` (or yours)      | `SN_DEFAULT_QUEUE_SYS_ID`           |
| `sys_user`             | Your test consumer (`alice.employee`) | `SN_DEFAULT_USER_SYS_ID`            |
| `sys_user`             | Your test agent (`alex.rep`)          | (used for verification only)        |
| `sys_user_group`       | The group that owns the queue (e.g. `Service Desk`) | (no env var)          |

The queue must be configured with:

- **Service channel** = `Chat`
- **Eligible group** = your group (e.g. `Service Desk`)
- **Order** = `100` (low number wins on ties)
- **Assignment rule** = `Chat Assignment Rule` (OOB)

The agent (`alex.rep`) must:

- Be a member of the eligible group.
- Have role `awa_agent`.
- Have an `awa_agent_capacity` row for channel `Chat`. This auto-creates the
  first time they sign in to Agent Workspace and pick *Available* in the
  presence selector.
- Be **Available** in the AWA presence selector right now (top right of
  Agent Workspace).

If you can't find a queue named `IT Help Chat`, create one:
*Workspace Experience → Advanced Work Assignment → Queues → New.*

---

## 5. Add the custom column on `interaction`

Lets the outbound Business Rule (§8) tell which interactions belong to
the bridge.

*System Definition → Tables → `interaction` → Columns related list → New.*

| Field        | Value                  |
| ------------ | ---------------------- |
| Column label | `Bridge Session ID`    |
| Column name  | `u_bridge_session_id`  |
| Type         | `String`               |
| Max length   | `64`                   |

Submit. ServiceNow will issue a confirmation about extending an OOB table —
accept it.

---

## 6. Create the Scripted REST API

*System Web Services → Scripted REST APIs → New.*

| Field         | Value                |
| ------------- | -------------------- |
| Name          | `Intranet Bridge`    |
| API ID        | `intranet_bridge`    |

Submit. Note the auto-generated **namespace** ServiceNow shows on the form
(e.g. `1833944`). You'll need it for the URL the bridge calls — but the
URL form `/api/<namespace>/intranet_bridge/<resource>` is shown on each
resource page, so just copy from there later.

### 6a. Add resource: `open_chat`

In the new API record, **Resources** related list → **New**.

| Field                             | Value                                                |
| --------------------------------- | ---------------------------------------------------- |
| Name                              | `open_chat`                                          |
| HTTP method                       | `POST`                                               |
| Relative path                     | `/open_chat`                                         |
| Requires authentication           | ✅                                                   |
| Requires ACL authorization        | ⬜ (unchecked)                                       |

In the **Security** tab → **Required role**: `x_intranet_bridge_caller`.

In the **Script** field, paste the **entire contents** of
[`servicenow/open_chat.js`](../servicenow/open_chat.js).

Submit.

### 6b. Add resource: `send_message`

Repeat with these values:

| Field                             | Value                                                |
| --------------------------------- | ---------------------------------------------------- |
| Name                              | `send_message`                                       |
| HTTP method                       | `POST`                                               |
| Relative path                     | `/send_message`                                      |
| Requires authentication           | ✅                                                   |
| Requires ACL authorization        | ⬜                                                   |
| Required role (Security tab)      | `x_intranet_bridge_caller`                           |

In **Script**, paste [`servicenow/send_message.js`](../servicenow/send_message.js).

Submit.

### 6c. Note your endpoints

Open each resource record. Each shows a **Resource path** like:

```
/api/1833944/intranet_bridge/open_chat
/api/1833944/intranet_bridge/send_message
```

The full URLs your bridge will call are:

```
https://<your-instance>.service-now.com/api/1833944/intranet_bridge/open_chat
https://<your-instance>.service-now.com/api/1833944/intranet_bridge/send_message
```

Set `SN_BRIDGE_API_BASE=/api/1833944/intranet_bridge` in your bridge `.env`.

---

## 7. Smoke-test `/open_chat` from the SN UI

Before continuing, prove the endpoint works.

*System Web Services → Scripted REST APIs → Intranet Bridge → open_chat.*
At the top, click **Explore REST API** (or use any REST client outside
ServiceNow).

Method: `POST`. Body (replace the sys_ids with values from §4):

```json
{
  "user_sys_id":       "<alice sys_id>",
  "short_description": "Smoke test: external bridge handoff",
  "bridge_session_id": "smoke-test-001",
  "channel_sys_id":    "<Chat channel sys_id>",
  "queue_sys_id":      "<IT Help Chat queue sys_id>",
  "first_message":     "Hello from /open_chat smoke test"
}
```

Authorize as `intranet.bridge` (basic auth).

**Expected `200`** body:

```json
{
  "conversation_sys_id":     "...",
  "interaction_sys_id":      "...",
  "interaction_number":      "IMS00000XX",
  "consumer_sys_id":         "...",
  "consumer_account_sys_id": "...",
  "session_sys_id":          "...",
  "session_binding_sys_id":  "...",
  "device_cuid":             "...",
  "first_message_sys_id":    "...",
  "work_item_sys_id":        "...",
  "queue_sys_id":            "...",
  "channel_sys_id":          "..."
}
```

Then within ~5 s, with your test agent **Available** in Agent Workspace,
they should see a chat invitation in their AWA Inbox. Verify by querying
the `awa_work_item` table: filter `Document = <interaction_sys_id>` and
confirm `state = offered` or `accepted`, with `Assigned to = alex.rep`.

If this works, the routing pipeline is wired correctly. Move on.

If it doesn't, see [`07-troubleshooting.md`](07-troubleshooting.md) — every
common failure is documented there.

---

## 8. Outbound Business Rule

This BR makes agent replies flow back to the bridge.

*System Definition → Business Rules → New.*

| Field           | Value                                                  |
| --------------- | ------------------------------------------------------ |
| Name            | `Intranet Bridge: relay outbound chat to webhook`      |
| Table           | `Open Chat Message [sys_cs_message]`                   |
| Active          | ✅                                                     |
| Advanced        | ✅                                                     |
| When            | `async`                                                |
| Insert          | ✅                                                     |
| Order           | `200`                                                  |

Click the **Filter Conditions** tab and set:

```
direction          IS               outbound
is_agent           IS               true
q_data_message_type IS one of       systemTextMessage, consumerTextMessage
```

Click the **Advanced** tab and paste this script into **Script**:

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

Submit.

---

## 9. Two `sys_properties` for the webhook

*System Properties → All Properties → New* (do this twice).

### 9a. URL property

| Field        | Value                                                                     |
| ------------ | ------------------------------------------------------------------------- |
| Name         | `intranet_bridge.outbound_webhook_url`                                    |
| Description  | URL the outbound BR posts agent replies to.                               |
| Type         | `string`                                                                  |
| Value        | The publicly reachable URL of your bridge's `POST /api/servicenow/webhook`. During development, a VS Code dev tunnel works (e.g. `https://abc-5000.use.devtunnels.ms/api/servicenow/webhook`). |

### 9b. Secret property

| Field        | Value                                                                     |
| ------------ | ------------------------------------------------------------------------- |
| Name         | `intranet_bridge.outbound_webhook_secret`                                 |
| Description  | Shared secret sent in the `X-Bridge-Secret` header. Must match `SN_WEBHOOK_SECRET` in the bridge `.env`. |
| Type         | `password2` (or `string` if password2 is unavailable)                     |
| Value        | Generate something opaque, e.g. `openssl rand -base64 32`.                |

When your tunnel URL or the secret changes, just update the property value
— no code change needed.

---

## 10. Configure the test agent's presence

In Agent Workspace (sign in as `alex.rep`):

1. Click the presence pill in the top right.
2. Set to **Available**.
3. Confirm the AWA Inbox icon appears in the left rail.

---

## 11. Smoke-test the BR

Run the same `/open_chat` request as in §7. Once Alex accepts and types a
reply, watch *System Logs → System Log → All*. Filter
`Message contains intranet_bridge`. You should see one of:

- `HTTP 200` — the bridge accepted it. Round-trip is live.
- `HTTP 403 forbidden` — your `intranet_bridge.outbound_webhook_secret`
  doesn't match the bridge's `SN_WEBHOOK_SECRET`. Update one.
- `HTTP 0` or connection error — your bridge URL isn't reachable from the
  internet. If you're using a dev tunnel, the URL changed (or the tunnel
  isn't running) — update §9a.
- `HTTP 404 session not found` — the BR fired and reached the bridge, but
  the bridge doesn't have a session matching that `bridge_session_id`. This
  is expected if you're calling `/open_chat` directly with a synthetic
  bridge session id (like the smoke test). When the real bridge owns the
  session lifecycle, this won't happen.

---

## 12. (Optional) Close-event Business Rule

If you want the bridge to know when the SN agent ends the chat (so the
browser switches the UI to "closed"):

*System Definition → Business Rules → New.*

| Field    | Value                                                                       |
| -------- | --------------------------------------------------------------------------- |
| Name     | `Intranet Bridge: relay close to webhook`                                   |
| Table    | `interaction`                                                               |
| Active   | ✅                                                                          |
| When     | `async`                                                                     |
| Update   | ✅                                                                          |
| Filter   | `state changes to closed_complete` AND `u_bridge_session_id is not empty`   |

Script:

```javascript
(function executeRule(current, previous) {
  var url    = gs.getProperty("intranet_bridge.outbound_webhook_url");
  var secret = gs.getProperty("intranet_bridge.outbound_webhook_secret");
  if (!url) return;

  var payload = {
    event:               "closed",
    bridge_session_id:   current.getValue("u_bridge_session_id"),
    interaction_sys_id:  current.getUniqueValue(),
    interaction_number:  current.getValue("number")
  };

  var rm = new sn_ws.RESTMessageV2();
  rm.setEndpoint(url);
  rm.setHttpMethod("POST");
  rm.setRequestHeader("Content-Type", "application/json");
  if (secret) rm.setRequestHeader("X-Bridge-Secret", secret);
  rm.setRequestBody(JSON.stringify(payload));
  rm.setHttpTimeout(8000);
  rm.executeAsync();
})(current, previous);
```

---

## 13. Tear-down (for re-provisioning)

To completely remove the integration from a PDI:

1. *System Web Services → Scripted REST APIs* → delete `Intranet Bridge`
   (this removes both resources).
2. *System Definition → Business Rules* → delete
   `Intranet Bridge: relay outbound chat to webhook` (and the close BR
   if you added it).
3. *System Properties → All Properties* → delete
   `intranet_bridge.outbound_webhook_url` and
   `intranet_bridge.outbound_webhook_secret`.
4. *System Definition → Tables → interaction → Columns* → delete
   `u_bridge_session_id`.
5. *User Administration → Roles* → delete `x_intranet_bridge_caller`.
6. *User Administration → Users* → delete `intranet.bridge`.

---

## What you have now

A ServiceNow instance that:

- Accepts `POST /api/<ns>/intranet_bridge/open_chat` and returns an
  AWA-routed `IMS#######` interaction visible to your live agent.
- Accepts `POST /api/<ns>/intranet_bridge/send_message` and pushes a
  consumer message into the live chat (visible in the SOW pane in real
  time).
- POSTs every agent reply to your bridge's webhook URL with a shared-secret
  header.

Next: stand up the bridge that calls these endpoints — see
[`03-bridge-backend.md`](03-bridge-backend.md).
