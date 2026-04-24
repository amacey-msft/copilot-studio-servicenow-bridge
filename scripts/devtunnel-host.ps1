<#
.SYNOPSIS
    Host the Dev Tunnel that forwards public HTTPS to the bridge container.

.DESCRIPTION
    Runs `devtunnel host` in the foreground, forwarding to host port 5001
    (the published port from bridge/docker-compose.yml). Press Ctrl+C to
    stop hosting; the tunnel itself is preserved (use devtunnel-delete.ps1
    to remove it).

.PARAMETER Port
    Host port to forward. Defaults to 5001.
#>
[CmdletBinding()]
param(
    [int]$Port = 5001,
    [string]$Label = 'copilot-sn-bridge'
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command devtunnel -ErrorAction SilentlyContinue)) {
    throw "devtunnel CLI not found. Install with: winget install Microsoft.devtunnel"
}

# Look up the persistent tunnel ID by label so the public URL stays stable
# across restarts. If none exists, run scripts/devtunnel-create.ps1 first.
$line = devtunnel list 2>&1 | Select-String -Pattern $Label | Select-Object -First 1
if (-not $line) {
    throw "No tunnel with label '$Label' found. Run .\scripts\devtunnel-create.ps1 first."
}
$tunnelId = ($line.ToString() -split '\s+')[0]

Write-Host "Hosting tunnel $tunnelId -> http://localhost:$Port" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop hosting (the tunnel itself remains)." -ForegroundColor Yellow
Write-Host ""
devtunnel host $tunnelId
