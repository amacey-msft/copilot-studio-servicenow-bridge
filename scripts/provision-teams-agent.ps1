# Provision Azure resources for teams_agent/ (M365 Agents SDK port).
#
# Creates:
#   1. OBO Entra app registration (delegated CopilotStudio.Copilots.Invoke
#      + User.Read + Dynamics CRM user_impersonation, expose api scope,
#      client secret)
#   2. Azure Bot Entra app + secret
#   3. Azure Bot resource (SingleTenant) with messaging endpoint
#   4. Microsoft Teams channel on the bot
#   5. OAuth connection setting on the bot wiring the OBO app
#
# Idempotent-ish: re-running with the same names will reuse existing apps
# / resources where possible and just print the env values.
#
# Usage:
#   .\scripts\provision-teams-agent.ps1 `
#       -ResourceGroup rg-cps-sn-agent `
#       -Location westus `
#       -BotName cps-sn-agent-dev `
#       -OboAppName cps-sn-agent-obo `
#       -BotAppName cps-sn-agent-bot `
#       -MessagingEndpoint https://my-tunnel.devtunnels.ms/api/messages
#
# After run: paste printed env block into teams_agent/.env, then rebuild
# the Teams app manifest with the printed AZURE_BOT_APP_ID:
#   .\teams_agent\manifest\build.ps1 -BotId <id> -AgentHost <tunnel host>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $ResourceGroup,
    [Parameter(Mandatory = $true)] [string] $Location,
    [Parameter(Mandatory = $true)] [string] $BotName,
    [Parameter(Mandatory = $true)] [string] $MessagingEndpoint,
    [string] $OboAppName = "$BotName-obo",
    [string] $BotAppName = "$BotName-app",
    [string] $OAuthConnectionName = 'mcs',
    [string] $Sku = 'F0'
)

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest
# az writes warnings (e.g. credential disclosure notices) to stderr which
# would otherwise trip PowerShell's NativeCommandError under Stop. We rely
# on exit code checks instead.

function Info($m) { Write-Host "[provision] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[provision] $m" -ForegroundColor Yellow }
function TrimOrEmpty($v) { if ($null -eq $v) { return '' } return ([string]$v).Trim() }

# Well-known service principal app ids for API permission targets.
$PowerPlatformApiSp = '8578e004-a5c6-46e7-913e-12f58912df43'   # Power Platform API
$PowerPlatformInvokeScope = '9332a9e9-810e-4b71-9b27-1a31d8fc8bc8' # CopilotStudio.Copilots.Invoke (delegated)
$GraphSp = '00000003-0000-0000-c000-000000000000'              # Microsoft Graph
$GraphUserReadScope = 'e1fe6dd8-ba31-4d61-89e7-88639da4683d'   # User.Read (delegated)
$DynamicsSp = '00000007-0000-0000-c000-000000000000'           # Dynamics CRM
$DynamicsUserImpersonationScope = '78ce3f0f-a1ce-49c2-8cde-64b5c0896db4' # user_impersonation (delegated)

# 0. Sanity / context ---------------------------------------------------------
Info "Resolving Azure context..."
$tenantId = TrimOrEmpty (az account show --query tenantId -o tsv)
$subId    = TrimOrEmpty (az account show --query id -o tsv)
if (-not $tenantId -or -not $subId) { throw "az login first." }
Info "Tenant: $tenantId   Subscription: $subId"

# 1. Resource group -----------------------------------------------------------
Info "Ensuring resource group $ResourceGroup in $Location..."
az group create -n $ResourceGroup -l $Location -o none

# 2. OBO Entra app ------------------------------------------------------------
Info "Ensuring OBO Entra app '$OboAppName'..."
$oboAppId = TrimOrEmpty (az ad app list --display-name $OboAppName --query "[0].appId" -o tsv)
if (-not $oboAppId) {
    $oboAppId = TrimOrEmpty (az ad app create --display-name $OboAppName `
        --sign-in-audience AzureADMyOrg `
        --query appId -o tsv)
    Info "Created OBO app appId=$oboAppId"
} else {
    Info "Reusing OBO app appId=$oboAppId"
}

# Redirect URIs (web + public client)
Info "Setting OBO redirect URIs..."
az ad app update --id $oboAppId `
    --web-redirect-uris https://token.botframework.com/.auth/web/redirect `
    --public-client-redirect-uris http://localhost `
    -o none

