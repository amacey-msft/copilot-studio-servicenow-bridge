<#
.SYNOPSIS
  Install (or update) the v3 skill fan-out Business Rule + sys_properties
  in ServiceNow via Table API.

.DESCRIPTION
  Reads bridge/.env for SN_INSTANCE / SN_ADMIN_USER / SN_ADMIN_PASSWORD /
  SN_WEBHOOK_SECRET, then idempotently:

    1. Upserts sys_properties:
         - intranet_bridge.skill_webhook_url    = <SkillWebhookUrl>
         - intranet_bridge.skill_webhook_secret = <SN_WEBHOOK_SECRET>
    2. Upserts sys_script Business Rule
         "Intranet Bridge Outbound (Skill Fan-Out)" on sys_cs_message.

  Mirrors the pattern in scripts/sync-bridge-url.ps1.

.PARAMETER EnvFile
  Path to bridge/.env. Default: ../bridge/.env relative to this script.

.PARAMETER SkillWebhookUrl
  Override the skill webhook URL. Default:
  https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io/api/sn-webhook
#>
[CmdletBinding()]
param(
    [string]$EnvFile,
    [string]$SkillWebhookUrl = 'https://ca-cps-sn-skill.happyhill-34f7f143.eastus2.azurecontainerapps.io/api/sn-webhook'
)
$ErrorActionPreference = 'Stop'

# ---------- locate paths ----------
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$repoRoot  = Split-Path $scriptDir -Parent
if (-not $EnvFile) { $EnvFile = Join-Path $repoRoot 'bridge\.env' }
if (-not (Test-Path $EnvFile)) { throw ".env not found at $EnvFile" }

# ---------- load .env ----------
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

$snInstance = (Get-Var 'SN_INSTANCE' -Required).TrimEnd('/')
$snAdmin    = Get-Var 'SN_ADMIN_USER'    -Required
$snPwd      = Get-Var 'SN_ADMIN_PASSWORD' -Required
$snSecret   = Get-Var 'SN_WEBHOOK_SECRET' -Required

Write-Host "SN instance        : $snInstance" -ForegroundColor Cyan
Write-Host "Skill webhook URL  : $SkillWebhookUrl" -ForegroundColor Cyan

$cred = "$snAdmin`:$snPwd"
$b64  = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cred))
$h    = @{
    Authorization  = "Basic $b64"
    Accept         = 'application/json'
    'Content-Type' = 'application/json'
}

# ---------- 1. sys_properties (upsert) ----------
function Upsert-SysProperty([string]$name, [string]$value, [string]$type = 'string') {
    Write-Host "`n[sys_property] $name" -ForegroundColor Cyan
    $q = "$snInstance/api/now/table/sys_properties?sysparm_query=name=$name&sysparm_limit=1&sysparm_fields=sys_id,value"
    $existing = Invoke-RestMethod -Headers $h -Uri $q
    if (-not $existing.result -or $existing.result.Count -eq 0) {
        Write-Host "  create"
        $body = @{ name = $name; value = $value; type = $type } | ConvertTo-Json -Compress
        Invoke-RestMethod -Method Post -Headers $h -Uri "$snInstance/api/now/table/sys_properties" -Body $body | Out-Null
    } else {
        $sysId = $existing.result[0].sys_id
        Write-Host "  patch sys_id=$sysId"
        $body  = @{ value = $value } | ConvertTo-Json -Compress
        Invoke-RestMethod -Method Patch -Headers $h -Uri "$snInstance/api/now/table/sys_properties/$sysId" -Body $body | Out-Null
    }
    Write-Host "  ok" -ForegroundColor Green
}

Upsert-SysProperty -name 'intranet_bridge.skill_webhook_url'    -value $SkillWebhookUrl
Upsert-SysProperty -name 'intranet_bridge.skill_webhook_secret' -value $snSecret

