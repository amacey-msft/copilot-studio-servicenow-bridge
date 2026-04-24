// ============================================================================
// ServiceNow Scripted REST API:  intranet_bridge / send_message
// ============================================================================
// Companion to /open_chat. Posts a message from the requester (Alice) into
// an existing sys_cs_conversation so the live agent (Alex) sees it in the
// SOW chat pane in real time.
//
// IMPLEMENTATION:
//   Uses sn_cs.AgentChatScriptObject.send(convoId, text, attachments, internal)
//   which is the OOB API that performs both:
//     1) sys_cs_message insert
//     2) AMB publish on /cs/messages/<sessId>
//   so the agent's open SOW pane updates live without a refresh.
//
//   Note: send() takes the SENDER from the calling user. Since this REST
//   resource runs as 'intranet.bridge', the avatar will display as IB unless
//   we impersonate the consumer first. Impersonation requires the impersonator
//   role on the caller; if not available, fall back to send() as-is.
//
// REQUEST BODY (JSON):
//   {
//     "conversation_sys_id": "<sys_cs_conversation.sys_id from /open_chat>",
//     "user_sys_id":         "<sys_user.sys_id of author / consumer>",
//     "text":                "<message body>"
//   }
//
// RESPONSE (200):
//   {
//     "message_sys_id":     "<sys_cs_message.sys_id of new row, if resolvable>",
//     "session_sys_id":     "<sys_cs_session.sys_id>",
//     "interaction_sys_id": "<interaction.sys_id>",
//     "impersonated":       true|false,
//     "send_result":        "<DTO from AgentChatScriptObject.send>"
//   }
// ============================================================================

(function process(/*RESTAPIRequest*/ request, /*RESTAPIResponse*/ response) {

  var body = request.body && request.body.data ? request.body.data : {};

  function bad(status, msg) {
    response.setStatus(status);
    response.setBody({ error: msg });
    return;
  }

  var convSid   = body.conversation_sys_id;
  var userSysId = body.user_sys_id;
  var text      = body.text;

  if (!convSid)   return bad(400, 'conversation_sys_id is required');
  if (!userSysId) return bad(400, 'user_sys_id is required');
  if (!text)      return bad(400, 'text is required');

  var c = new GlideRecord('sys_cs_conversation');
  if (!c.get(convSid)) return bad(404, 'sys_cs_conversation not found: ' + convSid);
  var deviceCuid = c.getValue('current_device');

  // Resolve session for the response payload (and binding bump).
  var sess = new GlideRecord('sys_cs_session');
  sess.addQuery('device_id', deviceCuid);
  sess.orderByDesc('sys_created_on');
  sess.setLimit(1);
  sess.query();
  var sessionSysId = sess.next() ? sess.getUniqueValue() : null;

  // Resolve interaction sys_id (for response only).
  var ix = new GlideRecord('interaction');
  ix.addQuery('channel_metadata_document', convSid);
  ix.orderByDesc('sys_created_on');
  ix.setLimit(1);
  ix.query();
  var ixSysId = ix.next() ? ix.getUniqueValue() : null;

  // Best-effort impersonate the consumer so the avatar/sender shows correctly.
  var imp = null;
  var origUser = gs.getUserID() + '';
  var impersonated = false;
  try {
    imp = new GlideImpersonate();
    imp.impersonate(userSysId);
    impersonated = (gs.getUserID() + '') === userSysId;
  } catch (eImp) {
    impersonated = false;
  }

  var sendResult = null;
  var sendError = null;
  try {
    sendResult = sn_cs.AgentChatScriptObject.send(convSid, String(text), null, false);
  } catch (eSend) {
    sendError = String(eSend);
  }

  // Restore identity even if send threw.
  if (imp) {
    try { imp.impersonate(origUser); } catch (eR) { /* ignore */ }
  }

  if (sendError) return bad(500, 'AgentChatScriptObject.send threw: ' + sendError);

  // Try to fish the new message sys_id out of the DTO (shape varies).
  var newMsgId = null;
  try {
    if (sendResult && sendResult.length) {
      var first = sendResult[0];
      if (first && first.sys_id) newMsgId = String(first.sys_id);
      else if (first && first.message && first.message.sys_id) newMsgId = String(first.message.sys_id);
    } else if (sendResult && sendResult.sys_id) {
      newMsgId = String(sendResult.sys_id);
    }
  } catch (eP) { /* ignore */ }

  // Bump session-binding activity to reset VA timeout.
  if (sessionSysId) {
    var sb = new GlideRecord('sys_cs_session_binding');
    sb.addQuery('session', sessionSysId);
    sb.setLimit(1);
    sb.query();
    if (sb.next()) {
      sb.setValue('last_client_activity_time', new GlideDateTime());
      sb.setValue('client_connected', true);
      sb.setValue('online', true);
      sb.setValue('sent_reminder', false);
      sb.update();
    }
  }

  response.setStatus(200);
  response.setBody({
    message_sys_id:     newMsgId,
    session_sys_id:     sessionSysId,
    interaction_sys_id: ixSysId,
    impersonated:       impersonated,
    send_result:        sendResult ? JSON.stringify(sendResult).substring(0, 500) : null
  });

})(request, response);
