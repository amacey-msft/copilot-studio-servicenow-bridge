# Build the Teams app package (.zip) for sideloading the M365 Agents SDK
# port (teams_agent/). Side-by-side with teams_bot/manifest/build.ps1 -
# uses a different manifest id, name suffix, and AZURE_BOT_APP_ID.
#
# Usage:
#   .\build.ps1 -BotId <AZURE_BOT_APP_ID> -AgentHost <agent.example.com>
#
# Produces:
#   teams_agent\manifest\dist\teamsapp.zip

param(
    [Parameter(Mandatory = $true)] [string] $BotId,
    [Parameter(Mandatory = $true)] [string] $AgentHost,
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

$staging = Join-Path $OutDir 'staging'
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

if (-not $AppId) { $AppId = [guid]::NewGuid().ToString() }

$json = Get-Content -Raw -Path $src
if ($json -match 'REPLACE-WITH-A-FRESH-GUID') {
    $json = $json -replace 'REPLACE-WITH-A-FRESH-GUID', $AppId
}
$json = $json -replace 'REPLACE-WITH-AZURE_BOT_APP_ID', $BotId
$json = $json -replace 'REPLACE-WITH-AGENT-PUBLIC-HOST\.example\.com', $AgentHost

# PowerShell 5.1's Set-Content -Encoding UTF8 prepends a BOM, which Teams
# rejects with a generic "Manifest parsing error message unavailable".
# Write BOM-less UTF-8 via raw .NET.
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $staging 'manifest.json'), $json, $utf8NoBom)

foreach ($name in 'icon-color.png','icon-outline.png') {
    $iconSrc = Join-Path $here $name
    if (-not (Test-Path $iconSrc)) {
        Write-Warning "Missing $name in $here. Copying from teams_bot/manifest/ as fallback."
        $fallback = Join-Path (Split-Path -Parent (Split-Path -Parent $here)) "teams_bot\manifest\$name"
        if (Test-Path $fallback) {
            Copy-Item $fallback (Join-Path $staging $name)
        } else {
            Write-Warning "No fallback $name either. Package won't sideload until you add icons."
        }
        continue
    }
    Copy-Item $iconSrc (Join-Path $staging $name)
}

$zipPath = Join-Path $OutDir 'teamsapp.zip'
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zipPath -Force

Write-Host "Wrote $zipPath" -ForegroundColor Green
Write-Host "Sideload via Teams: Apps -> Manage your apps -> Upload an app -> Upload a customized app."
