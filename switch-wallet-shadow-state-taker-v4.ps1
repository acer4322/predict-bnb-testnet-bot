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
        return ([string]$State.version).StartsWith("PREDICT_WALLET_SHADOW_V0_23_LIFECYCLE_V3_STATE_TAKER_V4")
    }
    catch { return $false }
}

# Research-only switch. It only replaces a verified Wallet Shadow observer on
# port 8776 and never touches the 8771/8777/8778 research feeds or live executors.
$ListenerPid = Get-ListeningProcessId 8776
if ($ListenerPid) {
    $Command = Get-ProcessCommandLine $ListenerPid
    $Lower = $Command.ToLowerInvariant()
    if ($Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_18")) {
        Write-Host "Wallet Shadow Lifecycle V3 + State Taker V4 is already running on 8776."
        if (-not $NoBrowser) { Start-Process "http://localhost:4320/wallet-shadow" }
        exit 0
    }
    if (-not ($Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_17") -or
              $Lower.Contains("predict_bot.predict_wallet_shadow_observer_v4_16"))) {
        throw "Port 8776 is occupied by an unrecognized process. Refusing to stop it. PID=$ListenerPid command=$Command"
    }
    Write-Host "Stopping verified paper-only Wallet Shadow observer on 8776 (PID=$ListenerPid)."
    & taskkill.exe /PID $ListenerPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop verified observer PID=$ListenerPid" }
    Start-Sleep -Milliseconds 500
}

$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"

Write-Host "Starting Lifecycle Maker V3 + five-second State Taker V4 paper observer on 8776."
$Shadow = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.predict_wallet_shadow_observer_v4_18") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Data "wallet-shadow-lab-observer.stdout.log") `
    -RedirectStandardError (Join-Path $Data "wallet-shadow-lab-observer.stderr.log") -PassThru
$Shadow.Id | Set-Content (Join-Path $Root ".wallet-shadow-lab-observer.pid")

$Deadline = (Get-Date).AddSeconds(30)
do {
    if (Test-Health) {
        Write-Host "8776 State Taker V4 ready. PAPER ONLY; target events do not drive decisions; live orders are unaffected."
        Write-Host "The V4 overlay excludes its deployment market and begins on the next complete market for a clean forward test."
        if (-not $NoBrowser) { Start-Process "http://localhost:4320/wallet-shadow" }
        exit 0
    }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $Deadline)

throw "State Taker V4 observer did not become healthy within 30 seconds. Check data/wallet-shadow-lab-observer.stderr.log"
