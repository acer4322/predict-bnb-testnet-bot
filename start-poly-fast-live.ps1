param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$PidFile = Join-Path $Root ".poly-fast-live.pid"
$Port = 8782
New-Item -ItemType Directory -Force -Path $Data | Out-Null

$UserEnvironment = @(
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_LIVE_API_KEY",
    "BINANCE_LIVE_API_SECRET",
    "PREDICT_LIVE_ACCOUNT_TYPE",
    "PREDICT_POLY_FAST_LIVE_ENABLED",
    "PREDICT_POLY_FAST_BINANCE_POLL_SECONDS",
    "PREDICT_POLY_FAST_SAMPLE_SECONDS",
    "PREDICT_POLY_GAP_LIVE_STAKE_USDT",
    "PREDICT_POLY_GAP_LIVE_MAX_LOSS_USDT",
    "PREDICT_POLY_GAP_LIVE_MAX_LOSS_ENABLED"
)
foreach ($Name in $UserEnvironment) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

if ([string]::IsNullOrWhiteSpace($env:PREDICT_POLY_FAST_LIVE_ENABLED)) {
    $env:PREDICT_POLY_FAST_LIVE_ENABLED = "true"
}
$env:PREDICT_POLY_FAST_LIVE_HOST = "127.0.0.1"
$env:PREDICT_POLY_FAST_LIVE_PORT = "$Port"

function Get-ListenerPid {
    try {
        $row = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($row) { return [int]$row.OwningProcess }
    }
    catch { }
    return $null
}

function Test-FastLive {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        return [int]$response.StatusCode -eq 200
    }
    catch { return $false }
}

$ExistingPid = Get-ListenerPid
if ($ExistingPid) {
    $CommandLine = ""
    try {
        $CommandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ExistingPid").CommandLine
    }
    catch { }
    if (-not $CommandLine.ToLowerInvariant().Contains("predict_bot.poly_fast_live")) {
        throw "Port $Port is already owned by another process. PID=$ExistingPid command=$CommandLine"
    }
    if (-not (Test-FastLive)) {
        throw "Poly Fast Live owns port $Port but its health endpoint is not responding."
    }
    Write-Host "Poly Fast Live already online on http://127.0.0.1:$Port (PID=$ExistingPid)."
    $ExistingPid | Set-Content $PidFile
    exit 0
}

$Stdout = Join-Path $Data "poly-fast-live.stdout.log"
$Stderr = Join-Path $Data "poly-fast-live.stderr.log"
$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.poly_fast_live") `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr `
    -PassThru
$Process.Id | Set-Content $PidFile

$Deadline = (Get-Date).AddSeconds(45)
do {
    if ($Process.HasExited) {
        $tail = ""
        if (Test-Path $Stderr) { $tail = (Get-Content $Stderr -Tail 50) -join [Environment]::NewLine }
        throw "Poly Fast Live exited during startup. $tail"
    }
    if (Test-FastLive) { break }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $Deadline)

if (-not (Test-FastLive)) {
    throw "Poly Fast Live did not become healthy on port $Port. Check $Stderr"
}

Write-Host "Poly Fast Live is ready: http://127.0.0.1:$Port/state"
Write-Host "Only Binance Prediction + Polymarket feeds and the existing V3 live executors are active."
Write-Host "ETH and BNB remain governed by each engine's persisted runtime setting / V3 safe-pause migration."

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:$Port/state"
}
