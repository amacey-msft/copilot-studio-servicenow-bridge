# 11 - Teams bot setup

End-to-end recipe for getting the relay bot running and sideloaded into your
own Teams tenant. Assumes you already have the web channel working
(see [`02-servicenow-setup.md`](02-servicenow-setup.md) and
[`03-bridge-backend.md`](03-bridge-backend.md)).

## Prereqs

- A Teams tenant where you can sideload custom apps. If your tenant blocks
  this, ask an admin to enable "Upload custom apps" or use the dev tenant.
- A working bridge URL (the same `BRIDGE_PUBLIC_URL` you set up for the
  web channel). The relay bot uses this URL as its messaging endpoint.

## 1. Register an Azure Bot resource

1. Azure portal -> **Create a resource** -> *Azure Bot*.
2. Bot handle: anything (e.g. `cps-sn-relay-dev`).
3. Type of App: **Multi Tenant** (simplest). Single Tenant works too if you
   only ever demo from one tenant; set `MS_APP_TYPE=SingleTenant` and
   `MS_APP_TENANT_ID=<tenant-guid>` in `bridge/.env` accordingly.
4. Create a new Microsoft App ID (the wizard will offer this). Save the
   **Application (client) ID** -> this is `MS_APP_ID`.
5. After the bot is created, open it -> **Configuration** -> **Manage
   Microsoft App ID** -> **Certificates & secrets** -> **New client
   secret**. Save the *value* (not the id) -> this is `MS_APP_PASSWORD`.
6. Bot -> **Configuration** -> **Messaging endpoint** ->
   `https://<your-bridge-host>/api/messages`. Apply.
7. Bot -> **Channels** -> add **Microsoft Teams**. Accept the terms.

## 2. Set the env vars on the bridge

Edit `bridge/.env`:

```dotenv
MS_APP_ID=<the application client id from step 1.4>
MS_APP_PASSWORD=<the secret value from step 1.5>
MS_APP_TYPE=MultiTenant
# MS_APP_TENANT_ID=  # only for SingleTenant
```

Restart the bridge:

```powershell
docker compose -f bridge/docker-compose.yml up -d --build
```

Sanity check the bot endpoint is now wired (should return 415 because the
request isn't a real Bot Framework activity, NOT 501):

```powershell
curl.exe -X POST "$env:BRIDGE_PUBLIC_URL/api/messages" -H "Content-Type: text/plain" -d "ping"
```

A `501` response means `MS_APP_ID` is unset; recheck `.env` and the
container restart.

## 3. Build the Teams app package

From the repo root:

```powershell
.\teams_bot\manifest\build.ps1 `
    -BotId       <MS_APP_ID> `
    -BridgeHost  <your-bridge-host>          # e.g. abc1234-5001.use.devtunnels.ms
```

This stages a copy of `manifest.json` with the placeholders substituted,
copies the icons, and produces `teams_bot\manifest\dist\teamsapp.zip`.

> Add real `icon-color.png` (192x192) and `icon-outline.png` (32x32) to
> `teams_bot/manifest/` before running the script if you want a non-blank
> tile in Teams. See `teams_bot/manifest/ICONS.md`.

## 4. Sideload into Teams

1. Teams -> **Apps** -> **Manage your apps** -> **Upload an app** ->
   **Upload a customized app**.
2. Pick `teams_bot\manifest\dist\teamsapp.zip`.
3. Open the new "IT Helper" app -> **Add**.

## 5. Smoke test (BOT mode)

1. In the new 1:1 chat with the IT Helper bot, type `hi`.
2. The bot should reply with the welcome line, then route your next message
   to Copilot Studio. Replies from the CS agent appear inline in Teams.

If the bot doesn't respond:

- Bridge logs (`docker compose logs -f bridge`) will show the
  `[teams_bot] /api/messages endpoint registered` line at startup, then
  per-turn `process_activity` calls. If you see `process_activity_sync` raising
  401, the JWT validation failed -> recheck `MS_APP_ID` / `MS_APP_PASSWORD`.
- Azure portal -> Bot -> **Test in Web Chat** can rule out Teams-specific
  issues (it talks to the same `/api/messages`).

## 6. Smoke test (escalation)

1. Type **talk to a human** (or whatever phrase your CS Escalate topic
   recognises).
2. CS fires the Escalate topic -> the existing
   `/api/servicenow/agent/escalate` HTTP action OR the `handoff.initiate`
   event opens the SN interaction. The bridge pushes a `status` event to
   Teams via `continue_conversation`; the user sees the "Connecting an
   agent..." Adaptive Card with the IMS number.
3. Have your test agent claim the chat in ServiceNow Service Operations
   Workspace. Reply with some text.
4. The reply lands in the SAME Teams 1:1 chat, prefixed with the rep name.

## 7. Test the reverse (close)

In SN, end the chat. The user sees the "This chat has ended." Adaptive Card.
Typing `new` resets the session and routes the next turn back to BOT mode.

## Troubleshooting quick refs

- **`501 teams_bot_not_configured`** - `MS_APP_ID` is empty. Set it in
  `bridge/.env` and rebuild.
- **`401` from `/api/messages`** - JWT validation failure. Recheck
  `MS_APP_ID` / `MS_APP_PASSWORD` and that the Azure Bot's *Microsoft App
  type* matches `MS_APP_TYPE`.
- **Teams app sideload fails with "manifest invalid"** - usually `botId`
  is still the placeholder. Re-run `build.ps1` with the right `-BotId`.
- **Bot replies in BOT mode but Direct Line errors** - `DIRECTLINE_TOKEN_ENDPOINT`
  or `DIRECTLINE_SECRET` not set on the bridge. Same prereq as the web flow.
- **Teams shows "the bot couldn't be reached"** - messaging endpoint URL
  is wrong on the Azure Bot resource, OR your tunnel is down.
- **Status card never arrives** - the bridge's webhook isn't getting hit
  by SN. Run `tools/probe_e2e.ps1` (the web-flow probe) - if THAT works,
  the SN side is fine, and the issue is in `_push_to_user` / the
  `teams_conversation_reference` not being captured. Check that the user
  has actually messaged the bot at least once before escalating (the
  reference is captured per-turn).

For the broader symptom -> cause table see [`07-troubleshooting.md`](07-troubleshooting.md).
