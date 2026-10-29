param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$taskTailscale = 'C:/Program Files/Tailscale/tailscale.exe'
if (-not (Test-Path -LiteralPath $taskTailscale)) { throw 'Tailscale is not installed.' }
$taskStatus = & $taskTailscale status --json | ConvertFrom-Json
if ($taskStatus.BackendState -ne 'Running') {
    throw 'Tailscale login is required. Run tailscale up, then sign in using your own account.'
}
$taskExisting = (& $taskTailscale funnel status --json) -join "`n"
if ($taskExisting -match 'Proxy' -and $taskExisting -notmatch '127\.0\.0\.1:8787') {
    throw 'Existing Funnel configuration belongs to another service; it was left unchanged.'
}
if ($Stop) {
    & $taskTailscale funnel --https=443 off
    exit $LASTEXITCODE
}
$taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/healthz' -TimeoutSec 5
if ($taskHealth.status -ne 'ready') { throw 'VLX API is not ready.' }
try {
    Invoke-WebRequest -Uri 'http://127.0.0.1:8787/api/v1/item-search/catalog' -UseBasicParsing -TimeoutSec 5 | Out-Null
    throw 'The API must reject unauthenticated access before publishing.'
} catch {
    if ($null -eq $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 401) { throw }
}
& $taskTailscale funnel --bg --https=443 --yes http://127.0.0.1:8787
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $taskTailscale funnel status
