param([switch]$Headless, [double]$Duration = 0)
$ErrorActionPreference = 'Stop'
$projectDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = 'D:\RetailVisionDev\envs\agents-py311\python.exe'
$entryPath = Join-Path $projectDirectory 'scripts\run_toy_guitar_live.py'
$configPath = Join-Path $projectDirectory 'config\toy_guitar_live.yaml'
$arguments = @('-B', $entryPath, '--config', $configPath, '--duration', [string]$Duration)
if ($Headless) { $arguments += '--headless' }
& $pythonPath @arguments
exit $LASTEXITCODE