# Identifier URI api://botid-<oboAppId>
$identifierUri = "api://botid-$oboAppId"
Info "Setting identifier URI $identifierUri..."
az ad app update --id $oboAppId --identifier-uris $identifierUri -o none

# Expose 'defaultScope' OAuth2 permission scope (idempotent: skip if present)
$existingScopes = az ad app show --id $oboAppId --query "api.oauth2PermissionScopes[].value" -o tsv
if ($existingScopes -notcontains 'defaultScope') {
    Info "Adding 'defaultScope' OAuth2 permission scope..."
    $scopeGuid = [guid]::NewGuid().ToString()
    $scopesJson = @"
{
  "api": {
    "oauth2PermissionScopes": [
      {
        "id": "$scopeGuid",
        "adminConsentDescription": "Allow the application to call Copilot Studio on behalf of the signed-in user.",
        "adminConsentDisplayName": "Call Copilot Studio on behalf of user",
        "isEnabled": true,
        "type": "User",
        "userConsentDescription": "Allow the app to call Copilot Studio on your behalf.",
        "userConsentDisplayName": "Call Copilot Studio on your behalf",
        "value": "defaultScope"
      }
    ]
  }
}
"@
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $scopesJson -Encoding utf8
    $oboObjectId = TrimOrEmpty (az ad app show --id $oboAppId --query id -o tsv)
    $patchUri = "https://graph.microsoft.com/v1.0/applications/$oboObjectId"
    az rest --method PATCH --uri $patchUri --headers "Content-Type=application/json" --body "@$tmp" -o none
    Remove-Item $tmp -Force
} else {
    Info "OBO app already exposes 'defaultScope'."
}

# Delegated API permissions (idempotent add, then admin consent)
Info "Adding delegated API permissions to OBO app..."
$oldEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
az ad app permission add --id $oboAppId --api $PowerPlatformApiSp --api-permissions "${PowerPlatformInvokeScope}=Scope" 2>&1 | Out-Null
az ad app permission add --id $oboAppId --api $GraphSp --api-permissions "${GraphUserReadScope}=Scope" 2>&1 | Out-Null
az ad app permission add --id $oboAppId --api $DynamicsSp --api-permissions "${DynamicsUserImpersonationScope}=Scope" 2>&1 | Out-Null

Info "Granting admin consent for OBO app permissions..."
az ad app permission admin-consent --id $oboAppId 2>&1 | Out-Null
$ErrorActionPreference = $oldEAP

# Client secret for OBO app
Info "Minting OBO app client secret..."
$oboSecret = TrimOrEmpty (az ad app credential reset --id $oboAppId `
    --display-name "teams_agent provisioned $(Get-Date -Format yyyyMMddHHmm)" `
    --years 2 --query password -o tsv)

# 3. Azure Bot Entra app + secret --------------------------------------------
Info "Ensuring Azure Bot Entra app '$BotAppName'..."
$botAppId = TrimOrEmpty (az ad app list --display-name $BotAppName --query "[0].appId" -o tsv)
if (-not $botAppId) {
    $botAppId = TrimOrEmpty (az ad app create --display-name $BotAppName `
        --sign-in-audience AzureADMyOrg `
        --query appId -o tsv)
    Info "Created Bot app appId=$botAppId"
} else {
    Info "Reusing Bot app appId=$botAppId"
}

# Ensure a service principal exists for the bot app (required by Azure Bot)
$botSpExists = TrimOrEmpty (az ad sp list --filter "appId eq '$botAppId'" --query "[0].id" -o tsv)
if (-not $botSpExists) {
    Info "Creating service principal for bot app..."
    az ad sp create --id $botAppId -o none
}

Info "Minting Azure Bot client secret..."
$botSecret = TrimOrEmpty (az ad app credential reset --id $botAppId `
    --display-name "teams_agent bot secret $(Get-Date -Format yyyyMMddHHmm)" `
    --years 2 --query password -o tsv)

