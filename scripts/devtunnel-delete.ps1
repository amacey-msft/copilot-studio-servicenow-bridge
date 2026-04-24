<#
.SYNOPSIS
    Delete the persistent Dev Tunnel created for the bridge.

.PARAMETER Label
    Tunnel label. Defaults to "copilot-sn-bridge".
#>
[CmdletBinding()]
param(
    [string]$Label = 'copilot-sn-bridge'
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command devtunnel -ErrorAction SilentlyContinue)) {
    throw "devtunnel CLI not found."
}

# Find tunnel ID by label.
$line = devtunnel list 2>&1 | Select-String -Pattern $Label | Select-Object -First 1
if (-not $line) {
    Write-Host "No tunnel with label '$Label' found." -ForegroundColor Yellow
    exit 0
}

# Tunnel ID is the first whitespace-separated token on the matched line.
$tunnelId = ($line.ToString() -split '\s+')[0]
Write-Host "Deleting tunnel $tunnelId (label '$Label')..." -ForegroundColor Cyan
devtunnel delete $tunnelId --force
Write-Host "Done." -ForegroundColor Green
