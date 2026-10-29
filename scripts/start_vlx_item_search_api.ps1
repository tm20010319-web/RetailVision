param([switch]$IssueToken, [switch]$Stop)
$ErrorActionPreference = 'Stop'
if ($IssueToken) { throw 'VLX uses the existing Java login Token. No separate Token is issued.' }
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '../RetailVisionDev/envs/vlx-seek-py311/Scripts/python.exe'
if ($Stop) {
    $taskCurrent = Get-Content (Join-Path $taskRoot '../RetailVisionDev/runtime/vlx-item-search-api/current.json') -Raw | ConvertFrom-Json
    Set-Content -LiteralPath (Join-Path $taskCurrent.runtime 'stop.request') -Value 'User stopped API'
    exit 0
}
$taskArgs = @('-B',(Join-Path $PSScriptRoot 'run_vlx_item_search_api.py'))
$taskApi = Start-Process -FilePath $taskPython -ArgumentList $taskArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru
Write-Output "API starting on http://127.0.0.1:8787 ; launcher PID $($taskApi.Id)"
