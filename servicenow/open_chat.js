// ============================================================================
// ServiceNow Scripted REST API:  intranet_bridge / open_chat
// ============================================================================
// Creates a properly-initialized live-chat conversation for AWA routing.
//
// Key insight: sys_cs_conversation must be created via the Java-backed
// `sn_cs.VASystemObject.createConversation()` API, NOT via raw GlideRecord
// insert. Only the API populates the compressed `context` blob that the
// downstream resolver `sn_cs.AgentChatScriptObject.send()` requires; without
// it, every agent reply NPEs in `ConversationContext.getBrandingKey()`.
//
// Flow:
//   1. sn_cs.VASystemObject.createConversation(userSysId)
//        -> mints sys_cs_conversation + interaction + consumer + consumer_account
//        -> populates context (1.9KB compressed ConversationContext blob)
//        -> returns convoId
//   2. Insert sys_cs_session  (interaction='QnA' enum, NOT a sys_id ref)
//   3. Insert sys_cs_session_binding  (channel_type='AMB', binds session to conv)
//   4. Optionally insert first inbound sys_cs_message
//   5. Update interaction with bridge metadata + AWA-friendly fields
//   6. Insert awa_work_item with state='queued' to fire AWA routing BR
//
// Discovered errors that this avoids:
//   - "ConversationContext.getBrandingKey()" NPE (missing context)
//   - "Can't find a live session" (missing sys_cs_session)
//   - "InteractionType.<sys_id>" (sys_cs_session.interaction must be enum string)
//   - "IChannelSession.isMessageBatching()" NPE (missing sys_cs_session_binding)
//
// Endpoint: POST /api/1833944/intranet_bridge/open_chat
//
// REQUEST BODY (JSON):
//   {
//     "user_sys_id":         "<sys_user.sys_id of the requester>",
//     "short_description":   "Free-text reason for handoff",
//     "bridge_session_id":   "<our internal session id>",
//     "channel_sys_id":      "<awa_service_channel.sys_id>  (Chat)",
//     "queue_sys_id":        "<awa_queue.sys_id>            (IT Help Chat)",
//     "first_message":       "<optional inbound message text>"
//   }
// ============================================================================

