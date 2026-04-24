# Reference intranet page

This folder ships a self-contained reference intranet page that exercises
the full bridge: AI chat via Direct Line, escalation to a ServiceNow
live agent, and bidirectional message relay — wrapped in a "lived-in"
demo intranet for screenshots and walkthroughs.

## What's here

- `intranet.html` — single-file demo page. Zero external assets except
  the Bot Framework Web Chat SDK (CDN). Avatars are inline SVG so the
  page renders identically with no network access to image hosts.

## Page features

- **Hero** with personalized greeting and a button that opens the chat.
- **KPI strip** (open tickets, PTO, pulse, office capacity).
- **Announcements** with category badges (events, IT, HR).
- **All-hands agenda** with time slots and presenter names.
- **New hires this week** grid with deterministic colored SVG avatars
  (no external image fetches, no PII).
- **Quick links** with iconography.
- **Recent activity** feed.
- Sticky top bar with search and a user avatar.
- Responsive layout (collapses to single-column on narrow screens).

## Chat widget features

The chat widget is a small, in-house implementation (deliberately not
the Web Chat React UI) so the bridge can swap message routing between
the bot and the ServiceNow rep mid-conversation.

- **Bot ↔ live agent state machine**: `bot → queued → live → closed`
  with status pill, header title, and presence dot updating
  automatically.
- **Restart chat** (`↻`) — confirms, tears down the current Direct Line
  + bridge session, opens a fresh one.
- **End chat** (`⏻`) — confirms, notifies the agent, closes the
  websocket / poll loop, and switches the panel to `closed` state.
- **Minimize** (`—`) — collapses to the launcher *while keeping the
  session alive*. Incoming agent messages bump an unread counter on the
  launcher badge.
- **Close** (`×`) — full close: tears the session down so reopening
  starts fresh.
- **Quick replies** — context-sensitive chips beneath the chat log
  (suggested intents in `bot` mode, "Start a new chat" in `closed`
  mode).
- **Typing indicator** — animated three-dot bubble while waiting for
  the bot or agent reply.
- **Per-message timestamps** and **avatars** (SVG, deterministic colour
  by sender name).
- **Auto-scroll** when at bottom; floating "↓ scroll to newest" button
  if you've scrolled up.
- **Auto-grow textarea** with **Enter to send**, **Shift+Enter for
  newline**.
- **Esc to minimize**.
- **Accessibility**: `aria-live` log, `role="dialog"` panel, labelled
  controls, sr-only labels on inputs, focus moves to the textarea on
  open.
- **Confirmation modal** (in-panel) for destructive actions instead of
  browser `confirm()`.

## How it's used

The bridge's `bridge/app.py` serves this file at `/` so a fresh
`docker compose up` immediately produces a working demo.

To embed the chat in your own intranet:

1. Copy the `<style>...</style>` block, the `#chatLauncher` button, the
   `#chatPanel` div (and its `confirm-overlay` child), and the
   `<script>...</script>` block at the bottom (you can drop the
   new-hires renderer if you don't need it).
2. Make sure your page is served from the same origin as the bridge
   (so the relative paths `/api/servicenow/*`, `/ws/intranet/*`, and
   `/directline/token` work). If not, you'll need to make those URLs
   absolute and configure CORS on the bridge.
3. Point `/directline/token` at your real Copilot Studio token endpoint
   (the reference `app.py` ships a 501 stub).

For details on the state machine and the bridge events the page
listens for, see [`../docs/05-browser-webchat.md`](../docs/05-browser-webchat.md).
