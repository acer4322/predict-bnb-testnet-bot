param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$PidFile = Join-Path $Root ".poly-fast-live.pid"
$Port = 8792
New-Item -ItemType Directory -Force -Path $Data | Out-Null

$UserEnvironment = @(
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_LIVE_API_KEY",
    "BINANCE_LIVE_API_SECRET",
    "PREDICT_POLY_FAST_ENTRY_MODE",
    "PREDICT_POLY_FAST_BINANCE_POLL_SECONDS",
    "PREDICT_POLY_FAST_SAMPLE_SECONDS"
)
foreach ($Name in $UserEnvironment) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

if ([string]::IsNullOrWhiteSpace($env:PREDICT_POLY_FAST_ENTRY_MODE)) {
    $env:PREDICT_POLY_FAST_ENTRY_MODE = "PINNED_DIVERGENCE"
}
$EntryMode = $env:PREDICT_POLY_FAST_ENTRY_MODE.Trim().ToUpperInvariant()
if ($EntryMode -ne "PINNED_DIVERGENCE") {
    throw "Poly Fast Signal V5 supports PINNED_DIVERGENCE only"
}
$env:PREDICT_POLY_FAST_LIVE_HOST = "127.0.0.1"
$env:PREDICT_POLY_FAST_LIVE_PORT = "$Port"
$env:PREDICT_POLY_FAST_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"

function Get-ListenerPid {
    try {
        $row = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($row) { return [int]$row.OwningProcess }
    }
    catch { }
    return $null
}

function Test-FastSignal {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        return [int]$response.StatusCode -eq 200
    }
    catch { return $false }
}

function Set-FastEntryMode {
    foreach ($Asset in @("BTC", "ETH", "BNB")) {
        $Body = @{ asset = $Asset; entryStrategyMode = $EntryMode } | ConvertTo-Json -Compress
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/settings" -ContentType "application/json" -Body $Body -TimeoutSec 5 | Out-Null
    }
}

$ExistingPid = Get-ListenerPid
if ($ExistingPid) {
    $CommandLine = ""
    try { $CommandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ExistingPid").CommandLine } catch { }
    if (-not $CommandLine.ToLowerInvariant().Contains("predict_bot.poly_fast_signal_v5")) {
        throw "Port $Port is already owned by an older/different process. Stop 8792 first. PID=$ExistingPid command=$CommandLine"
    }
    if (-not (Test-FastSignal)) { throw "Poly Fast Signal owns port $Port but health is not responding." }
    Set-FastEntryMode
    Write-Host "Poly Fast Signal V5 already online on http://127.0.0.1:$Port (PID=$ExistingPid)."
    $ExistingPid | Set-Content $PidFile
    exit 0
}

$Stdout = Join-Path $Data "poly-fast-live.stdout.log"
$Stderr = Join-Path $Data "poly-fast-live.stderr.log"
$Process = Start-Process -FilePath "python" -ArgumentList @("-m", "predict_bot.poly_fast_signal_v5") -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
$Process.Id | Set-Content $PidFile

$Deadline = (Get-Date).AddSeconds(45)
do {
    if ($Process.HasExited) {
        $tail = ""
        if (Test-Path $Stderr) { $tail = (Get-Content $Stderr -Tail 50) -join [Environment]::NewLine }
        throw "Poly Fast Signal exited during startup. $tail"
    }
    if (Test-FastSignal) { break }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $Deadline)
if (-not (Test-FastSignal)) { throw "Poly Fast Signal did not become healthy on port $Port. Check $Stderr" }

Set-FastEntryMode
Write-Host "Poly Fast Signal V5 is ready: http://127.0.0.1:$Port/state"
Write-Host "8792 is signal-only. One active round per asset; same-direction repeats are ignored while OPEN."
Write-Host "A Poly direction reversal emits an exit intent to 8781; a new round is allowed only after confirmed flat."
Write-Host "Same 5m market may contain multiple sequential rounds after confirmed flat."

if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/state" }
