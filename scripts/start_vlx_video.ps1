$ErrorActionPreference = 'Stop'
try {
    $taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $taskSettings = Get-Content -LiteralPath (Join-Path $taskRoot 'config/vlx_video_launcher.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $taskPython = $taskSettings.vlx_python
    if (-not [IO.Path]::IsPathRooted($taskPython)) { $taskPython = Join-Path $taskRoot $taskPython }
    if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
        throw 'Python not found. Set vlx_python in config/vlx_video_launcher.json.'
    }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $taskPython -B (Join-Path $PSScriptRoot 'run_vlx_video_job.py')
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
