# Build the Teams app package (.zip) for sideloading.
#
# Usage:
#   .\build.ps1 -BotId <MS_APP_ID> -BridgeHost <bridge.example.com>
#
# Produces:
#   teams_bot\manifest\dist\teamsapp.zip

param(
    [Parameter(Mandatory = $true)] [string] $BotId,
    [Parameter(Mandatory = $true)] [string] $BridgeHost,
    [string] $AppId = $null,
    [string] $OutDir = $null
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src  = Join-Path $here 'manifest.json'
if (-not (Test-Path $src)) { throw "manifest.json not found at $src" }

if (-not $OutDir) { $OutDir = Join-Path $here 'dist' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# Stage the manifest with substitutions in a temp folder so we don't touch
# the source-controlled file.
$staging = Join-Path $OutDir 'staging'
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

if (-not $AppId) { $AppId = [guid]::NewGuid().ToString() }

$json = Get-Content -Raw -Path $src
$json = $json -replace 'REPLACE-WITH-A-FRESH-GUID', $AppId
$json = $json -replace 'REPLACE-WITH-MS_APP_ID', $BotId
$json = $json -replace 'REPLACE-WITH-BRIDGE-PUBLIC-HOST\.example\.com', $BridgeHost

Set-Content -Path (Join-Path $staging 'manifest.json') -Value $json -Encoding UTF8

# Copy icons (placeholders or real artwork).
foreach ($name in 'icon-color.png','icon-outline.png') {
    $iconSrc = Join-Path $here $name
    if (-not (Test-Path $iconSrc)) {
        Write-Warning "Missing $name; the resulting package won't sideload until you add it."
        continue
    }
    Copy-Item $iconSrc (Join-Path $staging $name)
}

$zipPath = Join-Path $OutDir 'teamsapp.zip'
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zipPath -Force

Write-Host "Wrote $zipPath" -ForegroundColor Green
Write-Host "Sideload via Teams: Apps -> Manage your apps -> Upload an app -> Upload a customized app."
