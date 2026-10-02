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

function Assert-KnownListener([int]$Port, [string]$Needle, [string]$Label) {
    $ListenerPid = Get-ListeningProcessId $Port
    if (-not $ListenerPid) { return }
    $Command = Get-ProcessCommandLine $ListenerPid
    if (-not $Command.ToLowerInvariant().Contains($Needle.ToLowerInvariant())) {
        throw "Port $Port is occupied by an unrecognized process. $Label was not started. PID=$ListenerPid command=$Command"
    }
}

function Wait-LocalService([string]$Name, [string]$Url, [int]$Seconds, [string]$ErrorLog) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    do {
        if (Test-LocalService $Url 3) { return }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $Deadline)

    if (Test-Path $ErrorLog) {
        Write-Warning "$Name failed to become healthy. Last stderr lines:"
        Get-Content $ErrorLog -Tail 30 | ForEach-Object { Write-Warning $_ }
    }
    throw "$Name did not become healthy at $Url. Check $ErrorLog."
}

# This script is deliberately standalone. ETH Taker research only needs
# 8771 (multi-asset Predict.fun state), 8779 (ETH target wallet + full book),
# and 8780 (ETH public microstructure). BTC 8778/8777/8776 are not required.
$UserPredictKey = [Environment]::GetEnvironmentVariable("PREDICT_FUN_API_KEY", "User")
if ($UserPredictKey) { $env:PREDICT_FUN_API_KEY = $UserPredictKey }
if ([string]::IsNullOrWhiteSpace($env:PREDICT_FUN_API_KEY)) {
    throw "PREDICT_FUN_API_KEY is required. Configure it as a User environment variable first."
}

# Research children must never inherit an enabled live runtime.
$env:PREDICT_LIVE_ENABLED = "false"
$env:PREDICT_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_ETH_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_BNB_POLY_GAP_LIVE_ENABLED = "false"
$env:PREDICT_MICRO_RAW_RETENTION_HOURS = "6"
$env:PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS = "72"
$env:PREDICT_MICRO_SNAPSHOT_INTERVAL_MS = "250"

Assert-KnownListener 8771 "predict_bot.predict_fun_observer" "Predict.fun observer"
if (-not (Test-LocalService "http://127.0.0.1:8771/state")) {
    if (Get-ListeningProcessId 8771) {
        throw "8771 has the expected process but its /state endpoint is unhealthy. Check data/wallet-eth-taker-predict.stderr.log or the process state before retrying."
    }
    Write-Host "ETH Taker research: starting read-only Predict.fun multi-asset observer on 8771."
    $Predict = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.predict_fun_observer") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "wallet-eth-taker-predict.stdout.log") `
        -RedirectStandardError (Join-Path $Data "wallet-eth-taker-predict.stderr.log") -PassThru
    $Predict.Id | Set-Content (Join-Path $Root ".wallet-eth-taker-predict.pid")
    Wait-LocalService "8771 Predict.fun observer" "http://127.0.0.1:8771/state" 45 (Join-Path $Data "wallet-eth-taker-predict.stderr.log")
}
else {
    Write-Host "ETH Taker research: reusing 8771 Predict.fun multi-asset observer."
}

Assert-KnownListener 8779 "predict_bot.predict_wallet_maker_book_inference_collector_eth5m" "ETH 5M wallet/book collector"
if (-not (Test-LocalService "http://127.0.0.1:8779/state")) {
    if (Get-ListeningProcessId 8779) {
        throw "8779 has the expected process but its /state endpoint is unhealthy. Check data/wallet-eth-taker-target-book.stderr.log before retrying."
    }
    Write-Host "ETH Taker research: starting forward-only ETH target-wallet/full-book collector on 8779."
    $EthBook = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.predict_wallet_maker_book_inference_collector_eth5m") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "wallet-eth-taker-target-book.stdout.log") `
        -RedirectStandardError (Join-Path $Data "wallet-eth-taker-target-book.stderr.log") -PassThru
    $EthBook.Id | Set-Content (Join-Path $Root ".wallet-eth-taker-target-book.pid")
    Wait-LocalService "8779 ETH target/book collector" "http://127.0.0.1:8779/state" 45 (Join-Path $Data "wallet-eth-taker-target-book.stderr.log")
}
else {
    Write-Host "ETH Taker research: reusing 8779 ETH target-wallet/full-book collector."
}

Assert-KnownListener 8780 "predict_bot.predict_wallet_eth_taker_signal_collector" "ETH Taker public signal collector"
$ExistingPid = Get-ListeningProcessId 8780
if ($ExistingPid) {
    Write-Host "ETH Taker research: reusing 8780 collector PID=$ExistingPid."
}
else {
    Write-Host "ETH Taker research: starting isolated public ETH signal collector on 8780."
    $Process = Start-Process -FilePath "python" `
        -ArgumentList @("-m", "predict_bot.predict_wallet_eth_taker_signal_collector") `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Data "wallet-eth-taker-signal.stdout.log") `
        -RedirectStandardError (Join-Path $Data "wallet-eth-taker-signal.stderr.log") -PassThru
    $Process.Id | Set-Content (Join-Path $Root ".wallet-eth-taker-signal.pid")
}

Wait-LocalService "8780 ETH Taker signal collector" "http://127.0.0.1:8780/state" 45 (Join-Path $Data "wallet-eth-taker-signal.stderr.log")

Write-Host "ETH Taker research ready."
Write-Host "  8771 = BTC/ETH/BNB Predict.fun public state"
Write-Host "  8779 = ETH Target wallet events + public full-book recorder"
Write-Host "  8780 = ETH public spot/futures + Predict.fun signal recorder"
Write-Host "  BTC 8778/8777/8776 are intentionally not required by this standalone path."
Write-Host "No live trading runtime was enabled."

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8780/state"
}
