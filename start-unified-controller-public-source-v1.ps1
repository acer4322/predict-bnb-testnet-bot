param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
$PidFile = Join-Path $Root '.unified-controller-public-source-v1.pid'
$Stdout = Join-Path $Data 'unified-controller-public-source-v1.stdout.log'
$Stderr = Join-Path $Data 'unified-controller-public-source-v1.stderr.log'
$Port = 8783

New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Get-Json([string]$Url) {
    try {
        $raw = & curl.exe --silent --fail --connect-timeout 1 --max-time 4 $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return (($raw -join "`n") | ConvertFrom-Json -ErrorAction Stop)
    } catch { return $null }
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if ($proc -and ([string]$proc.CommandLine).ToLowerInvariant().Contains('predict_bot.unified_controller_public_source_v1')) {
        Write-Host "Unified paper public source already running on $Port PID=$($listener.OwningProcess)."
        exit 0
    }
    throw "Port $Port is occupied by another process PID=$($listener.OwningProcess)."
}

# This helper is intentionally paper-only and has no Echtgeld handoff code.
$env:PREDICT_LIVE_ENABLED = 'false'
$env:PREDICT_POLY_GAP_LIVE_ENABLED = 'false'
$env:PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_PORT = [string]$Port
$env:PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_DB = (Join-Path $Data 'unified_controller_public_source_v1.db')
$env:PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_INTERVAL_MS = '250'

$p = Start-Process -FilePath 'python' `
    -ArgumentList @('-m','predict_bot.unified_controller_public_source_v1') `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
$p.Id | Set-Content $PidFile

$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Milliseconds 500
    $health = Get-Json "http://127.0.0.1:$Port/health"
    if ($health -and [bool]$health.processHealthy -and [bool]$health.strategyInputReady) { break }
} while ((Get-Date) -lt $deadline)

if (-not $health -or -not [bool]$health.processHealthy -or -not [bool]$health.strategyInputReady) {
    if (Test-Path $Stderr) { Get-Content $Stderr -Tail 50 }
    throw 'Unified paper public source did not become ready.'
}

Write-Host 'Unified Controller paper-only public source is running.'
Write-Host "  state:  http://127.0.0.1:$Port/state"
Write-Host '  module: predict_bot.unified_controller_public_source_v1'
Write-Host '  use:    latestPublicSnapshot only for 8784 research'
Write-Host '  Echtgeld handoff: NONE'
Write-Host '  LIVE ORDERS: disabled'
