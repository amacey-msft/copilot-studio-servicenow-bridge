<#
.SYNOPSIS
  Push BRIDGE_PUBLIC_URL from bridge/.env to ServiceNow + Copilot Studio.

.DESCRIPTION
  Reads bridge/.env (or the file passed via -EnvFile) and:

    1. PATCHes the ServiceNow sys_property `intranet_bridge.outbound_webhook_url`
       to `<BRIDGE_PUBLIC_URL>/api/servicenow/webhook` via the Table API
       (uses SN_ADMIN_USER / SN_ADMIN_PASSWORD with Basic auth).

    2. Rewrites the `url:` line inside the two agent HTTP tools
       (`<schema>.action.CreateServiceNowIncident`,
        `<schema>.action.EscalateToLiveITAgent`)
       so they call `<BRIDGE_PUBLIC_URL>/api/servicenow/agent/...`,
       then PATCHes the botcomponent rows back via the Dataverse Web API.

  Auth for Dataverse uses `az account get-access-token --resource <org>`,
  so run `az login` first if you haven't.

  Run this any time your tunnel URL changes, or when handing the repo to a
  new user who has set their own BRIDGE_PUBLIC_URL.

.PARAMETER EnvFile
  Path to the .env file. Default: bridge/.env relative to the repo root.

.PARAMETER SkipServiceNow
  Don't touch ServiceNow.

.PARAMETER SkipCopilotStudio
  Don't touch Copilot Studio.

.EXAMPLE
  pwsh ./scripts/sync-bridge-url.ps1
#>
[CmdletBinding()]
param(
    [string]$EnvFile,
    [switch]$SkipServiceNow,
    [switch]$SkipCopilotStudio
)
$ErrorActionPreference = 'Stop'

if (-not $EnvFile) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $EnvFile = Join-Path (Split-Path $scriptDir -Parent) 'bridge\.env'
}

# ---------- load .env ----------
if (-not (Test-Path $EnvFile)) {
    throw ".env not found at $EnvFile. Copy bridge/.env.sample to bridge/.env first."
}
$envMap = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return }
    $idx = $line.IndexOf('=')
    if ($idx -lt 1) { return }
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    $envMap[$k] = $v
}
function Get-Var([string]$name, [switch]$Required) {
    $v = $envMap[$name]
    if ($Required -and [string]::IsNullOrWhiteSpace($v)) {
        throw "Missing required variable '$name' in $EnvFile"
    }
    return $v
}

$publicUrl = (Get-Var 'BRIDGE_PUBLIC_URL' -Required).TrimEnd('/')
Write-Host "BRIDGE_PUBLIC_URL = $publicUrl" -ForegroundColor Cyan

# ---------- 1. ServiceNow sys_property ----------
if (-not $SkipServiceNow) {
    $snInstance = (Get-Var 'SN_INSTANCE' -Required).TrimEnd('/')
    $snAdmin    = Get-Var 'SN_ADMIN_USER'
    $snPwd      = Get-Var 'SN_ADMIN_PASSWORD'
    if (-not $snAdmin -or -not $snPwd) {
        Write-Warning "SN_ADMIN_USER / SN_ADMIN_PASSWORD not set - skipping ServiceNow. Set them or run with -SkipServiceNow to silence this."
    } else {
        $webhookUrl = "$publicUrl/api/servicenow/webhook"
        $propName   = 'intranet_bridge.outbound_webhook_url'
        Write-Host "`n[ServiceNow] PATCH sys_property $propName -> $webhookUrl" -ForegroundColor Cyan
        $cred = "$snAdmin`:$snPwd"
        $b64  = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cred))
        $h    = @{ Authorization = "Basic $b64"; Accept = 'application/json'; 'Content-Type' = 'application/json' }
        $q    = "$snInstance/api/now/table/sys_properties?sysparm_query=name=$propName&sysparm_limit=1&sysparm_fields=sys_id,value"
        $existing = Invoke-RestMethod -Headers $h -Uri $q
        if (-not $existing.result -or $existing.result.Count -eq 0) {
            Write-Host "  sys_property does not exist - creating"
            $body = @{ name = $propName; value = $webhookUrl; type = 'string' } | ConvertTo-Json
            Invoke-RestMethod -Method Post -Headers $h -Uri "$snInstance/api/now/table/sys_properties" -Body $body | Out-Null
        } else {
            $sysId = $existing.result[0].sys_id
            $body  = @{ value = $webhookUrl } | ConvertTo-Json
            Invoke-RestMethod -Method Patch -Headers $h -Uri "$snInstance/api/now/table/sys_properties/$sysId" -Body $body | Out-Null
        }
        Write-Host "  ok" -ForegroundColor Green
    }
}

