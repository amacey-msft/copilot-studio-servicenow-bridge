# Local dev tunnel scripts

These scripts expose the bridge running in Docker to the public internet
via Microsoft Dev Tunnels so ServiceNow's outbound webhook (Business
Rule on `sys_cs_message`) can reach you while you develop locally.

## Prerequisites

1. **Docker Desktop** running.
2. **Dev Tunnels CLI** installed:
   ```powershell
   winget install Microsoft.devtunnel
   devtunnel user login
   ```
3. The bridge container running on host port `5001` (the default in
   `bridge/docker-compose.yml`).

## Usage

From the repo root:

```powershell
# 1. Start the bridge container
docker compose -f bridge/docker-compose.yml up -d --build

# 2. Create a persistent tunnel (one-time)
.\scripts\devtunnel-create.ps1

# 3. Start hosting the tunnel (runs in foreground; Ctrl+C to stop)
.\scripts\devtunnel-host.ps1
```

The host script prints a public HTTPS URL like
`https://abc123-5001.use.devtunnels.ms`. Put that URL in
`bridge/.env` as `BRIDGE_PUBLIC_URL` and run

```powershell
.\scripts\sync-bridge-url.ps1
```

to push it to both ServiceNow (`intranet_bridge.outbound_webhook_url`
sys_property) and Copilot Studio (the two HTTP-tool botcomponents).
After it finishes, click **Publish** once in the Copilot Studio maker
UI so the runtime picks up the new URLs. See
[`scripts/sync-bridge-url.ps1`](./sync-bridge-url.ps1) for details and
required env vars (`POWERPLATFORM_ORG_URL`, `POWERPLATFORM_BOT_ID`,
`POWERPLATFORM_BOT_SCHEMA`, `SN_ADMIN_USER`, `SN_ADMIN_PASSWORD`).

To stop and delete the tunnel when done:

```powershell
.\scripts\devtunnel-delete.ps1
```

## What ports

- **Container** listens on `5000` (gunicorn).
- **Host** publishes it on `5001` (to avoid colliding with other local
  Flask apps).
- **Dev tunnel** forwards public 443 -> host `5001`.

If you change the host port in `bridge/docker-compose.yml`, update the
`$Port` variable at the top of `devtunnel-create.ps1` and
`devtunnel-host.ps1`.

## Tunnel ID vs URL slug

The devtunnel CLI uses two different identifiers — don't confuse them:

| Identifier | Example | Where you use it |
| ---------- | ------- | ---------------- |
| **Tunnel ID** | `jolly-river-lw1s3ms` | `devtunnel host <id>`, `devtunnel delete <id>`. Shown by `devtunnel list`. |
| **URL slug** | `pbqgkr6d` | Only appears inside the public hostname `https://<slug>-<port>.<region>.devtunnels.ms`. This is what goes in the Azure Bot messaging endpoint, the bridge `BRIDGE_PUBLIC_URL`, and `TEAMS_AGENT_PUSH_URL`. |

Get the URL slug from the output of `devtunnel-create.ps1`, or by
running `devtunnel show <tunnel-id>`. Do **not** put the tunnel ID in
the bot endpoint — it isn't a routable hostname component.

## Two tunnels for the SDK port

The new `teams_agent/` service runs on port 3978 and needs its own
public hostname (separate from the bridge's 5001 tunnel) so the
Azure Bot messaging endpoint can reach it independently of bridge
traffic. Create with the same script:

```powershell
.\scripts\devtunnel-create.ps1 -Port 3978 -Label copilot-sn-agent
```

Run **both** hosts simultaneously in separate terminals:

```powershell
# terminal 1
devtunnel host <bridge-tunnel-id> -p 5001 --allow-anonymous
# terminal 2
devtunnel host <agent-tunnel-id>  -p 3978 --allow-anonymous
```

If either host stops, the corresponding component goes dark. The
`scripts/sync-bridge-url.ps1` helper updates the bridge's
`BRIDGE_PUBLIC_URL` env after a tunnel restart; do an analogous
update of the Azure Bot messaging endpoint when the agent tunnel
slug changes.
