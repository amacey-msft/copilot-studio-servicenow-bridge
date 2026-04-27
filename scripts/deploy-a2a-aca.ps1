# Deploy teams_a2a (v3 spike) to Azure Container Apps.
#
# Reuse existing ACA environment + ACR from the rg-cpv-aca resource group.
# Skill app name: ca-cps-sn-skill.
#
# Usage:
#   ./scripts/deploy-skill-aca.ps1                  # full build + push + deploy
#   ./scripts/deploy-skill-aca.ps1 -SkipBuild       # update existing image only
#
# Env vars (must be set or passed):
#   A2A_APP_ID, A2A_APP_PASSWORD, A2A_TENANT_ID, CS_PARENT_APP_ID
#   (provide AFTER Phase 3 — for Phase 2 first deploy, fakes are fine; the
#    /healthz route does not need them and lazy-init defers AAD use.)

param(
    [string]$ResourceGroup = 'rg-cpv-aca',
    [string]$AcaEnv        = 'cae-cpv',
    [string]$Acr           = 'acrcpvb0c139ea',
    [string]$AppName       = 'ca-cps-sn-skill',
    [string]$Image         = 'teams-a2a',
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Continue'
# az CLI emits informational lines on stderr (e.g. "WARNING: Packing source...")
# which PS treats as NativeCommandError under 'Stop'. Use Continue + explicit
# $LASTEXITCODE checks for actual failures.
$tag = "v$(Get-Date -Format 'MMddHHmm')"
# When SkipBuild we deploy :latest (whatever the last successful build was)
if ($SkipBuild) { $tag = 'latest' }
$fullImage = "$Acr.azurecr.io/${Image}:$tag"

Push-Location (Resolve-Path "$PSScriptRoot/..")
try {
    if (-not $SkipBuild) {
        Write-Host "==> Building image $fullImage" -ForegroundColor Cyan
        # --no-logs avoids colorama emoji crash on Windows cp1252 console.
        az acr build `
            --registry $Acr `
            --image "${Image}:$tag" `
            --image "${Image}:latest" `
            --file teams_a2a/Dockerfile `
            --no-logs `
            . | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host "acr build failed exit=$LASTEXITCODE" -ForegroundColor Red; exit 1 }
    }

    # Check if app exists
    $exists = $null
    try {
        $exists = az containerapp show -n $AppName -g $ResourceGroup --query name -o tsv 2>$null
    } catch {
        $exists = $null
    }

    # Source env values (allow caller to override via real env)
    $skillAppId   = $env:A2A_APP_ID;        if (-not $skillAppId)   { $skillAppId   = '00000000-0000-0000-0000-000000000000' }
    $skillSecret  = $env:A2A_APP_PASSWORD;  if (-not $skillSecret)  { $skillSecret  = 'placeholder' }
    $skillTenant  = $env:A2A_TENANT_ID;     if (-not $skillTenant)  { $skillTenant  = '00000000-0000-0000-0000-000000000000' }
    $csParentId   = $env:CS_PARENT_APP_ID;    if (-not $csParentId)   { $csParentId   = '00000000-0000-0000-0000-000000000000' }
    # ServiceNow connection (mirrors bridge/.env). Required for the
    # endConversation handler to actually open a live-agent chat.
    $snInstance   = $env:SN_INSTANCE;         if (-not $snInstance)   { $snInstance   = '' }
    $snUser       = $env:SN_USER;             if (-not $snUser)       { $snUser       = '' }
    $snPassword   = $env:SN_PASSWORD;         if (-not $snPassword)   { $snPassword   = '' }
    # Shared secret for inbound SN BR webhook -> /api/sn-webhook (rep replies).
    # NOTE: PowerShell + az CLI mishandle secret values containing '&', '}', '%'
    # (cmd metachars). Pick an alphanumeric+dash secret for the spike or update
    # the secret manually via a .cmd file indirection. See user memory.
    $snWhSecret   = $env:SN_WEBHOOK_SECRET;   if (-not $snWhSecret)   { $snWhSecret   = '' }
    if ($snWhSecret -match '[&}%]') {
        Write-Host "==> WARNING: SN_WEBHOOK_SECRET contains shell metachars (& } %); set may truncate." -ForegroundColor Yellow
    }

    if (-not $exists) {
        Write-Host "==> Creating new container app $AppName" -ForegroundColor Cyan
        az containerapp create `
            --name $AppName `
            --resource-group $ResourceGroup `
            --environment $AcaEnv `
            --image $fullImage `
            --target-port 3979 `
            --ingress external `
            --transport http `
            --min-replicas 0 `
            --max-replicas 2 `
            --registry-server "$Acr.azurecr.io" `
            --registry-username (az acr credential show -n $Acr --query username -o tsv) `
            --registry-password (az acr credential show -n $Acr --query passwords[0].value -o tsv) `
            --secrets "skill-app-password=$skillSecret" "sn-password=$snPassword" "sn-webhook-secret=$snWhSecret" `
            --env-vars `
                "A2A_APP_ID=$skillAppId" `
                "A2A_APP_PASSWORD=secretref:skill-app-password" `
                "A2A_TENANT_ID=$skillTenant" `
                "CS_PARENT_APP_ID=$csParentId" `
                "A2A_PUBLIC_URL=https://placeholder" `
                "SN_INSTANCE=$snInstance" `
                "SN_USER=$snUser" `
                "SN_PASSWORD=secretref:sn-password" `
                "SN_WEBHOOK_SECRET=secretref:sn-webhook-secret" `
                "PORT=3979" | Out-Host
    } else {
        Write-Host "==> Updating container app $AppName with new revision $tag" -ForegroundColor Cyan
        # Ensure sn-password + sn-webhook-secret are up to date before referencing them.
        az containerapp secret set `
            --name $AppName `
            --resource-group $ResourceGroup `
            --secrets "sn-password=$snPassword" "sn-webhook-secret=$snWhSecret" | Out-Null
        az containerapp update `
            --name $AppName `
            --resource-group $ResourceGroup `
            --image $fullImage `
            --revision-suffix $tag `
            --set-env-vars `
                "A2A_APP_ID=$skillAppId" `
                "A2A_APP_PASSWORD=secretref:skill-app-password" `
                "A2A_TENANT_ID=$skillTenant" `
                "CS_PARENT_APP_ID=$csParentId" `
                "SN_INSTANCE=$snInstance" `
                "SN_USER=$snUser" `
                "SN_PASSWORD=secretref:sn-password" `
                "SN_WEBHOOK_SECRET=secretref:sn-webhook-secret" `
                "PORT=3979" | Out-Host
    }

    # Get FQDN
    $fqdn = az containerapp show -n $AppName -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
    $publicUrl = "https://$fqdn"
    Write-Host "==> Public URL: $publicUrl" -ForegroundColor Green

    # Patch A2A_PUBLIC_URL with real FQDN so manifest reports correctly
    az containerapp update `
        --name $AppName `
        --resource-group $ResourceGroup `
        --set-env-vars "A2A_PUBLIC_URL=$publicUrl" | Out-Null

    # Verify health
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

    Write-Host "`n==> Deployed: $publicUrl" -ForegroundColor Green
    Write-Host "    Manifest:    $publicUrl/skill-manifest.json"
    Write-Host "    Messages:    $publicUrl/api/messages"
    Write-Host "    Health:      $publicUrl/healthz"
} finally {
    Pop-Location
}
