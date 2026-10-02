$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
$PidFile = Join-Path $Root '.hft-forward-paper-v1.pid'
$Stdout = Join-Path $Data 'hft-forward-paper-v1.stdout.log'
$Stderr = Join-Path $Data 'hft-forward-paper-v1.stderr.log'
$Port = 8788

New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Get-Json([string]$Url) {
    try {
        $raw = & curl.exe --silent --fail --connect-timeout 1 --max-time 4 $Url 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return (($raw -join "`n") | ConvertFrom-Json -ErrorAction Stop)
    } catch { return $null }
}

python -m py_compile `
    src/predict_bot/hft_forward_paper_collector_v1.py `
    tools/hftbacktest_r2_execution_school_v0.py `
    tools/hftbacktest_cap100_closed_loop_v0.py
if ($LASTEXITCODE -ne 0) { throw 'HFT Forward PAPER syntax check failed.' }

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
    if ($proc -and ([string]$proc.CommandLine).ToLowerInvariant().Contains('predict_bot.hft_forward_paper_collector_v1')) {
        Write-Host "HFT Forward PAPER already running on $Port PID=$($listener.OwningProcess)."
        exit 0
    }
    throw "Port $Port is occupied by another process PID=$($listener.OwningProcess)."
}

# PAPER ONLY. This process must never inherit live-order permission.
$env:PREDICT_LIVE_ENABLED = 'false'
$env:PREDICT_POLY_GAP_LIVE_ENABLED = 'false'
$env:HFT_FORWARD_PAPER_PORT = [string]$Port

$p = Start-Process -FilePath 'python' `
    -ArgumentList @('-m','predict_bot.hft_forward_paper_collector_v1') `
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
    if (Test-Path $Stderr) { Get-Content $Stderr -Tail 80 }
    throw 'HFT Forward PAPER did not become healthy.'
}

Write-Host 'HFT Forward PAPER V1 is running.'
Write-Host "  state:      http://127.0.0.1:$Port/health"
Write-Host '  execution:  HftBacktest + Predict Execution Tape V1 + true-match + latency/queue/partial fills'
Write-Host '  controller: frozen R2 / frozen CAP100 rerun closed-loop after each complete market tape is archived'
Write-Host '  official:   only HFT ledger counts as new PAPER performance/fill evidence'
Write-Host '  legacy:     8784/8786 QUEUECLEAR_PASS runtime remains intent/diagnostic capture only'
Write-Host '  database:   data\hft_forward_paper_v1.db'
Write-Host "  boundary:   after windowEndMs=$($health.activationAfterWindowEndMs); next complete tape onward"
Write-Host '  LIVE ORDERS: disabled'
