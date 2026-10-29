param([switch]$Headless, [double]$Duration = 0, [int]$MaxWindows = 0)
$ErrorActionPreference = 'Stop'
$projectDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$entryPath = Join-Path $projectDirectory 'scripts\run_toy_guitar_tracking.py'
$configPath = Join-Path $projectDirectory 'config\toy_guitar_tracking_vlx.yaml'
$arguments = @('-B', $entryPath, '--config', $configPath, '--duration', [string]$Duration)
if ($Headless) { $arguments += '--headless' }
if ($MaxWindows -gt 0) { $arguments += @('--max-windows', [string]$MaxWindows) }
& 'D:\RetailVisionDev\envs\agents-py311\python.exe' @arguments
exit $LASTEXITCODE
