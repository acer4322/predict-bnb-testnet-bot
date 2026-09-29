$ErrorActionPreference = 'Stop'
$Port = 8811
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listener) {
    Write-Host 'Polymarket BTC5M external-HFT collector is not running.'
    exit 0
}
$pidValue = [int]$listener.OwningProcess
$proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
$cmd = [string]$proc.CommandLine
if (-not $cmd.ToLowerInvariant().Contains('predict_bot.polymarket_btc5m_external_hft_collector_v1')) {
    throw "Refusing to stop unexpected process on port $Port. PID=$pidValue command=$cmd"
}
Stop-Process -Id $pidValue -Force
Write-Host "Stopped Polymarket BTC5M external-HFT collector PID=$pidValue."