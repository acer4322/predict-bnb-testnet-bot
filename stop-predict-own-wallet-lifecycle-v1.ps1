$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$PidPath = Join-Path $ProjectRoot ".predict-own-wallet-lifecycle-v1.pid"

if (-not (Test-Path -LiteralPath $PidPath)) {
    Write-Host "Predict own-wallet lifecycle collector PID file is absent."
    exit 0
}

$collectorPid = 0
[void][int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$collectorPid)
if ($collectorPid -le 0) {
    throw "Invalid collector PID file: $PidPath"
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId=$collectorPid" -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Host "Collector was not running; removed stale PID file."
    exit 0
}
if ($process.CommandLine -notmatch "predict_own_wallet_lifecycle_collector_v1") {
    throw "PID $collectorPid does not belong to the Predict own-wallet lifecycle collector; refusing to stop it."
}

Stop-Process -Id $collectorPid
Remove-Item -LiteralPath $PidPath -Force
Write-Host "Stopped Predict own-wallet lifecycle collector PID $collectorPid."