# 4. Azure Bot resource ------------------------------------------------------
Info "Ensuring Azure Bot resource '$BotName'..."
$botExists = (az bot show -n $BotName -g $ResourceGroup --query id -o tsv 2>$null)
if (-not $botExists) {
    Info "Creating Azure Bot..."
    az bot create -n $BotName -g $ResourceGroup `
        --app-type SingleTenant `
        --appid $botAppId `
        --tenant-id $tenantId `
        --sku $Sku `
        --endpoint $MessagingEndpoint -o none
} else {
    Info "Bot exists. Updating messaging endpoint..."
    az bot update -n $BotName -g $ResourceGroup --endpoint $MessagingEndpoint -o none
}

# 5. Teams channel -----------------------------------------------------------
Info "Ensuring Microsoft Teams channel..."
try {
    az bot msteams create -n $BotName -g $ResourceGroup -o none 2>$null
} catch {
    Warn "Teams channel create returned non-zero (often means already enabled). Continuing..."
}

# 6. OAuth Connection Setting ------------------------------------------------
Info "Ensuring OAuth connection '$OAuthConnectionName' on bot..."
$existingConn = (az bot authsetting show -n $BotName -g $ResourceGroup `
    -c $OAuthConnectionName --query name -o tsv 2>$null)
if ($existingConn) {
    Warn "OAuth connection '$OAuthConnectionName' already exists. Deleting and recreating with current OBO secret..."
    az bot authsetting delete -n $BotName -g $ResourceGroup -c $OAuthConnectionName -o none
}
az bot authsetting create -n $BotName -g $ResourceGroup `
    -c $OAuthConnectionName `
    --client-id $oboAppId `
    --client-secret $oboSecret `
    --service Aadv2 `
    --provider-scope-string "$identifierUri/defaultScope" `
    --parameters "tenantID=$tenantId" "tokenExchangeUrl=$identifierUri/defaultScope" -o none

# 7. Output env block --------------------------------------------------------
Write-Host ""
Write-Host "================ teams_agent/.env ================" -ForegroundColor Green
@"
AZURE_BOT_APP_ID=$botAppId
AZURE_BOT_APP_PASSWORD=$botSecret
AZURE_BOT_APP_TYPE=SingleTenant
AZURE_BOT_TENANT_ID=$tenantId
AZURE_BOT_OAUTH_CONNECTION_NAME=$OAuthConnectionName

OBO_CLIENT_ID=$oboAppId
OBO_CLIENT_SECRET=$oboSecret
OBO_TENANT_ID=$tenantId

# Filled from existing bridge/.env (BOT_ID + DIRECTLINE_TOKEN_ENDPOINT host).
# Verify in Copilot Studio -> Settings -> Advanced -> Metadata.
COPILOTSTUDIO_ENVIRONMENT_ID=<paste from CS Settings -> Advanced -> Metadata>
COPILOTSTUDIO_SCHEMA_NAME=<paste from CS Settings -> Advanced -> Metadata>
COPILOTSTUDIO_HANDOFF_EVENT_NAME=ServiceNowHandoff

BRIDGE_INTERNAL_URL=http://127.0.0.1:5000
PUSH_SHARED_SECRET=<long random string; also set TEAMS_AGENT_PUSH_SECRET in bridge/.env to match>

PORT=3978
LOG_LEVEL=INFO
"@ | Write-Host

Write-Host "===================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next:" -ForegroundColor Cyan
Write-Host "  1. Save the block above to teams_agent\.env" -ForegroundColor Cyan
Write-Host "  2. Rebuild Teams manifest:" -ForegroundColor Cyan
Write-Host "       .\teams_agent\manifest\build.ps1 -BotId $botAppId -AgentHost <agent-tunnel-host>" -ForegroundColor Cyan
Write-Host "  3. Sideload teams_agent\manifest\dist\teamsapp.zip in Teams." -ForegroundColor Cyan
Write-Host "  4. In bridge\.env set:" -ForegroundColor Cyan
Write-Host "       TEAMS_PUSH_TARGET=both" -ForegroundColor Cyan
Write-Host "       TEAMS_AGENT_PUSH_URL=https://<agent-tunnel-host>" -ForegroundColor Cyan
Write-Host "       TEAMS_AGENT_PUSH_SECRET=<same as PUSH_SHARED_SECRET>" -ForegroundColor Cyan
Write-Host ""
Warn "OBO_CLIENT_SECRET / AZURE_BOT_APP_PASSWORD are shown ONCE - save them now."
