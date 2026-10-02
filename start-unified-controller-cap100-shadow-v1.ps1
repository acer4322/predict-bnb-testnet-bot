$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
$PidFile = Join-Path $Root '.unified-controller-cap100-shadow-v1.pid'
$Stdout = Join-Path $Data 'unified-controller-cap100-shadow-v1.stdout.log'
$Stderr = Join-Path $Data 'unified-controller-cap100-shadow-v1.stderr.log'
$Port = 8786

New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Get-Json([string]$Url) {
    try {
        $raw = & curl.exe --silent --fail --connect-timeout 1 --max-time 4 $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return (($raw -join "`n") | ConvertFrom-Json -ErrorAction Stop)
    } catch { return $null }
}

$Source = Get-Json 'http://127.0.0.1:8783/health'
if (-not $Source -or -not [bool]$Source.processAlive) {
    throw '8783 paper-only public feature source is unavailable. Run start-unified-controller-public-source-v1.ps1 first.'
}
if ([bool]$Source.targetEventsUsedForDecision) {
    throw '8783 reports targetEventsUsedForDecision=true; refusing contaminated source.'
}
if (-not [bool]$Source.processHealthy -or -not [bool]$Source.strategyInputReady) {
    Write-Host "8783 is alive but public input is not ready; 8786 will start fail-closed in WAITING_SOURCE. status=$($Source.strategyStatus) missing=$([string]::Join(',', @($Source.missingFeatures)))"
}

python -m py_compile `
    src/predict_bot/strategy_target_compare_recorder_v1.py `
    src/predict_bot/unified_controller_cap100_shadow_v1.py
if ($LASTEXITCODE -ne 0) { throw 'Unified controller syntax check failed.' }

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if ($proc -and ([string]$proc.CommandLine).ToLowerInvariant().Contains('predict_bot.unified_controller_cap100_shadow_v1')) {
        Write-Host "Unified Controller CAP100 Shadow V1 already running on $Port PID=$($listener.OwningProcess)."
        exit 0
    }
    throw "Port $Port is occupied by another process PID=$($listener.OwningProcess)."
}

$env:PREDICT_LIVE_ENABLED = 'false'
$env:PREDICT_POLY_GAP_LIVE_ENABLED = 'false'
$env:UNIFIED_CONTROLLER_CAP100_PORT = [string]$Port
$env:UNIFIED_CONTROLLER_PUBLIC_SOURCE_URL = 'http://127.0.0.1:8783/state'

$p = Start-Process -FilePath 'python' `
    -ArgumentList @('-m','predict_bot.unified_controller_cap100_shadow_v1') `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
$p.Id | Set-Content $PidFile

$deadline = (Get-Date).AddSeconds(90)
do {
    Start-Sleep -Milliseconds 500
    $health = Get-Json "http://127.0.0.1:$Port/health"
    if ($health -and [bool]$health.ok) { break }
} while ((Get-Date) -lt $deadline)

if (-not $health) {
    if (Test-Path $Stderr) { Get-Content $Stderr -Tail 50 }
    throw 'Unified Controller CAP100 Shadow V1 did not become healthy.'
}

Write-Host 'Unified Controller CAP100 Shadow V1 is running.'
Write-Host "  state:    http://127.0.0.1:$Port/state"
Write-Host '  source:   8783 public-only latestPublicSnapshot (no 8776/Target/Echtgeld path)'
Write-Host '  candidate: PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18 (fresh safety shadow)'
Write-Host '  maker:     same R2 Maker stack, fixed 18 shares; spent + resting commitment capped at $80'
Write-Host '  runtime:   QUEUECLEAR_PASS remains legacy intent/diagnostic state only'
Write-Host '  official:  post-close HftBacktest + Predict Execution Tape V1 closed-loop via 8788'
Write-Host '  repair:    same R2 overlap/residual arbitration; $20 reserve remains for active intervention'
Write-Host '  capital:   nominal $100 / Maker $80 / active reserve $20; known Maker-after-Taker hard-cap gap remains a safety blocker'
Write-Host '  recorder: data\strategy_target_compare_v1.db'
Write-Host "  boundary: current startup market $($health.excludedDeploymentMarketId) excluded; begins next complete market"
Write-Host '  LIVE ORDERS: disabled'

# Official forward PAPER execution is produced by the shared HFT collector.
& (Join-Path $Root 'start-hft-forward-paper-v1.ps1')

