# End-to-end test of the rewritten servicenow_bridge:
#   1. POST /api/servicenow/init-session  -> sid
#   2. POST /api/servicenow/escalate {session_id: sid, opening_message: ...}
#   3. Wait for Alex to accept (or already auto-accepted)
#   4. POST /api/servicenow/user-message {session_id: sid, text: ...}
#   5. Poll /api/servicenow/poll/<sid> for any rep replies
#
# Run while watching docker logs in another window.

param(
  [string]$BaseUrl = 'http://localhost:5000',
  [string]$OpeningMessage = 'E2E probe: Alice cant send mail',
  [string]$UserMessage = 'E2E probe: this is a follow-up from /api/servicenow/user-message - Alex you should see this live.'
)

$ErrorActionPreference='Continue'
$h = @{ 'Content-Type' = 'application/json'; 'Accept' = 'application/json' }

Write-Host '=== 1. init-session ==='
$initBody = @{ user_email = 'alice@contoso.com'; user_display_name = 'Alice' } | ConvertTo-Json
$init = Invoke-RestMethod -Uri "$BaseUrl/api/servicenow/init-session" -Method POST -Headers $h -Body $initBody
$init | ConvertTo-Json -Depth 5
$sid = $init.session_id
if (-not $sid) { Write-Host 'no sid'; exit 1 }

Write-Host ''
Write-Host '=== 2. escalate ==='
$escBody = @{ session_id = $sid; opening_message = $OpeningMessage } | ConvertTo-Json
$esc = Invoke-RestMethod -Uri "$BaseUrl/api/servicenow/escalate" -Method POST -Headers $h -Body $escBody
$esc | ConvertTo-Json -Depth 5

Write-Host ''
Write-Host "=== 3. waiting 5s for AWA ==="
Start-Sleep -Seconds 5

Write-Host ''
Write-Host '=== 4. user-message ==='
$umBody = @{ session_id = $sid; text = $UserMessage } | ConvertTo-Json
try {
  $um = Invoke-RestMethod -Uri "$BaseUrl/api/servicenow/user-message" -Method POST -Headers $h -Body $umBody
  $um | ConvertTo-Json -Depth 5
} catch {
  Write-Host "user-message error: $_"
}

Write-Host ''
Write-Host "=== 5. poll  (sid=$sid) ==="
$poll = Invoke-RestMethod -Uri "$BaseUrl/api/servicenow/poll/$sid" -Method GET -Headers $h
$poll | ConvertTo-Json -Depth 5

Write-Host ''
Write-Host "Bridge sid=$sid  interaction=$($esc.interaction_number)"
Write-Host 'Now go reply as Alex in SOW and re-run: '
Write-Host "  Invoke-RestMethod -Uri '$BaseUrl/api/servicenow/poll/$sid' -Method GET"
