param([switch]$Headless, [double]$Duration = 0)
$ErrorActionPreference = 'Stop'
$projectDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectDirectory '..\RetailVisionDev\envs\vlx-seek-py311\Scripts\python.exe'
$entryPath = Join-Path $projectDirectory 'scripts\run_toy_guitar_vlx_live.py'
$arguments = @('-B', $entryPath, '--config', (Join-Path $projectDirectory 'config\toy_guitar_vlx_live.yaml'), '--duration', [string]$Duration)
if ($Headless) { $arguments += '--headless' }
& $pythonPath @arguments
exit $LASTEXITCODE
