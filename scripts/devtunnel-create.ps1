<#
.SYNOPSIS
    Create (or reuse) a named, persistent Dev Tunnel for the local bridge.

.DESCRIPTION
    Creates a Dev Tunnel labelled "copilot-sn-bridge" that forwards public
    HTTPS to host port 5001 (the published port for the bridge container).
    If the tunnel already exists, the script just prints its details.

.PARAMETER Port
    Host port to forward. Defaults to 5001 (matches bridge/docker-compose.yml).

.PARAMETER Label
    Tunnel label. Defaults to "copilot-sn-bridge".
#>
[CmdletBinding()]
param(
    [int]$Port  = 5001,
    [string]$Label = 'copilot-sn-bridge'
)

$ErrorActionPreference = 'Stop'

function Require-DevTunnel {
    if (-not (Get-Command devtunnel -ErrorAction SilentlyContinue)) {
        throw "devtunnel CLI not found. Install with: winget install Microsoft.devtunnel"
    }
}

Require-DevTunnel

# devtunnel list emits a banner + a header row + tunnel rows. Match by label.
$existing = devtunnel list 2>&1 | Select-String -Pattern $Label
if ($existing) {
    Write-Host "Tunnel with label '$Label' already exists:" -ForegroundColor Cyan
    devtunnel list | Select-String -Pattern $Label
    Write-Host "`nTo (re)host it, run: .\scripts\devtunnel-host.ps1" -ForegroundColor Yellow
    exit 0
}

Write-Host "Creating new dev tunnel labelled '$Label' on port $Port..." -ForegroundColor Cyan
devtunnel create --allow-anonymous --labels $Label | Out-Host
devtunnel port create -p $Port --protocol http | Out-Host

# Print the resulting public URL hint for convenience.
$line = devtunnel list 2>&1 | Select-String -Pattern $Label | Select-Object -First 1
if ($line) {
    $tunnelId = ($line.ToString() -split '\s+')[0]
    # Tunnel IDs look like 'jolly-river-lw1s3ms.use'. The public URL slug is
    # the trailing token before '.use', so derive a hint URL.
    Write-Host "`nTunnel ID: $tunnelId" -ForegroundColor Green
    Write-Host "Public URL will appear when you host: https://<slug>-$Port.use.devtunnels.ms" -ForegroundColor Green
}
Write-Host "`nTo start hosting, run: .\scripts\devtunnel-host.ps1" -ForegroundColor Green
