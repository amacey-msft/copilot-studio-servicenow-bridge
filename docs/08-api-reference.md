# 08 — API & table reference

Every ServiceNow API, table, role, and configuration object touched by this
integration, with the rationale for why it's needed. Use this as the
authoritative "what does this thing do" reference.

## ServiceNow APIs called

### `sn_cs.VASystemObject.createConversation(userSysId)`

- **Where used:** `servicenow/open_chat.js`, step 1.
- **Return:** `string` — sys_id of the new `sys_cs_conversation`.
- **Why we use it:** The supported entry point for creating a conversation
  outside the OOB Virtual Agent UI. Critically, it populates the
  conversation's `context` column with a serialized `ConversationContext`
  blob (~1.9 KB compressed) that downstream APIs unpack at runtime.
  Without it, every agent reply throws an NPE in
  `ConversationContext.getBrandingKey()`.
- **What it does as a side effect:** creates a `sys_user_consumer`,
  `sys_user_consumer_account`, and an `interaction` row whose
  `channel_metadata_document` field points back to the conversation.
- **Caveats:** Sets `interaction.assigned_to = Virtual Agent`. You must
  clear this before you want AWA to route the interaction to a human.

### `sn_cs.AgentChatScriptObject.send(convoId, text, attachments, internal)`

- **Where used:** `servicenow/send_message.js`.
- **Why we use it:** The *only* supported API that performs both a
  `sys_cs_message` insert and an AMB publish on `/cs/messages/<sessId>`.
  Raw `GlideRecord` inserts persist the row but the agent's pane never
  updates because no AMB frame is published.
- **Sender:** comes from `gs.getUserID()` of the calling thread. Use
  `GlideImpersonate` if you need it to be the consumer rather than the
  service account.

### `sn_ws.RESTMessageV2`

