# 05 — Browser webchat integration

The reference implementation lives in [`web/intranet.html`](../web/intranet.html).
This page documents the contract so you can wire the bridge into your own
chat UI.

## State machine

| State    | Header                            | Input box                     | Outgoing call                       |
| -------- | --------------------------------- | ----------------------------- | ----------------------------------- |
| `bot`    | "Chat with Assistant"             | Enabled, sends to Direct Line | `directLine.postActivity(...)`      |
| `queued` | "Connecting an agent…"            | Disabled, with spinner        | (none — waiting on bridge)          |
| `live`   | "Chat with `<Rep Name>`"          | Enabled, sends to bridge      | `POST /api/servicenow/user-message` |
| `closed` | "This chat has ended."            | Disabled                      | (none — offer to start a new chat)  |

## Page-load bootstrap

```javascript
let state = { sid: null, mode: 'bot', repName: null, directLine: null };

async function boot() {
  // 1. Allocate a bridge session.
  const r = await fetch('/api/servicenow/init-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_display_name: 'Intranet User' }),
  });
  const { session_id } = await r.json();
  state.sid = session_id;

  // 2. Open the WS up-front so we don't miss the 'queued' status push.
  openBridgeSocket();

  // 3. Always poll too — useful when WS is blocked, proxied through a
  //    worker that doesn't pass server-pushed frames, or behind a CDN.
  setInterval(pollBridge, 2000);

  // 4. Start Direct Line with the bridge sid as user.id so Copilot Studio
  //    can echo it back in the Escalate topic's HTTP action.
  await startDirectLine(session_id);
}

function openBridgeSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/intranet/${state.sid}`);
  ws.onmessage = (e) => handleBridgeEvent(JSON.parse(e.data));
}

async function pollBridge() {
  const r = await fetch(`/api/servicenow/poll/${state.sid}`);
  const j = await r.json();
  for (const evt of j.events || []) handleBridgeEvent(evt);
  if (j.state && j.state !== state.mode) setMode(j.state, j.rep_name);
}
```

## Direct Line setup

```javascript
async function startDirectLine(sid) {
  const r = await fetch('/directline/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: sid }),
  });
  const { token } = await r.json();
  state.directLine = window.WebChat.createDirectLine({ token });

  state.directLine.activity$.subscribe((act) => {
    if (act.from && act.from.id === sid) return; // echo of our own message
    if (act.type !== 'message') return;
    if (state.mode !== 'bot') return; // ignore stale bot turns once handed off
    if (act.text) renderBubble({ from: 'Assistant', text: act.text });
  });
}
```

## Send box (mode-aware)

```javascript
async function onSend(text) {
  renderBubble({ from: 'You', text });
  if (state.mode === 'bot') {
    state.directLine.postActivity({
      from: { id: state.sid },
      type: 'message',
      text,
    }).subscribe();
  } else if (state.mode === 'live') {
    await fetch('/api/servicenow/user-message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.sid, text }),
    });
  }
}
```

## Handling bridge events

```javascript
function handleBridgeEvent(evt) {
  if (evt.type === 'status') {
    setMode(evt.state, evt.rep_name);
  } else if (evt.type === 'message' && evt.from === 'rep') {
    renderBubble({ from: evt.rep_name || 'Support Agent', text: evt.text });
  }
}

function setMode(mode, repName) {
  state.mode = mode;
  state.repName = repName || state.repName;
  // Update headers, input box, etc.
  document.querySelector('#header').textContent =
    mode === 'bot'    ? 'Chat with Assistant'
  : mode === 'queued' ? 'Connecting an agent…'
  : mode === 'live'   ? `Chat with ${state.repName || 'Support Agent'}`
  :                     'This chat has ended.';
  document.querySelector('#input').disabled =
    mode === 'queued' || mode === 'closed';
}
```

## Optional: client-driven handoff

If you chose the client-driven option in
[`04-copilot-studio.md`](04-copilot-studio.md) (using
`TransferConversationV2`), add this inside `directLine.activity$.subscribe`:

```javascript
if (act.type === 'event' && act.name === 'handoff.initiate') {
  if (state.mode !== 'bot') return; // already handed off
  const ctx = act.value || {};
  const opening =
    ctx.va_AgentMessage || ctx.va_LastPhrases || ctx.va_LastTopic || '';
  fetch('/api/servicenow/escalate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: state.sid, opening_message: opening }),
  });
  return;
}
```

## Things that have bitten people

- **Forgetting `from: { id: sid }`** on Direct Line `postActivity`. Copilot
  Studio assigns a random `User.Id` per turn if you don't, and your
  Escalate topic's HTTP action will pass garbage.
- **Opening the WS *after* `/escalate`**. The `queued` status push lands
  within milliseconds, often before your WS is connected. Open it during
  page bootstrap, not on demand.
- **Trusting only the WS**. Behind some proxies/load balancers (notably
  default gunicorn `gthread`), the WS can connect but never deliver
  pushed frames. Always poll as a fallback — every 2s is fine.
- **Not deduplicating polled events**. The reference bridge drains the
  pending queue on every poll, so a frame is delivered exactly once. If
  you change that behavior, deduplicate on the client.
