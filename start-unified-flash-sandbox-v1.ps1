$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root 'data'
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Test-Service([string]$Url) {
    try {
        $r = Invoke-RestMethod -Uri $Url -TimeoutSec 3
        return [bool]$r.ok -or ([string]$r.status -ne '')
    } catch { return $false }
}

if (-not (Test-Service 'http://127.0.0.1:8784/health')) {
    throw 'Unified base 8784 must be running first. Run .\start-unified-controller-paper-v1.ps1'
}
if (-not (Test-Service 'http://127.0.0.1:8783/health')) {
    throw 'Paper-only public feature source 8783 must be healthy first.'
}

python -m py_compile src/predict_bot/unified_controller_flash_sandbox_v1.py src/predict_bot/strategy_target_compare_recorder_v1.py
if ($LASTEXITCODE -ne 0) { throw 'Flash sandbox syntax check failed.' }

$existing = Get-NetTCPConnection -LocalPort 8785 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    $pidValue = [int]$existing.OwningProcess
    $cmd = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue).CommandLine
    if ($cmd.ToLowerInvariant().Contains('predict_bot.unified_controller_flash_sandbox_v1')) {
        Write-Host "Flash Sandbox V1 already running on 8785. PID=$pidValue"
        exit 0
    }
    throw "Port 8785 occupied by another process. PID=$pidValue command=$cmd"
}

$env:UNIFIED_FLASH_PORT = '8785'
$env:UNIFIED_FLASH_PUBLIC_SOURCE_URL = 'http://127.0.0.1:8783/state'
$env:UNIFIED_FLASH_BASE_SOURCE_URL = 'http://127.0.0.1:8784/state'
$env:UNIFIED_FLASH_REGISTRY = (Join-Path $Root 'data\research\flash_sandbox_registry_v1.json')
$env:UNIFIED_FLASH_DB = (Join-Path $Root 'data\strategy_target_flash_v1.db')

$p = Start-Process -FilePath 'python' `
    -ArgumentList @('-m','predict_bot.unified_controller_flash_sandbox_v1') `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Data 'unified-flash-v1.stdout.log') `
    -RedirectStandardError (Join-Path $Data 'unified-flash-v1.stderr.log') -PassThru
$p.Id | Set-Content (Join-Path $Root '.unified-flash-v1.pid')

$deadline = (Get-Date).AddSeconds(30)
do {
    Start-Sleep -Milliseconds 500
    if (Test-Service 'http://127.0.0.1:8785/health') { break }
} while ((Get-Date) -lt $deadline)
if (-not (Test-Service 'http://127.0.0.1:8785/health')) {
    if (Test-Path (Join-Path $Data 'unified-flash-v1.stderr.log')) {
        Get-Content (Join-Path $Data 'unified-flash-v1.stderr.log') -Tail 50
    }
    throw 'Flash Sandbox V1 did not become healthy on 8785.'
}

Write-Host 'Unified Flash Sandbox V1 is running.'
Write-Host '  base:     http://127.0.0.1:8784/state'
Write-Host '  sandbox:  http://127.0.0.1:8785/state'
Write-Host '  registry: data\research\flash_sandbox_registry_v1.json'
Write-Host '  db:       data\strategy_target_flash_v1.db'
Write-Host '  changes activate only at the next complete market rollover'
Write-Host '  LIVE ORDERS: disabled'
