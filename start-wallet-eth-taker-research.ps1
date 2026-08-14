param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Test-LocalService([string]$Url, [int]$TimeoutSeconds = 2) {
    try {
        $Code = & curl.exe --silent --output NUL --connect-timeout 1 --max-time $TimeoutSeconds --write-out "%{http_code}" $Url 2>$null
        return $LASTEXITCODE -eq 0 -and ([string]$Code).Trim() -eq "200"
    }
    catch { return $false }
}

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

if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
    throw "8771 Predict.fun multi-asset observer is not running. Start .\start-wallet-shadow-lab.ps1 -NoBrowser first."
}
if (-not (Test-LocalService "http://127.0.0.1:8779/state")) {
    throw "8779 ETH 5M wallet/book collector is not running. Start .\start-wallet-shadow-lab.ps1 -NoBrowser first."
}

$ExistingPid = Get-ListeningProcessId 8780
if ($ExistingPid) {
    $Command = Get-ProcessCommandLine $ExistingPid
    if (-not $Command.ToLowerInvariant().Contains("predict_bot.predict_wallet_eth_taker_signal_collector")) {
        throw "Port 8780 is occupied by an unrecognized process. PID=$ExistingPid command=$Command"
    }
    Write-Host "ETH Taker research: reusing 8780 collector PID=$ExistingPid."
}
else {
    # Research child must never inherit an enabled live runtime.
    $env:PREDICT_LIVE_ENABLED = "false"
    $env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
    $env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
    $env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"
    $env:PREDICT_MICRO_RAW_RETENTION_HOURS = "6"
    $env:PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS = "72"
    $env:PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS = "72"
    $env:PREDICT_MICRO_SNAPSHOT_INTERVAL_MS = "250"

    Write-Host "ETH Taker research: starting isolated public ETH signal collector on 8780."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.predict_wallet_eth_taker_signal_collector") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "wallet-eth-taker-signal.stdout.log") `
        -RedirectStandardError (Join-Path $Data "wallet-eth-taker-signal.stderr.log") -PassThru
    $Process.Id | Set-Content (Join-Path $Root ".wallet-eth-taker-signal.pid")
}

$Deadline = (Get-Date).AddSeconds(30)
do {
    if (Test-LocalService "http://127.0.0.1:8780/state" 3) { break }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $Deadline)

if (-not (Test-LocalService "http://127.0.0.1:8780/state" 3)) {
    throw "8780 ETH Taker signal collector did not become healthy. Check data/wallet-eth-taker-signal.stderr.log."
}

Write-Host "ETH Taker research ready."
Write-Host "  8771 = BTC/ETH/BNB Predict.fun public state"
Write-Host "  8779 = ETH Target wallet events + public full-book recorder"
Write-Host "  8780 = ETH public spot/futures + Predict.fun signal recorder"
Write-Host "No live trading runtime was enabled."

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8780/state"
}
