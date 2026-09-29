$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
New-Item -ItemType Directory -Force -Path $Data | Out-Null

$Port = 8810
$ModuleNeedle = 'predict_bot.gemini_btc5m_testnet_bridge_v1'

# Refresh Gemini Sandbox credentials from the Windows User environment because
# long-running project services do not inherit variables added after startup.
$UserGeminiKey = [Environment]::GetEnvironmentVariable('gemini_sandbox_APIKEY', 'User')
$UserGeminiSecret = [Environment]::GetEnvironmentVariable('gemini_sandbox_APISecret', 'User')
if ($UserGeminiKey) {
    $env:gemini_sandbox_APIKEY = $UserGeminiKey
    $env:GEMINI_PM_SANDBOX_API_KEY = $UserGeminiKey
}
if ($UserGeminiSecret) {
    $env:gemini_sandbox_APISecret = $UserGeminiSecret
    $env:GEMINI_PM_SANDBOX_API_SECRET = $UserGeminiSecret
}

# Prefer the project target-blind live feature source so frozen models can
# receive Chainlink + Binance order-flow features. Failure is non-fatal because
# the bridge can still fall back to direct Binance public bookTicker data.
$FeatureSourceScript = Join-Path $Root 'start-gemini-public-feature-source-v1.ps1'
if (Test-Path $FeatureSourceScript) {
    try { & $FeatureSourceScript }
    catch { Write-Warning "Gemini public feature source unavailable; continuing with bridge fallback. $($_.Exception.Message)" }
}
if (-not $env:GEMINI_BTC5M_BASELINE_SOURCE_URL) {
    $env:GEMINI_BTC5M_BASELINE_SOURCE_URL = 'http://127.0.0.1:8783/current-public'
}

function Test-Bridge {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
        return [bool]$r.processHealthy
    } catch {
        return $false
    }
}

python -m py_compile src/predict_bot/gemini_prediction_sandbox_v1.py src/predict_bot/gemini_btc5m_testnet_bridge_v1.py
if ($LASTEXITCODE -ne 0) { throw 'Gemini BTC5M sandbox syntax check failed.' }

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    $pidValue = [int]$existing.OwningProcess
    $cmd = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue).CommandLine
    if ($cmd.ToLowerInvariant().Contains($ModuleNeedle)) {
        Write-Host "Gemini BTC5M sandbox bridge already running on $Port. PID=$pidValue"
        exit 0
    }
    throw "Port $Port occupied by another process. PID=$pidValue command=$cmd"
}

if (-not $env:GEMINI_PM_SANDBOX_ORDER_ENABLED) {
    $env:GEMINI_PM_SANDBOX_ORDER_ENABLED = 'false'
}
$env:GEMINI_BTC5M_BRIDGE_PORT = "$Port"
if (-not $env:GEMINI_BTC5M_RETENTION_HOURS) {
    $env:GEMINI_BTC5M_RETENTION_HOURS = '72'
}

$stdout = Join-Path $Data 'gemini-btc5m-sandbox-v1.stdout.log'
$stderr = Join-Path $Data 'gemini-btc5m-sandbox-v1.stderr.log'
$p = Start-Process -FilePath 'python' -ArgumentList @('-m','predict_bot.gemini_btc5m_testnet_bridge_v1') -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$p.Id | Set-Content (Join-Path $Root '.gemini-btc5m-sandbox-v1.pid')

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    if (Test-Bridge) { break }
} while ((Get-Date) -lt $deadline)

if (-not (Test-Bridge)) {
    if (Test-Path $stderr) { Get-Content $stderr -Tail 80 }
    throw 'Gemini BTC5M sandbox bridge did not become healthy.'
}

$state = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/state" -TimeoutSec 5
Write-Host 'Gemini BTC5M Prediction Sandbox V1 is running.'
Write-Host "  state:    http://127.0.0.1:$Port/state"
Write-Host "  health:   http://127.0.0.1:$Port/health"
Write-Host "  rules:    http://127.0.0.1:$Port/rules"
Write-Host "  db:       data\gemini_btc5m_sandbox_v1.db"
Write-Host "  market:   $($state.currentMarket.ticker) / $($state.currentMarket.instrumentSymbol)"
Write-Host "  status:   $($state.status)"
Write-Host "  baseline: $($state.baselineReady)"
Write-Host "  ORDERS:   $($state.sandboxOrdersEnabled) (sandbox only; production host is rejected in code)"
