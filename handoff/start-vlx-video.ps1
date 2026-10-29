param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Video,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [string]$Config = 'config/vlx_interaction_refined.local.yaml',
    [string]$Classes = '3,4,5'
)
$ErrorActionPreference = 'Stop'
$taskProject = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (Test-Path -LiteralPath $OutputDir) { throw 'OutputDir must be new; existing evidence is preserved.' }
Push-Location -LiteralPath $taskProject
try {
    & $Python -B scripts/run_vlx_interaction_refined.py --video $Video --output-dir $OutputDir --config $Config --classes $Classes
    if ($LASTEXITCODE -ne 0) { throw "VLX process failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
