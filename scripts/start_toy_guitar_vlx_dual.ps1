param([switch]$Headless, [double]$Duration = 0, [string]$Target = '')
$ErrorActionPreference = 'Stop'
$projectDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectDirectory '..\RetailVisionDev\envs\vlx-seek-py311\Scripts\python.exe'
$entryPath = Join-Path $projectDirectory 'scripts\run_toy_guitar_vlx_dual.py'
$arguments = @('-B', $entryPath, '--config', (Join-Path $projectDirectory 'config\toy_guitar_vlx_dual.yaml'), '--duration', [string]$Duration)
if ($Headless) { $arguments += '--headless' }
if ($Target) { $arguments += @('--target', $Target) }
& $pythonPath @arguments
exit $LASTEXITCODE