# ---------- 2. sys_event_register (idempotent) ----------
# Bot Interconnect blocks outbound HTTP from any BR running in the BI
# transaction (sync OR async_always). The only safe pattern is to fire
# an event from the BR; a Script Action processes it in a separate
# worker outside the BI context.
$eventName = 'intranet_bridge.skill.fanout'
Write-Host "`n[sysevent_register] $eventName" -ForegroundColor Cyan
try {
    $qEv = "$snInstance/api/now/table/sysevent_register?sysparm_query=event_name=$eventName&sysparm_limit=1&sysparm_fields=sys_id"
    $existingEv = Invoke-RestMethod -Headers $h -Uri $qEv
    if (-not $existingEv.result -or $existingEv.result.Count -eq 0) {
        Write-Host "  create"
        $evBody = @{
            event_name  = $eventName
            table       = 'sys_cs_message'
            description = 'v3 skill fan-out: BR queues this event with CSR message sys_id; Script Action makes the outbound HTTP call.'
        } | ConvertTo-Json -Compress
        Invoke-RestMethod -Method Post -Headers $h -Uri "$snInstance/api/now/table/sysevent_register" -Body $evBody | Out-Null
    } else {
        Write-Host "  exists sys_id=$($existingEv.result[0].sys_id)"
    }
    Write-Host "  ok" -ForegroundColor Green
} catch {
    Write-Host "  warn: could not register event ($($_.Exception.Message)); Script Action will still fire because event_name is a free string. Continuing." -ForegroundColor Yellow
}

# ---------- 3. sys_script_action (Script Action: does the HTTP) ----------
$saName = 'Intranet Bridge Skill Fan-Out (HTTP)'
$saScript = @'
(function() {
  try {
    var msgId = String(event.parm1 || "");
    var convId = String(event.parm2 || "");
    if (!msgId || !convId) return;

    var msg = new GlideRecord("sys_cs_message");
    if (!msg.get(msgId)) return;

    var ix = new GlideRecord("interaction");
    ix.addQuery("channel_metadata_document", convId);
    ix.orderByDesc("sys_created_on");
    ix.setLimit(1);
    ix.query();
    if (!ix.next()) return;
    var bridgeSid = ix.getValue("u_bridge_session_id");

    var url    = gs.getProperty("intranet_bridge.skill_webhook_url");
    var secret = gs.getProperty("intranet_bridge.skill_webhook_secret");
    if (!url) return;

    var repName = "";
    var senderId = msg.getValue("sender");
    if (senderId) {
      var u = new GlideRecord("sys_user");
      if (u.get(senderId)) repName = String(u.getValue("name") || u.getValue("user_name") || "");
    }

    var payload = {
      bridge_session_id:   bridgeSid || "",
      conversation_sys_id: convId,
      interaction_sys_id:  ix.getUniqueValue(),
      interaction_number:  ix.getValue("number"),
      message_sys_id:      msg.getUniqueValue(),
      sender_sys_id:       senderId,
      rep_name:            repName,
      q_data_message_type: String(msg.getValue("q_data_message_type") || ""),
      text:                String(msg.getValue("payload") || ""),
      event:               "reply",
      send_time:           msg.getValue("send_time"),
      sys_created_on:      msg.getValue("sys_created_on")
    };

    var rm = new sn_ws.RESTMessageV2();
    rm.setEndpoint(url);
    rm.setHttpMethod("POST");
    rm.setRequestHeader("Content-Type", "application/json");
    rm.setRequestHeader("Accept", "application/json");
    if (secret) rm.setRequestHeader("X-Bridge-Secret", secret);
    rm.setRequestBody(JSON.stringify(payload));
    rm.setHttpTimeout(8000);
    var resp = rm.execute();
    var sc = resp.getStatusCode();
    if (sc < 200 || sc >= 300) {
      gs.warn("[intranet_bridge.skill] webhook " + url + " returned HTTP " + sc + " body=" + String(resp.getBody() || "").substring(0, 500));
    }
  } catch (e) {
    gs.error("[intranet_bridge.skill] script action threw: " + e + (e.stack ? "\n" + e.stack : ""));
  }
})();
'@

