# 09 — Production hardening checklist

The reference implementation is correct end-to-end but optimized for
clarity over production rigor. Work through this list before promoting
beyond a dev PDI.

## ServiceNow side

- [ ] **Use a dedicated PDI / sub-prod for staging**, then move to prod
      with the same config. Take notes; the
      [`02-servicenow-setup.md`](02-servicenow-setup.md) checklist is
      designed to be re-run.
- [ ] **Promote via Update Set**, not by re-clicking through the UI.
      Capture the role, user, custom column, Scripted REST API + both
      resources, both `sys_properties`, and the Business Rule into a
      single update set so the prod move is one import.
- [ ] **Restrict Scripted REST role grants narrowly.** Only the bridge
      service account should hold `x_intranet_bridge_caller`. Don't
      re-use the role for human users.
- [ ] **Rotate the `intranet.bridge` password** on a schedule. The
      bridge needs a single env-var update; no code change.
- [ ] **Use a `password2`-typed `sys_property`** for
      `intranet_bridge.outbound_webhook_secret` so it isn't visible in
      casual UI browsing.
- [ ] **Audit the BR script** — anyone with `business_rule_admin` can
      modify it. Treat the Business Rule and Scripted REST scripts as
      production code under change control.

## Bridge side

- [ ] **Persist `BridgeSession` outside the process.** The reference
      `_sessions` dict is in-memory: a worker restart loses every
      in-flight chat. Move to Redis or a small relational table keyed by
      `bridge_session_id`. State to persist:
      - `state` (BOT/QUEUED/LIVE/CLOSED)
      - `interaction_sys_id`, `interaction_number`
      - `conversation_sys_id`, `sn_user_sys_id`
      - `user_email`, `user_display_name`, `rep_name`

      *Status:* the bridge runs on Azure Container Apps as
      `ca-cps-bridge` pinned to `min=max=1` exactly because of this.
      Externalising state is the prerequisite to scaling out and to
      zero-downtime deploys.
- [ ] **Constant-time secret comparison.** Replace
      `secret == SN_WEBHOOK_SECRET` with `hmac.compare_digest(...)`.
      Same for `AGENT_API_SECRET`.
- [ ] **HTTPS only.** Terminate TLS at your ingress; reject plain HTTP.
- [ ] **Rate-limit** the public endpoints, especially
      `/api/servicenow/escalate` (cheap to call, expensive on the SN
      side: each call creates an interaction + queues a work item).
- [ ] **Authentication for `/api/servicenow/escalate` (browser-facing).**
      Today, anyone with a `bridge_session_id` can escalate. Either:
      - Validate the session id against an authenticated user (e.g. SSO
        session cookie), or
      - Remove the browser-facing escalate path entirely and only allow
        Copilot Studio's `/agent/escalate` (with `X-Agent-Secret`).
- [ ] **Bind the consumer identity to the authenticated user.** Replace
      `SN_DEFAULT_USER_SYS_ID` with a real lookup (e.g. SSO claim →
      `sys_user` lookup by email).
- [ ] **Drop `agent_api_secret` from URLs.** The reference accepts it as
      either a header or a query string. For prod, remove the query
      string fallback.
- [ ] **Centralize logging.** Forward bridge logs to your APM /
      observability stack so you can correlate `bridge_session_id`
      across user-message, webhook, and Direct Line activity.
- [ ] **Add health/readiness endpoints** (`/healthz`, `/readyz`) and
      gate them behind your platform's health checks.

## Copilot Studio side

- [ ] **Move secrets out of the Topic editor.** The HTTP action's
      `X-Agent-Secret` header value is visible to anyone with edit
      access to the agent. Use Copilot Studio's *Connectors* with a
      stored credential, or front the bridge with an API gateway that
      injects the secret based on caller identity.
- [ ] **Add a fallback message** when the HTTP action fails — show the
      user something like "Sorry, I couldn't reach a live agent right
      now. Please email support@yourco.com." rather than dying silently.

## Browser side

- [ ] **Don't trust the `bridge_session_id` from query strings or
      `localStorage`.** It's allocated by the server on page load
      (`/api/servicenow/init-session`); always use that result, never a
      cached value from a previous session.
- [ ] **Render rep messages with text-only escaping.** Don't innerHTML
      the `text` field — it comes from a chat message that could
      contain `<script>`-like content. The reference snippets use
      `textContent`.
- [ ] **Handle session expiry.** Decide what to do when the bridge
      returns 404 on poll (e.g. session GC'd because user left the page
      open for hours). Reset to `bot` mode and re-init.

## Operations

- [ ] **Synthetic monitoring.** Run an end-to-end probe (the sequence
      in [`06-end-to-end-test.md`](06-end-to-end-test.md)) on a schedule
      against your prod bridge from outside your network. Alert on
      failure.
- [ ] **Capacity planning.** Each escalation creates a SN
      `sys_cs_conversation`, `interaction`, `sys_cs_session`,
      `sys_cs_session_binding`, `awa_work_item`, plus N
      `sys_cs_message` rows. SN PDIs have very low rate limits; check
      with your SN admin for prod limits.
- [ ] **Disaster recovery.** If the bridge goes down:
      - In-flight chats: agents in SOW can still see and reply to the
        chat, but consumer messages won't reach them and agent replies
        won't reach consumers.
      - New chats: Copilot Studio HTTP action fails → fallback message.
      Have a documented "the bridge is down" playbook (e.g. expose
      `support@yourco.com` and a status page).

## Things explicitly NOT in scope

These are reasonable extensions but not done in the reference:

- Multi-region failover for the bridge.
- File / image attachments in either direction.
- Typing indicators ("Alex is typing…") — would require subscribing to
  additional `sys_cs_*` events or polling the conversation.
- Read receipts.
- Transcript persistence outside ServiceNow.
- Internationalization of the auto-greeting / status messages.
