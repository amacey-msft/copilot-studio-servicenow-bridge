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
`https://abc123-5001.use.devtunnels.ms`. Paste that as the
`BRIDGE_BASE_URL` value in your ServiceNow outbound Business Rule
header (`X-Bridge-Secret` plus the `BRIDGE_BASE_URL` env var inside
ServiceNow).

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
