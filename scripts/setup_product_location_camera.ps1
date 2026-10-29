param(
    [string]$DeviceAddress = '192.0.2.4',
    [string]$InstallRoot = 'D:\RetailVisionDev',
    [string]$DeviceUsername,
    [switch]$UseEmptyPassword
)

# Run locally: credentials are entered interactively and encrypted by Windows
# for this user/machine. Only a read-only device-information request is sent.
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'install.json'))) {
    throw 'Restore the development environment before configuring the camera.'
}
$parsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($DeviceAddress, [ref]$parsedAddress) -or
    $parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw 'DeviceAddress must be the camera IPv4 address.'
}

$settingsRoot = Join-Path $InstallRoot 'runtime\product-location'
New-Item -ItemType Directory -Path $settingsRoot -Force | Out-Null
$script:cameraSetupStage = 'starting'
function Set-SetupStage([string]$Stage) {
    $script:cameraSetupStage = $Stage
    [ordered]@{
        stage = $Stage
        device_address = $DeviceAddress
        updated_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $settingsRoot 'camera-setup-status.json') -Encoding UTF8
}
trap {
    $failureMessage = $_.Exception.Message
    $failureStage = $script:cameraSetupStage
    Set-SetupStage 'failed'
    Write-Host ("Camera setup failed: " + $failureMessage) -ForegroundColor Red
    if ($failureMessage -like '*returned HTTP 401*') {
        Write-Host 'Verify the camera device account and camera address. The EasyVMS software/cloud account may be different.'
        Write-Host 'Authentication failed; no credentials were saved.'
    } elseif ($failureStage -in @('waiting_username', 'waiting_password')) {
        Write-Host 'Run this script in an interactive PowerShell window and enter the camera device credentials.'
    } else {
        Write-Host 'See the error above. No camera settings were changed.'
    }
    exit 1
}

Write-Host "[1/3] Camera device login: $DeviceAddress" -ForegroundColor Cyan
Write-Host 'Use the camera device account, not the EasyVMS cloud account.'
if ([string]::IsNullOrWhiteSpace($DeviceUsername)) {
    Write-Host 'Camera username: type it below and press Enter.'
    Set-SetupStage 'waiting_username'
    $cameraUsername = Read-Host 'Username'
} else {
    $cameraUsername = $DeviceUsername
}
if ([string]::IsNullOrWhiteSpace($cameraUsername)) { throw 'Camera username cannot be empty.' }
if ($UseEmptyPassword) {
    $cameraPassword = [System.Security.SecureString]::new()
} else {
    Write-Host 'Camera password: input is hidden. Type the password and press Enter.'
    Set-SetupStage 'waiting_password'
    $cameraPassword = Read-Host 'Password' -AsSecureString
    if ($cameraPassword.Length -eq 0) {
        throw 'For a confirmed empty device password, run with -UseEmptyPassword.'
    }
}
$credential = [System.Management.Automation.PSCredential]::new($cameraUsername, $cameraPassword)
Write-Host '[2/3] Reading camera identity (timeout: 8 seconds)...' -ForegroundColor Cyan
Set-SetupStage 'checking_device'
Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$handler.UseProxy = $false
$handler.AllowAutoRedirect = $false
$handler.Credentials = $credential.GetNetworkCredential()
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(8)
try {
    $response = $client.GetAsync("http://$DeviceAddress/System/DeviceInfo").GetAwaiter().GetResult()
    if (-not $response.IsSuccessStatusCode) {
        throw "Device metadata request returned HTTP $([int]$response.StatusCode). Nothing saved."
    }
    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $metadata = [ordered]@{}
    $allowedFields = @('deviceName', 'name', 'serialNumber', 'serial', 'serialNum',
        'deviceSerial', 'deviceModel', 'deviceType', 'deviceTypeName', 'model', 'modelName',
        'firmwareVersion', 'softwareVersion', 'uniqueCode', 'deviceID', 'uuid')
    try {
        $xmlSettings = [System.Xml.XmlReaderSettings]::new()
        $xmlSettings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
        $xmlSettings.XmlResolver = $null
        $textReader = [System.IO.StringReader]::new($body)
        $xmlReader = [System.Xml.XmlReader]::Create($textReader, $xmlSettings)
        try {
            $document = [System.Xml.XmlDocument]::new()
            $document.XmlResolver = $null
            $document.Load($xmlReader)
            foreach ($node in $document.SelectNodes('//*')) {
                if ($node.LocalName -in $allowedFields -and -not $metadata.Contains($node.LocalName)) {
                    $metadata[$node.LocalName] = $node.InnerText.Trim()
                }
            }
        } finally {
            $xmlReader.Dispose()
            $textReader.Dispose()
        }
    } catch {
        throw 'The device response was not supported XML. Nothing saved.'
    }
    if ($metadata.Count -eq 0) {
        throw 'No camera identity fields were returned. Nothing saved.'
    }
    Write-Host '[3/3] Saving encrypted local credentials and public metadata...' -ForegroundColor Cyan
    $credentialPath = Join-Path $settingsRoot 'camera.credential.xml'
    $reportPath = Join-Path $settingsRoot 'camera-preflight.json'
    $credential | Export-Clixml -LiteralPath $credentialPath
    [ordered]@{
        device_address = $DeviceAddress
        expected_camera_name = 'behavior-detection-3'
        identity_confirmed = $false
        checked_at = [DateTimeOffset]::UtcNow.ToString('o')
        metadata = $metadata
        credential_file = $credentialPath
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host 'Camera login succeeded. Identity still needs comparison with EasyVMS.'
    $metadata | ConvertTo-Json
    Write-Host "Report: $reportPath"
    Write-Host 'Credentials are encrypted locally; do not send the credential file in chat.'
    Set-SetupStage 'complete'
} finally {
    $client.Dispose()
    $handler.Dispose()
    $credential = $null
}