(function process(/*RESTAPIRequest*/ request, /*RESTAPIResponse*/ response) {

  var VENDOR_SERVICENOW       = 'c2f0b8f187033200246ddd4c97cb0bb9';
  var VIRTUAL_AGENT_SYS_USER  = 'cda46ca8f54c03100a22c0b3dfa1514c';

  var body = request.body && request.body.data ? request.body.data : {};

  function bad(status, msg) {
    response.setStatus(status);
    response.setBody({ error: msg });
    return;
  }

  // ---- input validation ---------------------------------------------------
  var userSysId  = body.user_sys_id;
  var shortDesc  = body.short_description || 'Intranet bridge handoff';
  var bridgeSid  = body.bridge_session_id;
  var channelSid = body.channel_sys_id;
  var queueSid   = body.queue_sys_id;
  var firstMsg   = body.first_message || '';

  if (!userSysId)  return bad(400, 'user_sys_id is required');
  if (!bridgeSid)  return bad(400, 'bridge_session_id is required');
  if (!channelSid) return bad(400, 'channel_sys_id is required');
  if (!queueSid)   return bad(400, 'queue_sys_id is required');

  var u = new GlideRecord('sys_user');
  if (!u.get(userSysId)) return bad(404, 'sys_user not found: ' + userSysId);

  // ---- 1) mint conversation via internal API ------------------------------
  // This call is what populates `context` with a valid serialized
  // ConversationContext (branding_key, channel session refs, etc.).
  var convoId;
  try {
    convoId = sn_cs.VASystemObject.createConversation(userSysId);
  } catch (eC) {
    return bad(500, 'sn_cs.VASystemObject.createConversation threw: ' + eC);
  }
  if (!convoId) return bad(500, 'createConversation returned empty id');

  var conv = new GlideRecord('sys_cs_conversation');
  if (!conv.get(convoId)) return bad(500, 'created convo not found: ' + convoId);

  var deviceCuid    = conv.getValue('current_device');
  var consumerSysId = conv.getValue('consumer');
  var accountSysId  = conv.getValue('consumer_account');

  // Promote conversation to chatInProgress + add bridge metadata.
  conv.setValue('state', 'chatInProgress');
  conv.setValue('live_agent_transfer_time', new GlideDateTime());
  conv.setValue('opened_for', userSysId);
  conv.setValue('title', 'Intranet bridge: ' + shortDesc.substring(0, 80));
  conv.update();

  // ---- 2) sys_cs_session --------------------------------------------------
  // interaction column on sys_cs_session is an InteractionType ENUM string,
  // NOT a reference to the interaction table. Use 'QnA' (matches OOB).
  var sessionSysId = '';
  var sessionError = '';
  try {
    var sess = new GlideRecord('sys_cs_session');
    sess.initialize();
    sess.setValue('consumer',         consumerSysId);
    sess.setValue('consumer_account', accountSysId);
    sess.setValue('vendor',           VENDOR_SERVICENOW);
    sess.setValue('device_id',        deviceCuid);
    sess.setValue('device_type',      'mweb');
    sess.setValue('direction',        'inbound');
    sess.setValue('interaction',      'QnA');
    sess.setValue('start_time',       new GlideDateTime());
    sessionSysId = sess.insert();
    if (!sessionSysId) sessionError = sess.getLastErrorMessage() || 'insert returned no sys_id';
  } catch (eS) {
    sessionError = String(eS);
  }

  // ---- 3) sys_cs_session_binding ------------------------------------------
  // The "channelSession" record loaded by the resolver. system_topic and topic
  // both store the conversation sys_id (despite the misleading column names).
  var bindingSysId = '';
  var bindingError = '';
  if (sessionSysId) {
    try {
      var sb = new GlideRecord('sys_cs_session_binding');
      sb.initialize();
      sb.setValue('session',                   sessionSysId);
      sb.setValue('system_topic',              convoId);
      sb.setValue('topic',                     convoId);
      sb.setValue('channel_type',              'AMB');
      sb.setValue('channel_id',                '/cs/messages/' + sessionSysId);
      sb.setValue('client_connected',          true);
      sb.setValue('online',                    true);
      sb.setValue('guest',                     false);
      sb.setValue('last_client_activity_time', new GlideDateTime());
      bindingSysId = sb.insert();
      if (!bindingSysId) bindingError = sb.getLastErrorMessage() || 'insert returned no sys_id';
    } catch (eB) {
      bindingError = String(eB);
    }
  }

  // ---- 4) optional first inbound message ----------------------------------
  var firstMessageSysId = '';
  if (firstMsg && sessionSysId) {
    try {
      var msg = new GlideRecord('sys_cs_message');
      msg.initialize();
      msg.setValue('conversation',        convoId);
      msg.setValue('session',             sessionSysId);
      msg.setValue('consumer',            consumerSysId);
      msg.setValue('consumer_account',    accountSysId);
      msg.setValue('vendor',              VENDOR_SERVICENOW);
      msg.setValue('direction',           'inbound');
      msg.setValue('message_type',        'text');
      msg.setValue('q_data_message_type', 'consumerTextMessage');
      msg.setValue('payload',             firstMsg);
      msg.setValue('message_size',        ('' + firstMsg).length);
      msg.setValue('sender',              userSysId);
      msg.setValue('recipient',           VIRTUAL_AGENT_SYS_USER);
      msg.setValue('status',              'received');
      msg.setValue('send_time',           new GlideDateTime());
      msg.setValue('receive_time',        new GlideDateTime());
      firstMessageSysId = msg.insert() || '';
    } catch (eM) { /* non-fatal */ }
  }

  // ---- 5) locate / update the interaction --------------------------------
  // createConversation already created an interaction with
  // channel_metadata_document=convoId. Update it with bridge metadata + AWA fields.
  var ixId = '';
  var ixNumber = '';
  var ix = new GlideRecord('interaction');
  ix.addQuery('channel_metadata_document', convoId);
  ix.setLimit(1);
  ix.query();
  if (ix.next()) {
    ixId = ix.getUniqueValue();
    // createConversation pre-sets assigned_to=Virtual Agent. Clear it so AWA
    // will route to a live agent and so the agent's "Assigned to you" Inbox
    // includes this row after acceptance.
    try { ix.setValue('assigned_to',         ''); }          catch(eX){}
    try { ix.setValue('opened_for',          userSysId); }   catch(eX){}
    try { ix.setValue('opened_by',           userSysId); }   catch(eX){}
    try { ix.setValue('short_description',   shortDesc); }   catch(eX){}
    try { ix.setValue('u_bridge_session_id', bridgeSid); }   catch(eX){}
    try { ix.setValue('direction',           'inbound'); }   catch(eX){}
    try { ix.setValue('type',                'chat'); }      catch(eX){}
    try { ix.setValue('subtype',             'mweb'); }      catch(eX){}
    try { ix.setValue('state',               'new'); }       catch(eX){}
    ix.update();
    ix.get(ixId); // refresh to read .number
    ixNumber = ix.getValue('number') || '';

    // Link first inbound message to this interaction (task field).
    if (firstMessageSysId) {
      var fm = new GlideRecord('sys_cs_message');
      if (fm.get(firstMessageSysId)) {
        fm.setValue('task', ixId);
        fm.update();
      }
    }
  }

  // ---- 6) AWA work item ---------------------------------------------------
  // The "Try to assign workItem" BR fires AFTER UPDATE on state CHANGES TO queued.
  // Insert with empty state, then UPDATE to queued so the change is observed.
  var workItemSysId = '';
  var workItemError = '';
  if (ixId) {
    try {
      var wi = new GlideRecord('awa_work_item');
      wi.initialize();
      wi.setValue('document_id',    ixId);
      wi.setValue('document_table', 'interaction');
      wi.setValue('queue',          queueSid);
      wi.setValue('active',         true);
      workItemSysId = wi.insert();
      if (!workItemSysId) {
        workItemError = wi.getLastErrorMessage() || 'insert returned no sys_id';
      } else {
        var wi2 = new GlideRecord('awa_work_item');
        if (wi2.get(workItemSysId)) {
          wi2.setValue('state', 'queued');
          wi2.update();
        }
      }
    } catch (eW) {
      workItemError = String(eW);
    }
  }

  // ---- respond ------------------------------------------------------------
  response.setStatus(200);
  response.setBody({
    conversation_sys_id:     convoId,
    interaction_sys_id:      ixId,
    interaction_number:      ixNumber,
    consumer_sys_id:         consumerSysId,
    consumer_account_sys_id: accountSysId,
    session_sys_id:          sessionSysId,
    session_error:           sessionError,
    session_binding_sys_id:  bindingSysId,
    session_binding_error:   bindingError,
    device_cuid:             deviceCuid,
    first_message_sys_id:    firstMessageSysId,
    work_item_sys_id:        workItemSysId,
    work_item_error:         workItemError,
    queue_sys_id:            queueSid,
    channel_sys_id:          channelSid
  });

})(request, response);
