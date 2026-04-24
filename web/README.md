# Reference intranet page

This folder ships a self-contained reference intranet page that exercises
the full bridge: AI chat via Direct Line, escalation to a ServiceNow live
agent, and bidirectional message relay.

## What's here

- `intranet.html` — single-file demo page with branding placeholder
  ("Contoso Connect"), a chat launcher in the bottom-right corner, and
  the in-house chat UI that drives Direct Line and the bridge.

## How it's used

The bridge's `bridge/app.py` serves this file at `/` so that a fresh
`docker compose up` immediately produces a working demo.

To embed the chat in your own intranet:

1. Copy the `<style>...</style>` block, the `#chatLauncher` button, the
   `#chatPanel` div, and the `<script>...</script>` block at the bottom.
2. Make sure your page is served from the same origin as the bridge
   (so the relative paths `/api/servicenow/*`, `/ws/intranet/*`, and
   `/directline/token` work). If not, you'll need to make those URLs
   absolute and configure CORS on the bridge.
3. Point `/directline/token` at your real token endpoint (the reference
   `app.py` ships a 501 stub).

For details on the state machine and the bridge events the page
listens for, see [`../docs/05-browser-webchat.md`](../docs/05-browser-webchat.md).
