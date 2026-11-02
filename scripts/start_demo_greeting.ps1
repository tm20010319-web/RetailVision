param(
    [string]$Python = "C:\Users\Administrator\miniconda3\envs\agents-py311\python.exe",
    [string]$Source = $env:VIDEO_SOURCE
)

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not $env:DATABASE_URL) {
    $databasePath = (Join-Path $projectRoot "runtime/databases/demo_flow.db").Replace("\", "/")
    $env:DATABASE_URL = "sqlite:///$databasePath"
}
if (-not $Source) {
    throw "Set VIDEO_SOURCE or pass -Source with the RTSP URL before starting the Demo greeting worker."
}

$env:DEMO_GREETING_VIDEO_SOURCE = $Source
$env:DEMO_GREETING_CAMERA_ID = if ($env:DEMO_GREETING_CAMERA_ID) { $env:DEMO_GREETING_CAMERA_ID } else { "entrance-1" }
$env:DEMO_GREETING_STORE_ID = if ($env:DEMO_GREETING_STORE_ID) { $env:DEMO_GREETING_STORE_ID } else { "store-1" }

& $Python -m demo.greeting
