$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
New-Item -ItemType Directory -Force -Path $Data | Out-Null

$Port = 8811
$ModuleNeedle = 'predict_bot.polymarket_btc5m_external_hft_collector_v1'

function Test-Service {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/state" -TimeoutSec 4
        return [bool]($r.version -eq 'POLYMARKET_BTC5M_EXTERNAL_HFT_COLLECTOR_V1')
    } catch { return $false }
}

python -m py_compile src/predict_bot/polymarket_btc5m_external_hft_collector_v1.py
if ($LASTEXITCODE -ne 0) { throw 'Polymarket external HFT collector syntax check failed.' }

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $pidValue = [int]$listener.OwningProcess
    $cmd = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue).CommandLine
    if ($cmd.ToLowerInvariant().Contains($ModuleNeedle)) {
        Write-Host "Polymarket BTC5M external-HFT collector already running on $Port PID=$pidValue."
        exit 0
    }
    throw "Port $Port occupied by another process. PID=$pidValue command=$cmd"
}

$env:POLYMARKET_BTC5M_EXTERNAL_HFT_PORT = "$Port"
if (-not $env:POLYMARKET_BTC5M_EXTERNAL_HFT_RETENTION_HOURS) { $env:POLYMARKET_BTC5M_EXTERNAL_HFT_RETENTION_HOURS = '3' }
$stdout = Join-Path $Data 'polymarket-btc5m-external-hft-v1.stdout.log'
$stderr = Join-Path $Data 'polymarket-btc5m-external-hft-v1.stderr.log'
$p = Start-Process -FilePath 'python' -ArgumentList @('-m','predict_bot.polymarket_btc5m_external_hft_collector_v1') -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$p.Id | Set-Content (Join-Path $Root '.polymarket-btc5m-external-hft-v1.pid')

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    if (Test-Service) { break }
} while ((Get-Date) -lt $deadline)

if (-not (Test-Service)) {
    if (Test-Path $stderr) { Get-Content $stderr -Tail 80 }
    throw 'Polymarket BTC5M external-HFT collector did not become ready.'
}

$state = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/state" -TimeoutSec 5
Write-Host 'Polymarket BTC5M External-HFT V1 is running.'
Write-Host "  state:      http://127.0.0.1:$Port/state"
Write-Host "  market:     $($state.market.eventSlug)"
Write-Host "  status:     $($state.status)"
Write-Host "  websocket:  $($state.wsStatus)"
Write-Host "  retention:  $($state.retentionHours)h"
Write-Host '  orders:     disabled / read-only'
Write-Host '  purpose:    real Polymarket BTC5M L2 + trade path for external HFT replay'