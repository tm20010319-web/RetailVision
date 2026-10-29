param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$Config = 'config/vlx_item_search_api.local.yaml'
)
$ErrorActionPreference = 'Stop'
$taskProject = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location -LiteralPath $taskProject
try {
    & $Python -B scripts/run_vlx_item_search_api.py --config $Config
    if ($LASTEXITCODE -ne 0) { throw "API process failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
