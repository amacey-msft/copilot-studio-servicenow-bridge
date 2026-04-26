# Tear down resources created by provision-teams-agent.ps1.
#
# Deletes:
#   - Azure Bot resource ($BotName) and its OAuth connection
#   - Azure Bot Entra app ($BotAppName)
#   - OBO Entra app ($OboAppName)
#
# Does NOT delete the resource group (might contain other things).
#
# Usage:
#   .\scripts\teardown-teams-agent.ps1 `
#       -ResourceGroup rg-cps-sn-agent `
#       -BotName cps-sn-agent-dev `
#       -OboAppName cps-sn-agent-obo `
#       -BotAppName cps-sn-agent-app

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $ResourceGroup,
    [Parameter(Mandatory = $true)] [string] $BotName,
    [string] $OboAppName = "$BotName-obo",
    [string] $BotAppName = "$BotName-app",
    [switch] $Force
)

$ErrorActionPreference = 'Continue'
function Info($m) { Write-Host "[teardown] $m" -ForegroundColor Cyan }

if (-not $Force) {
    $resp = Read-Host "Delete bot '$BotName' (rg=$ResourceGroup) and Entra apps '$OboAppName','$BotAppName'? Type DELETE to confirm"
    if ($resp -ne 'DELETE') { Write-Host "Aborted."; exit 0 }
}

Info "Deleting Azure Bot $BotName..."
az bot delete -n $BotName -g $ResourceGroup -o none 2>$null

foreach ($name in @($BotAppName, $OboAppName)) {
    $appId = (az ad app list --display-name $name --query "[0].appId" -o tsv).Trim()
    if ($appId) {
        Info "Deleting Entra app '$name' ($appId)..."
        az ad app delete --id $appId 2>$null
    } else {
        Info "Entra app '$name' not found - skipping."
    }
}

Info "Done. Resource group '$ResourceGroup' was NOT deleted."
