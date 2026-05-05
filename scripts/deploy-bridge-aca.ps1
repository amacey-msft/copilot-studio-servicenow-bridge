# Deploy bridge/ to Azure Container Apps.
#
# Reuses existing ACA environment + ACR from the rg-cpv-aca resource group.
# Bridge app name: ca-cps-bridge.
#
# Usage:
#   ./scripts/deploy-bridge-aca.ps1                  # full build + push + deploy
#   ./scripts/deploy-bridge-aca.ps1 -SkipBuild       # update existing image only
#
# Reads bridge/.env for secrets/env values. The ACA app is configured with
# min=max=1 because the bridge holds session state in process memory; a
# revision swap during an active live chat will drop that session. Track
# Redis externalisation as a follow-up.

param(
    [string]$ResourceGroup = 'rg-cpv-aca',
    [string]$AcaEnv        = 'cae-cpv',
    [string]$Acr           = 'acrcpvb0c139ea',
    [string]$AppName       = 'ca-cps-bridge',
    [string]$Image         = 'bridge',
    [string]$EnvFile       = 'bridge/.env',
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Continue'
$tag = "v$(Get-Date -Format 'MMddHHmm')"
if ($SkipBuild) { $tag = 'latest' }
$fullImage = "$Acr.azurecr.io/${Image}:$tag"

Push-Location (Resolve-Path "$PSScriptRoot/..")
try {
    # ---------------------------------------------------------------------
    # Read bridge/.env (KEY=VALUE lines, ignore comments/blank). az CLI is
    # not given the secret values verbatim on the command line; we pass them
    # via --secrets and reference them via secretref:.
    # ---------------------------------------------------------------------
    if (-not (Test-Path $EnvFile)) {
        Write-Host "==> $EnvFile not found. Aborting." -ForegroundColor Red
        exit 1
    }
    $envMap = @{}
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*#') { continue }
        if ($line -notmatch '=') { continue }
        $k,$v = $line -split '=', 2
        $k = $k.Trim()
        $v = $v.Trim().Trim('"').Trim("'")
        if ($k) { $envMap[$k] = $v }
    }

    function Get-Env([string]$name) {
        if ($envMap.ContainsKey($name)) { return $envMap[$name] }
        return ''
    }

    $directlineTokenEndpoint = Get-Env 'DIRECTLINE_TOKEN_ENDPOINT'
    $directlineSecret        = Get-Env 'DIRECTLINE_SECRET'
    $snInstance              = Get-Env 'SN_INSTANCE'
    $snUser                  = Get-Env 'SN_USER'
    $snPassword              = Get-Env 'SN_PASSWORD'
    $snWebhookSecret         = Get-Env 'SN_WEBHOOK_SECRET'
    $agentApiSecret          = Get-Env 'AGENT_API_SECRET'
    $snBridgeApiBase         = Get-Env 'SN_BRIDGE_API_BASE'
    $snDefaultUser           = Get-Env 'SN_DEFAULT_USER_SYS_ID'
    $snDefaultQueue          = Get-Env 'SN_DEFAULT_QUEUE_SYS_ID'
    $snDefaultChannel        = Get-Env 'SN_DEFAULT_CHANNEL_SYS_ID'
    $teamsAgentPushUrl       = Get-Env 'TEAMS_AGENT_PUSH_URL'
    $teamsAgentPushSecret    = Get-Env 'TEAMS_AGENT_PUSH_SECRET'

    foreach ($pair in @(
        @('SN_INSTANCE',    $snInstance),
        @('SN_USER',        $snUser),
        @('SN_PASSWORD',    $snPassword),
        @('SN_WEBHOOK_SECRET', $snWebhookSecret)
    )) {
        if (-not $pair[1]) {
            Write-Host "==> WARNING: $($pair[0]) is empty in $EnvFile" -ForegroundColor Yellow
        }
    }
    foreach ($name in @('SN_PASSWORD','SN_WEBHOOK_SECRET','AGENT_API_SECRET','DIRECTLINE_SECRET','TEAMS_AGENT_PUSH_SECRET')) {
        $v = Get-Env $name
        if ($v -match '[&}%]') {
            Write-Host "==> WARNING: $name contains shell metachars (& } %); az CLI may truncate." -ForegroundColor Yellow
        }
    }

    # ---------------------------------------------------------------------
    # Build + push image.
    # ---------------------------------------------------------------------
    if (-not $SkipBuild) {
        Write-Host "==> Building image $fullImage" -ForegroundColor Cyan
        az acr build `
            --registry $Acr `
            --image "${Image}:$tag" `
            --image "${Image}:latest" `
            --file bridge/Dockerfile `
            --no-logs `
            . | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host "acr build failed exit=$LASTEXITCODE" -ForegroundColor Red; exit 1 }
    }

    # ---------------------------------------------------------------------
    # Create or update the container app. Single replica; flask-sock + gevent
    # worker terminates on the same process, in-memory session state must
    # survive across requests.
    # ---------------------------------------------------------------------
    $exists = $null
    try {
        $exists = az containerapp show -n $AppName -g $ResourceGroup --query name -o tsv 2>$null
    } catch { $exists = $null }

    $secrets = @(
        "directline-secret=$directlineSecret",
        "sn-webhook-secret=$snWebhookSecret",
        "agent-api-secret=$agentApiSecret",
        "teams-agent-push-secret=$teamsAgentPushSecret"
    )
    # SN_PASSWORD is set separately via a .cmd file because PowerShell + az.cmd
    # mishandle '}' '&' '%' inside argument values (the az.cmd wrapper hands
    # the value to cmd.exe which interprets the metachars). cmd's own quoting
    # rules tolerate them when embedded in a literal .cmd file. See user
    # memory + tools/set_skill_secrets.cmd for the same pattern.
    $needsCmdFile = ($snPassword -match '[&}%<>|^]')

    # Env vars common to create + update.
    $envVars = @(
        "DIRECTLINE_TOKEN_ENDPOINT=$directlineTokenEndpoint",
        "DIRECTLINE_SECRET=secretref:directline-secret",
        "SN_INSTANCE=$snInstance",
        "SN_USER=$snUser",
        "SN_PASSWORD=secretref:sn-password",
        "SN_WEBHOOK_SECRET=secretref:sn-webhook-secret",
        "AGENT_API_SECRET=secretref:agent-api-secret",
        "SN_BRIDGE_API_BASE=$snBridgeApiBase",
        "SN_DEFAULT_USER_SYS_ID=$snDefaultUser",
        "SN_DEFAULT_QUEUE_SYS_ID=$snDefaultQueue",
        "SN_DEFAULT_CHANNEL_SYS_ID=$snDefaultChannel",
        "TEAMS_AGENT_PUSH_URL=$teamsAgentPushUrl",
        "TEAMS_AGENT_PUSH_SECRET=secretref:teams-agent-push-secret",
        "PORT=5000"
    )

    if (-not $exists) {
        Write-Host "==> Creating new container app $AppName" -ForegroundColor Cyan
        az containerapp create `
            --name $AppName `
            --resource-group $ResourceGroup `
            --environment $AcaEnv `
            --image $fullImage `
            --target-port 5000 `
            --ingress external `
            --transport auto `
            --min-replicas 1 `
            --max-replicas 1 `
            --registry-server "$Acr.azurecr.io" `
            --registry-username (az acr credential show -n $Acr --query username -o tsv) `
            --registry-password (az acr credential show -n $Acr --query passwords[0].value -o tsv) `
            --secrets $secrets `
            --env-vars $envVars | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host "containerapp create failed exit=$LASTEXITCODE" -ForegroundColor Red; exit 1 }
    } else {
        Write-Host "==> Updating container app $AppName with new revision $tag" -ForegroundColor Cyan
        az containerapp secret set `
            --name $AppName `
            --resource-group $ResourceGroup `
            --secrets $secrets | Out-Null
        az containerapp update `
            --name $AppName `
            --resource-group $ResourceGroup `
            --image $fullImage `
            --revision-suffix $tag `
            --set-env-vars $envVars | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host "containerapp update failed exit=$LASTEXITCODE" -ForegroundColor Red; exit 1 }
    }

    # Set sn-password separately via a .cmd file when the value contains
    # cmd.exe metachars (}, &, %, etc.). PowerShell can't safely pass these
    # through the az.cmd wrapper without truncation.
    if ($needsCmdFile) {
        Write-Host "==> Setting sn-password via temporary .cmd file (value has cmd metachars)" -ForegroundColor Cyan
        $tmpCmd = Join-Path $env:TEMP "set-bridge-sn-password.cmd"
        # Single literal line; cmd handles ^, &, } etc. inside double-quoted args.
        $line = "az containerapp secret set --name $AppName --resource-group $ResourceGroup --secrets `"sn-password=$snPassword`""
        Set-Content -Path $tmpCmd -Value $line -Encoding ASCII
        & cmd.exe /c $tmpCmd | Out-Host
        Remove-Item $tmpCmd -ErrorAction SilentlyContinue
        if ($LASTEXITCODE -ne 0) { Write-Host "sn-password set failed exit=$LASTEXITCODE" -ForegroundColor Red; exit 1 }
        # Re-apply env-vars now that the secret exists, so SN_PASSWORD secretref resolves.
        az containerapp update `
            --name $AppName `
            --resource-group $ResourceGroup `
            --set-env-vars "SN_PASSWORD=secretref:sn-password" | Out-Null
    }

    # ---------------------------------------------------------------------
    # Resolve FQDN, then health-check the new revision.
    # ---------------------------------------------------------------------
    $fqdn = az containerapp show -n $AppName -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
    $publicUrl = "https://$fqdn"
    Write-Host "==> Public URL: $publicUrl" -ForegroundColor Green

    Write-Host "==> Health check (may take ~30s for cold start)..." -ForegroundColor Cyan
    $maxAttempts = 12
    for ($i = 1; $i -le $maxAttempts; $i++) {
        Start-Sleep -Seconds 5
        try {
            $r = Invoke-RestMethod -Uri "$publicUrl/healthz" -TimeoutSec 5
            Write-Host "==> /healthz OK on attempt $i :" -ForegroundColor Green
            $r | ConvertTo-Json
            break
        } catch {
            Write-Host "  attempt $i : not ready yet ($($_.Exception.Message))" -ForegroundColor Yellow
        }
        if ($i -eq $maxAttempts) {
            Write-Host "==> Health check failed after $maxAttempts attempts" -ForegroundColor Red
            exit 1
        }
    }

    # Confirm new revision is the active one + 100% traffic.
    $rev = az containerapp revision list -n $AppName -g $ResourceGroup `
        --query "[?properties.active].{name:name, healthState:properties.healthState, traffic:properties.trafficWeight, replicas:properties.replicas}" `
        -o table
    Write-Host "==> Active revisions:" -ForegroundColor Cyan
    Write-Host $rev

    Write-Host "`n==> Deployed: $publicUrl" -ForegroundColor Green
    Write-Host "    Intranet:        $publicUrl/"
    Write-Host "    Health:          $publicUrl/healthz"
    Write-Host "    DL token:        $publicUrl/directline/token"
    Write-Host "    SN webhook:      $publicUrl/api/sn-webhook"
    Write-Host ""
    Write-Host "==> Next:" -ForegroundColor Cyan
    Write-Host "    1. Set BRIDGE_PUBLIC_URL=$publicUrl in bridge/.env"
    Write-Host "    2. Run scripts/sync-bridge-url.ps1 to repoint SN sys_property + CS HTTP-tool botcomponents."
    Write-Host "    3. Open each updated CS HTTP tool in the Studio UI and re-save (per memory: programmatic botcomponent edits sometimes need manual UI save before they activate)."
    Write-Host "    4. Publish the CS agent."
} finally {
    Pop-Location
}