# ---------- 2. Copilot Studio botcomponents ----------
if (-not $SkipCopilotStudio) {
    $orgUrl = (Get-Var 'POWERPLATFORM_ORG_URL').TrimEnd('/')
    $botId  = Get-Var 'POWERPLATFORM_BOT_ID'
    $schema = Get-Var 'POWERPLATFORM_BOT_SCHEMA'
    if (-not $orgUrl -or -not $botId -or -not $schema) {
        Write-Warning "POWERPLATFORM_ORG_URL / POWERPLATFORM_BOT_ID / POWERPLATFORM_BOT_SCHEMA not all set - skipping Copilot Studio."
    } else {
        if (-not $orgUrl.StartsWith('http')) { $orgUrl = "https://$orgUrl" }
        Write-Host "`n[Copilot Studio] org=$orgUrl bot=$botId schema=$schema" -ForegroundColor Cyan
        $tok = az account get-access-token --resource $orgUrl --query accessToken -o tsv 2>$null
        if (-not $tok) {
            throw "az account get-access-token failed. Run 'az login' first."
        }
        $h = @{
            Authorization      = "Bearer $tok"
            Accept             = 'application/json'
            'OData-Version'    = '4.0'
            'Content-Type'     = 'application/json'
            Prefer             = 'return=representation'
        }
        $base = "$orgUrl/api/data/v9.2"

        # Map of schemaname -> backend path the tool should call
        $tools = @{
            "$schema.action.CreateServiceNowIncident" = "$publicUrl/api/servicenow/agent/create-ticket"
            "$schema.action.EscalateToLiveITAgent"    = "$publicUrl/api/servicenow/agent/escalate"
        }

        foreach ($entry in $tools.GetEnumerator()) {
            $sn  = $entry.Key
            $newUrl = $entry.Value
            Write-Host "  -> $sn => $newUrl"
            $q = "$base/botcomponents?`$filter=schemaname eq '$sn'&`$select=botcomponentid,data"
            $r = Invoke-RestMethod -Headers $h -Uri $q
            if (-not $r.value -or $r.value.Count -eq 0) {
                Write-Warning "     botcomponent not found - skipping"
                continue
            }
            $row  = $r.value[0]
            $yaml = $row.data
            # Replace the first 'url:' line under HttpRequestAction nodes.
            # Tool YAMLs only have HTTP actions calling the bridge, so a
            # blanket replace of the url field is safe.
            $patched = [Regex]::Replace(
                $yaml,
                '(?m)^(\s*url:\s*).*$',
                { param($m) $m.Groups[1].Value + $newUrl }
            )
            if ($patched -eq $yaml) {
                Write-Warning "     no 'url:' line matched - YAML unchanged"
                continue
            }
            $body = @{ data = $patched } | ConvertTo-Json -Depth 5
            Invoke-RestMethod -Method Patch -Headers $h -Uri "$base/botcomponents($($row.botcomponentid))" -Body $body | Out-Null
            Write-Host "     ok" -ForegroundColor Green
        }

        Write-Host "`nNOTE: Copilot Studio caches published agent. Open the maker UI and click Publish so the runtime picks up the new URLs:" -ForegroundColor Yellow
        Write-Host "  https://copilotstudio.microsoft.com/  ->  your agent  ->  Publish"
    }
}

Write-Host "`nDone." -ForegroundColor Green