- **Where used:** Outbound BR (§8 of [`02-servicenow-setup.md`](02-servicenow-setup.md)).
- **Why we use it:** Standard server-side HTTP client. We call `execute()`
  inside an `async` BR (so we don't block the message insert) and log
  non-2xx responses via `gs.warn`.

### `gs.getProperty(name)` / `gs.setProperty(name, value)`

- **Where used:** Outbound BR reads `intranet_bridge.outbound_webhook_url`
  and `intranet_bridge.outbound_webhook_secret`.
- **Why we use it:** Lets you change the webhook URL or secret without
  modifying the BR script.

## ServiceNow tables touched

| Table                     | Operation         | Purpose                                                                     |
| ------------------------- | ----------------- | --------------------------------------------------------------------------- |
| `sys_user`                | read              | Validate `user_sys_id`. Source the consumer identity for impersonation.    |
| `sys_cs_conversation`     | create (via API), update | The conversation. Created by `createConversation()`; we then set `state=chatInProgress`, `live_agent_transfer_time`, `opened_for`, `title`. |
| `sys_user_consumer`       | created by API    | Consumer record linked to the conversation.                                 |
| `sys_user_consumer_account` | created by API  | Consumer account linked to the conversation.                                |
| `sys_cs_session`          | insert            | Marks a live channel session for the conversation. Critically, `interaction` is an enum string `'QnA'`, **not** a sys_id reference. |
| `sys_cs_session_binding`  | insert, update    | Links the session to the conversation via `system_topic` and `topic` columns (both store the conv sys_id despite the misleading names). `channel_type='AMB'` is what makes the OOB resolver find the session. We `update` it on every consumer message to bump `last_client_activity_time` and reset the VA timeout. |
| `sys_cs_message`          | insert            | Each chat message. Created indirectly via `AgentChatScriptObject.send()` for consumer messages, and directly by SN itself for agent messages. |
| `interaction`             | update            | The IMS row visible in lists and reports. We add `u_bridge_session_id`, set `direction='inbound'`, `type='chat'`, `subtype='mweb'`, `state='new'`, and **clear** `assigned_to`. |
| `interaction.u_bridge_session_id` | added (custom column) | Lets the outbound BR correlate replies back to the right browser session. |
| `awa_work_item`           | insert + update   | The AWA routing primitive. We insert with empty state, then update to `queued` so the "Try to assign workItem" BR observes the change and assigns the item. |
| `sys_security_acl`        | (read for diag)   | Confirmed there is no public create ACL on `sys_cs_conversation`.           |
| `sys_properties`          | created (2 rows)  | `intranet_bridge.outbound_webhook_url` and `intranet_bridge.outbound_webhook_secret`. |
| `sys_script` (Business Rule) | created (1 row) | `Intranet Bridge: relay outbound chat to webhook` on `sys_cs_message`.     |
| `sys_user`                | created (1 row)   | The `intranet.bridge` service account.                                      |
| `sys_user_role`           | created (1 row)   | The `x_intranet_bridge_caller` custom role.                                 |
| `sys_user_has_role`       | created (multiple)| Role grants for `intranet.bridge`.                                          |

## ServiceNow web services / endpoints created

| Path                                           | Method | Auth role                  | Source file                                  |
| ---------------------------------------------- | ------ | -------------------------- | -------------------------------------------- |
| `/api/<ns>/intranet_bridge/open_chat`          | POST   | `x_intranet_bridge_caller` | [`servicenow/open_chat.js`](../servicenow/open_chat.js)       |
| `/api/<ns>/intranet_bridge/send_message`       | POST   | `x_intranet_bridge_caller` | [`servicenow/send_message.js`](../servicenow/send_message.js) |

`<ns>` is the namespace ServiceNow assigns to the Scripted REST API
record (visible on the resource form, e.g. `1833944`).

## Bridge endpoints (HTTP / WS)

See [`03-bridge-backend.md`](03-bridge-backend.md) for the full surface,
including request/response shapes.

## Why we did NOT use these alternatives

Each of the below was attempted and rejected. Documented to save the
next person from re-trying them.

| Approach                                                     | Why it failed                                                                     |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `POST /api/now/table/interaction`                            | Inserts an IMS row but `awa_work_item` is never created — AWA only triggers on `sys_cs_conversation` insert, not `interaction` insert. Verified by inserting IMS0000054 and querying `awa_work_item?document_id=<sys_id>` → empty. |
| Raw `GlideRecord` insert into `sys_cs_conversation`          | `context` column is null, agent's first reply NPEs in `ConversationContext.getBrandingKey()`. |
| `POST /api/now/cs_message`                                   | This is the AMB widget endpoint, not a public API. Requires a `guest_session_identifier` cookie, an `amb-channel` header, and a previously-established conversation via the widget's WebSocket. Cannot be cleanly called server-to-server. |
| `sn_cs.VASystemObject.authorizeAndEnqueueMessage(...)`       | Tested in 6 different argument shapes; all returned `ERROR` or `false`. Internal API not designed for this use case. |
| Raw `GlideRecord` insert into `sys_cs_message`               | Persists the row but no AMB publish occurs, so the live agent's pane stays empty. The supported path is `AgentChatScriptObject.send()`. |
| Using the `interaction.work_notes` field to relay user text  | Visible to the agent in the Activity log but doesn't appear in the chat pane. Loses the chat conversation metaphor entirely. |

## Appendix: tables that don't exist on Yokohama PDIs

When debugging, you'll find references in older blog posts to these
tables that have been renamed or removed. None of these exist on a
clean Yokohama PDI:

- `sys_ws_operation_role` (replaced by ACLs on Scripted REST resources)
- `sys_script_object`
- `sys_transaction_log`
- `sys_graphql_field_data_fetcher`
- `awa_agent`, `awa_agent_state`, `awa_agent_queue`, `awa_agent_skill_set`
  (presence is now tracked in `sys_user_presence` and capacity in
  `awa_agent_capacity`)

## Appendix: roles required to install everything

To set up the integration manually (i.e. to do the steps in
[`02-servicenow-setup.md`](02-servicenow-setup.md)) you need `admin`.

To **call** the integration day-to-day (i.e. what the bridge service
account needs) you need `x_intranet_bridge_caller` plus the read roles
listed in [`02-servicenow-setup.md`](02-servicenow-setup.md) §3.
