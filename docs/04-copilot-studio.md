# 04 — Copilot Studio configuration

You will add **one new topic** to your existing Copilot Studio agent and
make sure your webchat uses the bridge's session id as the Direct Line
`User.Id`. That's it.

## Prerequisites

- An existing Copilot Studio agent answering questions in your webchat.
- The bridge from [`03-bridge-backend.md`](03-bridge-backend.md) running and
  reachable from the public internet (so Copilot Studio's cloud can call
  it).
- The two shared secrets from your `.env`:
  - `AGENT_API_SECRET` — Copilot Studio sends this to the bridge.
  - (and `SN_WEBHOOK_SECRET` — used by ServiceNow, not Copilot Studio)

> Per Microsoft guidance, build new agents with **generative orchestration**
> unless you have a specific reason not to. Steps below assume that.

## 1. Pass the bridge session id through Direct Line

The bridge needs to know which `BridgeSession` to escalate. The cleanest way
is: have your web page call `/api/servicenow/init-session` first, then use
the returned `session_id` as the Direct Line `User.Id`. Copilot Studio
exposes this as `User.Id` (a system variable) inside topics, so the topic
can pass it back to the bridge.

```javascript
// In your webchat bootstrap:
const init = await fetch('/api/servicenow/init-session', { method: 'POST' });
const { session_id } = await init.json();

const tokenResp = await fetch('/directline/token', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ user_id: session_id }),  // <-- key line
});
const { token } = await tokenResp.json();

const directLine = window.WebChat.createDirectLine({ token });

// Always include from.id when posting so Copilot Studio sees a stable user.id:
directLine.postActivity({
  from: { id: session_id },
  type: 'message',
  text: userInput,
}).subscribe();
```

Confirm this works by sending a message in the chat, then in Copilot Studio
turning on the test pane and looking at the *Variables* — `User.Id` should
equal the session id you generated.

## 2. Create the Escalate topic

In Copilot Studio:

1. Open your agent → **Topics** → **+ Add a topic** → **From blank**.
2. Name: `Escalate to live agent`.
3. **Description** (matters for generative orchestration): something like:

   > Use this topic when the user asks to talk to a person, a human, an
   > agent, support, or otherwise indicates the bot can't help and they
   > want a live human. Also use it when the user is clearly frustrated
   > with bot answers and asks for escalation.

4. (Optional) **Trigger phrases** as a safety net for classic orchestration:
   *"talk to a human"*, *"agent please"*, *"speak to a person"*,
   *"this isn't helping"*, *"escalate"*.

## 3. Add the HTTP request action

Inside the topic, after any clarifying message ("Let me connect you with a
live agent…"):

1. **+ Add a node → Send a message** (optional, e.g. "Connecting you now…").
2. **+ Add a node → Advanced → Send an HTTP request**.

Configure the HTTP request:

| Field            | Value                                                                |
| ---------------- | -------------------------------------------------------------------- |
| Method           | `POST`                                                               |
| URL              | `https://<your-bridge-host>/api/servicenow/agent/escalate`           |
| Headers          | `Content-Type: application/json`<br>`X-Agent-Secret: <AGENT_API_SECRET value>` |
| Body type        | `application/json`                                                   |
| Body             | (see below)                                                          |
| Save response to | `Topic.escalateResp`                                                 |

Body (use the formula bar to insert variables):

```json
{
  "session_id":      "{x:User.Id}",
  "opening_message": "{x:Topic.LastUserMessage}"
}
```

> Don't have `Topic.LastUserMessage` available? Save the user's previous
> turn into a topic variable before this node and reference that.

## 4. End the bot's side of the conversation

After the HTTP action, add:

1. **Send a message**: *"You're connected — an agent will be with you shortly."*
2. **End conversation** (or jump to a "live agent in progress" subtopic
   that just stops further bot replies).

The browser is responsible for visually switching the chat into "live
agent" mode. It does that the moment it receives the bridge's WS push
`{type:"status", state:"queued"}`, which lands within milliseconds of your
HTTP action returning. See [`05-browser-webchat.md`](05-browser-webchat.md).

## 5. Test from inside Copilot Studio

1. Save and publish the topic.
2. Open your test webchat.
3. Type `talk to an agent`.
4. The bridge logs should show:

   ```
   [agent] escalate hit headers=... body={"session_id":"...","opening_message":"..."}
   ```

5. Within a few seconds your test agent should see a chat invitation in
   Agent Workspace (assuming they're **Available**).

## Alternative: client-driven handoff (no public bridge URL needed)

If you don't want to expose the bridge to the internet (e.g. it's on a
corporate network), you can use Copilot Studio's built-in
**Transfer conversation** action instead. Copilot Studio emits a Direct
Line activity of `type='event', name='handoff.initiate'` with a
`value.va_*` payload; have the browser subscribe and call the bridge
itself:

```javascript
directLine.activity$.subscribe((act) => {
  if (act.type === 'event' && act.name === 'handoff.initiate') {
    const ctx = act.value || {};
    const opening =
      ctx.va_AgentMessage || ctx.va_LastPhrases || ctx.va_LastTopic || '';
    fetch('/api/servicenow/escalate', {  // browser-facing, not /agent/escalate
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: bridgeSessionId,
        opening_message: opening,
      }),
    });
  }
});
```

Trade-offs:

| Option                     | Pros                                                          | Cons                                                              |
| -------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------- |
| HTTP action (recommended)  | Bridge owns the lifecycle. Easy to add server-side logic.     | Bridge must be reachable from Copilot Studio cloud (HTTPS).       |
| `handoff.initiate` event   | Bridge can be private (only browser calls it).                | Browser code is the trust boundary; harder to add server logic.   |

## Reference

- [TransferConversationV2 / handoff.initiate](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-hand-off)
- [Send an HTTP request action](https://learn.microsoft.com/en-us/microsoft-copilot-studio/authoring-http-requests)
