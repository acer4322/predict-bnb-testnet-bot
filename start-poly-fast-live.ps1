param(
    [switch]$NoBrowser,
    [ValidateSet("POLY_GAP", "PINNED_DIVERGENCE")]
    [string]$EntryMode = "POLY_GAP"
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
    "PREDICT_POLY_FAST_BINANCE_POLL_SECONDS",
    "PREDICT_POLY_FAST_SAMPLE_SECONDS",
    "PREDICT_POLY_GAP_ENTRY_DELAY_SECONDS",
    "PREDICT_POLY_GAP_LIVE_MAX_ENTRY_PRICE"
)
foreach ($Name in $UserEnvironment) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
}

$EntryMode = $EntryMode.Trim().ToUpperInvariant()
$env:PREDICT_POLY_FAST_ENTRY_MODE = $EntryMode
$env:PREDICT_POLY_FAST_LIVE_HOST = "127.0.0.1"
$env:PREDICT_POLY_FAST_LIVE_PORT = "$Port"
# 8792 is always signal-only; these switches prevent inherited venue writers.
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
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        return $null -ne $response -and [bool]$response.ok
    }
    catch { return $false }
}

$ExistingPid = Get-ListenerPid
if ($ExistingPid) {
    $CommandLine = ""
    try { $CommandLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ExistingPid").CommandLine } catch { }
    if (-not $CommandLine.ToLowerInvariant().Contains("predict_bot.poly_fast_signal")) {
        throw "Port $Port is already owned by a different process. Stop 8792 first. PID=$ExistingPid command=$CommandLine"
    }
    Write-Host "Replacing old Poly Fast Signal PID=$ExistingPid with V6 entry=$EntryMode."
    & taskkill.exe /PID $ExistingPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop old Poly Fast Signal PID=$ExistingPid" }
    Start-Sleep -Milliseconds 400
}

$Stdout = Join-Path $Data "poly-fast-live.stdout.log"
$Stderr = Join-Path $Data "poly-fast-live.stderr.log"
$Process = Start-Process -FilePath "python" -ArgumentList @("-m", "predict_bot.poly_fast_signal_v6") -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
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

$State = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/state" -TimeoutSec 4
if (-not ([string]$State.state.version).Contains("POLY_FAST_SIGNAL_V6")) {
    throw "Unexpected Poly Fast version: $($State.state.version)"
}
Write-Host "Poly Fast Signal V6 is ready: http://127.0.0.1:$Port/state"
Write-Host "  Entry mode : $EntryMode"
Write-Host "  Default    : POLY_GAP = R_POLY_GAP_SCALP_LIVE (high-frequency)"
Write-Host "  Optional   : PINNED_DIVERGENCE (experimental rare-event mode; use -EntryMode PINNED_DIVERGENCE)"
Write-Host "  Lifecycle  : one active round per asset; same-direction repeats ignored while OPEN"
Write-Host "  Exit       : immediate Poly direction reversal -> 8781 SELL; rearm only after confirmed flat"
Write-Host "  Execution  : 8792 is signal-only; 8781 remains the only Echtgeld venue owner"

if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/state" }
