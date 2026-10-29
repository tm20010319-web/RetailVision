# Foreground, operator-owned receiver. Configuration affects only this shell
# and its child; no system environment, PEAK camera or credentials are changed.
param(
    [ValidateRange(0.0, 1.0)]
    [double]$SimilarityThreshold = 0.75
)
$ErrorActionPreference = 'Stop'
Set-Location D:/GJJ/work/code
$receiverPython = 'C:/Users/Administrator/miniconda3/envs/agents-py311/python.exe'
if (Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 8081 is already listening; inspect the existing receiver instead of replacing it.'
}
$env:DATABASE_URL = 'sqlite:///D:/GJJ/work/code/demo_flow.db'
$env:RETAILVISION_CAMERA_CONFIG_PATH = 'D:/GJJ/work/code/configs/behavior_cameras.cam03.identity.json'
$env:BEHAVIOR_CONFIG_PATH = $env:RETAILVISION_CAMERA_CONFIG_PATH
$env:CAMERA_FACE_DEVICE_ID = 'cam03'
$env:CAMERA_FACE_BASE_URL = 'http://192.0.2.4'
$env:P6S_SIMILARITY_THRESHOLD = $SimilarityThreshold.ToString([System.Globalization.CultureInfo]::InvariantCulture)
# Reuse the already persisted cam03 fingerprint, without printing it or
# writing the Unique Code into this script. The receiver does not query faces.
$serialLookup = @'
import json, sqlite3
with sqlite3.connect('file:D:/GJJ/work/code/demo_flow.db?mode=ro',uri=True) as c:
    row=c.execute("SELECT device_metadata FROM push_coverage_observations WHERE device_id=? AND device_metadata IS NOT NULL ORDER BY observed_at DESC LIMIT 1",('cam03',)).fetchone()
    metadata=json.loads(row[0]) if row else {}
    serial=metadata.get('device_serial',{}).get('normalized_value','')
    if not serial or not serial.isalnum():
        raise SystemExit('persisted_cam03_serial_unavailable')
    print(serial)
'@
$receiverSerial = & $receiverPython -c $serialLookup
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the already configured cam03 serial.' }
$env:P6S_DEVICE_SERIAL = $receiverSerial.Trim()
Write-Host "Starting existing cam03 Push receiver on 192.0.2.7:8081, threshold $env:P6S_SIMILARITY_THRESHOLD. Keep this window open."
& $receiverPython D:/GJJ/work/code/scripts/run_cam03_push_receiver.py