Write-Host "`n[sys_script_action] $saName for event $eventName" -ForegroundColor Cyan
$qSaName = [Uri]::EscapeDataString($saName)
$qSa = "$snInstance/api/now/table/sysevent_script_action?sysparm_query=name=$qSaName^event_name=$eventName&sysparm_limit=1&sysparm_fields=sys_id"
$existingSa = Invoke-RestMethod -Headers $h -Uri $qSa
$saFields = [ordered]@{
    name        = $saName
    event_name  = $eventName
    active      = 'true'
    description = 'v3 skill fan-out: outbound HTTP runs here, OUTSIDE the Bot Interconnect transaction, to bypass the BI guard.'
    script      = $saScript
}
$saBody = ($saFields | ConvertTo-Json -Depth 5 -Compress)
if (-not $existingSa.result -or $existingSa.result.Count -eq 0) {
    Write-Host "  create"
    $saResp = Invoke-RestMethod -Method Post -Headers $h -Uri "$snInstance/api/now/table/sysevent_script_action" -Body $saBody
    Write-Host ("  sys_id=" + $saResp.result.sys_id)
} else {
    $saId = $existingSa.result[0].sys_id
    Write-Host "  patch sys_id=$saId"
    Invoke-RestMethod -Method Patch -Headers $h -Uri "$snInstance/api/now/table/sysevent_script_action/$saId" -Body $saBody | Out-Null
}
Write-Host "  ok" -ForegroundColor Green

# ---------- 4. sys_script Business Rule (upsert) ----------
# BR is a thin shim: only fires the event. NO outbound HTTP here.
# 'when=async_always' is fine because we never call sn_ws inside the BI txn.
$brName       = 'Intranet Bridge Outbound (Skill Fan-Out)'
$brCollection = 'sys_cs_message'
$brFilter     = 'direction=outbound^is_agent=true^q_data_message_typeINsystemTextMessage,consumerTextMessage'
$brScript     = @"
(function executeRule(current, previous /*null when async*/) {
  try {
    if (current.getValue('direction') !== 'outbound') return;
    if (current.getValue('is_agent') != '1' && String(current.getValue('is_agent')) !== 'true') return;
    var qd = String(current.getValue('q_data_message_type') || '');
    if (qd !== 'systemTextMessage' && qd !== 'consumerTextMessage') return;
    var convId = current.getValue('conversation');
    if (!convId) return;
    // Queue an event; the Script Action '$saName' picks it up in a
    // separate worker thread and makes the outbound HTTP call OUTSIDE
    // the Bot Interconnect transaction (otherwise BI rejects it).
    gs.eventQueue('$eventName', current, current.getUniqueValue(), convId);
  } catch (e) {
    gs.error('[intranet_bridge.skill] BR shim threw: ' + e);
  }
})(current, previous);
"@

$brFields = [ordered]@{
    name             = $brName
    collection       = $brCollection
    when             = 'async_always'
    order            = 210
    active           = 'true'
    advanced         = 'true'
    action_insert    = 'true'
    action_update    = 'false'
    action_delete    = 'false'
    action_query     = 'false'
    filter_condition = $brFilter
    script           = $brScript
    description      = "v3 skill fan-out shim: queues '$eventName' event; '$saName' Script Action does the HTTP. Independent of the legacy bridge BR."
}

Write-Host "`n[sys_script] $brName on $brCollection" -ForegroundColor Cyan
$qName = [Uri]::EscapeDataString($brName)
$q = "$snInstance/api/now/table/sys_script?sysparm_query=name=$qName^collection=$brCollection&sysparm_limit=1&sysparm_fields=sys_id"
$existingBr = Invoke-RestMethod -Headers $h -Uri $q
$bodyJson = ($brFields | ConvertTo-Json -Depth 5 -Compress)
if (-not $existingBr.result -or $existingBr.result.Count -eq 0) {
    Write-Host "  create"
    $resp = Invoke-RestMethod -Method Post -Headers $h -Uri "$snInstance/api/now/table/sys_script" -Body $bodyJson
    Write-Host ("  sys_id=" + $resp.result.sys_id)
} else {
    $sysId = $existingBr.result[0].sys_id
    Write-Host "  patch sys_id=$sysId"
    Invoke-RestMethod -Method Patch -Headers $h -Uri "$snInstance/api/now/table/sys_script/$sysId" -Body $bodyJson | Out-Null
}
Write-Host "  ok" -ForegroundColor Green

Write-Host "`nDone. The BR will fire on the next outbound CSR message in any sys_cs_conversation." -ForegroundColor Green
