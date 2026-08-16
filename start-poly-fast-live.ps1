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
    "PREDICT_POLY_GAP_LIVE_MAX_ENTRY_PRICE",
    "PREDICT_POLY_GAP_UP_THRESHOLD",
    "PREDICT_POLY_GAP_DOWN_THRESHOLD"
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
    Write-Host "Replacing old Poly Fast Signal PID=$ExistingPid with V16 Binance-book self-heal entry=$EntryMode."
    & taskkill.exe /PID $ExistingPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop old Poly Fast Signal PID=$ExistingPid" }
    Start-Sleep -Milliseconds 400
}

$Stdout = Join-Path $Data "poly-fast-live.stdout.log"
$Stderr = Join-Path $Data "poly-fast-live.stderr.log"
$Process = Start-Process -FilePath "python" -ArgumentList @("-m", "predict_bot.poly_fast_signal_v16") -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
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
if (-not ([string]$State.state.version).Contains("POLY_FAST_SIGNAL_V16")) {
    throw "Unexpected Poly Fast version: $($State.state.version)"
}
Write-Host "Poly Fast Signal V16 is ready: http://127.0.0.1:$Port/state"
Write-Host "  Entry mode : $EntryMode"
Write-Host "  Entry cap  : Binance executable Ask > 0.90 is blocked; exactly 0.90 remains allowed"
Write-Host "  Take profit: Binance executable same-side Bid >= 0.95 sends a risk-reducing exit"
Write-Host "  Reversal   : original immediate Poly-direction reversal exit remains enabled"
Write-Host "  Entry assets: BTC, ETH"
Write-Host "  BNB        : NEW ENTRY BLOCKED; observer/lifecycle remains active so an existing BNB round can still exit"
Write-Host "  Safe retry : only REJECTED with no venue-write evidence; requires >=1s cooldown + newer Binance book"
Write-Host "  Never retry: AMBIGUOUS/SUBMITTED/vendorOrderId/shares/submitted amount"
Write-Host "  Diagnostic : Binance receipt/source/RTT freshness + missing Ask sides + persistent WAITING_BOOK detail revalidation"
Write-Host "  Rollover   : only exact active Binance 5m market may enter cache; future market is never cached"
Write-Host "  Book heal  : WAITING_BOOK >=1.5s revalidates current topic token ids; two-sided Ask admission remains fail-closed"
Write-Host "  Poly state : evaluator reads the same in-process 8792 Poly observer shown by diagnostics; legacy 8767 state is bypassed"
Write-Host "  Lifecycle  : one active round per asset; same-direction repeats ignored while OPEN"
Write-Host "  Alignment  : current 5m bucket == Poly bucket == Binance start/end; fail closed on rollover"
Write-Host "  Execution  : 8792 is signal-only; 8781 remains the only Echtgeld venue owner"

if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/state" }
