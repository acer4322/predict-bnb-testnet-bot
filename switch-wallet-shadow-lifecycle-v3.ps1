param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Get-ListeningProcessId([int]$Port) {
    try {
        $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($Connection) { return [int]$Connection.OwningProcess }
    }
    catch { }
    return $null
}

function Get-ProcessCommandLine([int]$ProcessId) {
    try { return [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop).CommandLine }
    catch { return "" }
}

function Test-Health {
    try {
        $Body = & curl.exe --silent --fail --connect-timeout 1 --max-time 3 "http://127.0.0.1:8776/health" 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        $State = (($Body -join "`n") | ConvertFrom-Json -ErrorAction Stop)
        if ($State.state) { $State = $State.state }
        return ([string]$State.version).StartsWith("PREDICT_WALLET_SHADOW_V0_22_EVENT_DRIVEN_MAKER_LIFECYCLE_V3")
    }
    catch { return $false }
}

# This switch changes only the paper-only 8776 observer. It refuses to kill an
# unknown process and does not touch live executors or the 8771/8777/8778 feeds.
$ListenerPid = Get-ListeningProcessId 8776
if ($ListenerPid) {
    $Command = Get-ProcessCommandLine $ListenerPid
    if ($Command.ToLowerInvariant().Contains("predict_bot.predict_wallet_shadow_observer_v4_17")) {
        Write-Host "Wallet Shadow lifecycle V3 observer is already running on 8776."
        if (-not $NoBrowser) { Start-Process "http://localhost:4320/wallet-shadow" }
        exit 0
    }
    if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_wallet_shadow_observer_v4_16")) {
        throw "Port 8776 is occupied by an unrecognized process. Refusing to stop it. PID=$ListenerPid command=$Command"
    }
    Write-Host "Stopping verified paper-only V4.16 observer on 8776 (PID=$ListenerPid)."
    & taskkill.exe /PID $ListenerPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop V4.16 observer PID=$ListenerPid" }
    Start-Sleep -Milliseconds 500
}

$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"

Write-Host "Starting event-driven Maker lifecycle V3 paper observer on 8776."
$Shadow = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_17") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Data "wallet-shadow-lab-observer.stdout.log") `
    -RedirectStandardError (Join-Path $Data "wallet-shadow-lab-observer.stderr.log") -PassThru
$Shadow.Id | Set-Content (Join-Path $Root ".wallet-shadow-lab-observer.pid")

$Deadline = (Get-Date).AddSeconds(30)
do {
    if (Test-Health) {
        Write-Host "8776 lifecycle V3 ready. Existing Maker Rules V2 remains the control cohort; live orders are unaffected."
        if (-not $NoBrowser) { Start-Process "http://localhost:4320/wallet-shadow" }
        exit 0
    }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $Deadline)

throw "Lifecycle V3 observer did not become healthy within 30 seconds. Check data/wallet-shadow-lab-observer.stderr.log"
