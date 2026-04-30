[CmdletBinding()]
param([string]$EnvFile)
$ErrorActionPreference = 'Stop'
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$repoRoot  = Split-Path $scriptDir -Parent
if (-not $EnvFile) { $EnvFile = Join-Path $repoRoot 'bridge\.env' }
$envMap = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim(); if (-not $line -or $line.StartsWith('#')) { return }
    $idx = $line.IndexOf('='); if ($idx -lt 1) { return }
    $envMap[$line.Substring(0,$idx).Trim()] = $line.Substring($idx+1).Trim().Trim('"').Trim("'")
}
$snInstance = $envMap['SN_INSTANCE'].TrimEnd('/')
$pair = "$($envMap['SN_ADMIN_USER']):$($envMap['SN_ADMIN_PASSWORD'])"
$auth = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
$h = @{ Authorization = $auth; Accept = 'application/json' }

Write-Host "`n=== sys_script BRs on sys_cs_message ===" -ForegroundColor Cyan
$brs = Invoke-RestMethod -Headers $h -Uri "$snInstance/api/now/table/sys_script?sysparm_query=collection=sys_cs_message^active=true&sysparm_fields=sys_id,name,when,order,filter_condition,active"
$brs.result | Sort-Object order | Format-Table name, when, order, sys_id -AutoSize

Write-Host "`n=== sysevent_script_action for intranet_bridge.skill.fanout ===" -ForegroundColor Cyan
$sa = Invoke-RestMethod -Headers $h -Uri "$snInstance/api/now/table/sysevent_script_action?sysparm_query=event_name=intranet_bridge.skill.fanout&sysparm_fields=sys_id,name,active,event_name"
$sa.result | Format-Table name, active, event_name, sys_id -AutoSize

Write-Host "`n=== last 10 sysevent rows for intranet_bridge.skill.fanout ===" -ForegroundColor Cyan
$ev = Invoke-RestMethod -Headers $h -Uri "$snInstance/api/now/table/sysevent?sysparm_query=name=intranet_bridge.skill.fanout^ORDERBYDESCsys_created_on&sysparm_limit=10&sysparm_fields=sys_id,name,state,parm1,parm2,sys_created_on,processed,error_message"
$ev.result | Format-Table sys_created_on, state, processed, parm1, parm2 -AutoSize
if (-not $ev.result -or $ev.result.Count -eq 0) {
    Write-Host "  NO events queued. BR shim never fired (or queued events purged)." -ForegroundColor Yellow
}

Write-Host "`n=== last 10 syslog rows mentioning intranet_bridge.skill ===" -ForegroundColor Cyan
$lg = Invoke-RestMethod -Headers $h -Uri "$snInstance/api/now/table/syslog?sysparm_query=messageLIKEintranet_bridge.skill^ORDERBYDESCsys_created_on&sysparm_limit=10&sysparm_fields=sys_created_on,level,source,message"
$lg.result | Format-Table sys_created_on, level, source, message -AutoSize -Wrap

Write-Host "`n=== last 5 outbound CSR sys_cs_message rows ===" -ForegroundColor Cyan
$msgs = Invoke-RestMethod -Headers $h -Uri "$snInstance/api/now/table/sys_cs_message?sysparm_query=direction=outbound^is_agent=true^ORDERBYDESCsys_created_on&sysparm_limit=5&sysparm_fields=sys_id,sys_created_on,conversation,sender,q_data_message_type,payload"
$msgs.result | Format-Table sys_created_on, conversation, q_data_message_type, payload -AutoSize -Wrap
