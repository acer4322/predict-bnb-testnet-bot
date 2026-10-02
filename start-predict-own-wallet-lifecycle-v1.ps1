param(
    [int]$Port = 8795,
    [string]$Database = "data/predict_own_wallet_lifecycle_v1.db"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$PidPath = Join-Path $ProjectRoot ".predict-own-wallet-lifecycle-v1.pid"

function Import-UserEnvironment([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = [Environment]::GetEnvironmentVariable($Name, "User")
    }
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        [Environment]::SetEnvironmentVariable($Name, $value, "Process")
    }
}

foreach ($name in @(
    "PREDICT_FUN_API_KEY",
    "PREDICT_FUN_PRIVATE_KEY",
    "PREDICT_FUN_PRIVY_PRIVATE_KEY",
    "PREDICT_FUN_ACCOUNT_ADDRESS",
    "PREDICT_FUN_JWT"
)) {
    Import-UserEnvironment $name
}

if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required"
}
if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_PRIVATE_KEY) -and
    [string]::IsNullOrWhiteSpace($env:PREDICT_FUN_PRIVY_PRIVATE_KEY)) {
    throw "PREDICT_FUN_PRIVATE_KEY or PREDICT_FUN_PRIVY_PRIVATE_KEY is required for wallet JWT authentication"
}

if (Test-Path -LiteralPath $PidPath) {
    $existingPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$existingPid)
    if ($existingPid -gt 0) {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$existingPid" -ErrorAction SilentlyContinue
        if ($existing -and $existing.CommandLine -match "predict_own_wallet_lifecycle_collector_v1") {
            Write-Host "Predict own-wallet lifecycle collector is already running (PID $existingPid)."
            exit 0
        }
    }
}

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:PREDICT_OWN_WALLET_LIFECYCLE_PORT = [string]$Port
$env:PREDICT_OWN_WALLET_LIFECYCLE_DB = Join-Path $ProjectRoot $Database

$process = Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "predict_bot.predict_own_wallet_lifecycle_collector_v1") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -PassThru
Set-Content -LiteralPath $PidPath -Value ([string]$process.Id) -Encoding ascii

$stateUrl = "http://127.0.0.1:$Port/state"
for ($attempt = 0; $attempt -lt 90; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        throw "Predict own-wallet lifecycle collector exited during startup with code $($process.ExitCode)"
    }
    try {
        $state = Invoke-RestMethod -Uri $stateUrl -TimeoutSec 2
        if ($state.version -eq "PREDICT_OWN_WALLET_LIFECYCLE_COLLECTOR_V1" -and
            $state.status -eq "LIVE" -and
            [bool]$state.websocket.subscriptionReady -and
            [bool]$state.websocket.dataCaptureReady) {
            Write-Host "Predict own-wallet lifecycle collector started (PID $($process.Id), port $Port)."
            Write-Host "Status=$($state.status) readOnly=$($state.readOnly) canPlaceOrders=$($state.canPlaceOrders)"
            exit 0
        }
    }
    catch {
    }
}

throw "Collector process started but /state did not become ready at $stateUrl"
