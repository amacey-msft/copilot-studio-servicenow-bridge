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
    [int]$Port = 5001
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command devtunnel -ErrorAction SilentlyContinue)) {
    throw "devtunnel CLI not found. Install with: winget install Microsoft.devtunnel"
}

Write-Host "Hosting dev tunnel -> http://localhost:$Port" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop hosting (the tunnel itself remains)." -ForegroundColor Yellow
Write-Host ""
devtunnel host --port-numbers $Port --allow-anonymous
