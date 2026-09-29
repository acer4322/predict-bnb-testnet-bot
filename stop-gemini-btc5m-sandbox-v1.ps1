$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidPath = Join-Path $Root '.gemini-btc5m-sandbox-v1.pid'
$ModuleNeedle = 'predict_bot.gemini_btc5m_testnet_bridge_v1'

$targets = @()
if (Test-Path $PidPath) {
    $raw = (Get-Content $PidPath -Raw).Trim()
    if ($raw -match '^[0-9]+$') { $targets += [int]$raw }
}
$listener = Get-NetTCPConnection -LocalPort 8810 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) { $targets += [int]$listener.OwningProcess }

$targets = $targets | Select-Object -Unique
foreach ($pidValue in $targets) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    $cmd = [string]$proc.CommandLine
    if (-not $cmd.ToLowerInvariant().Contains($ModuleNeedle)) {
        throw "Refusing to stop PID=$pidValue because it is not the Gemini BTC5M bridge: $cmd"
    }
    Stop-Process -Id $pidValue -ErrorAction Stop
    Write-Host "Stopped Gemini BTC5M sandbox bridge PID=$pidValue"
}
if (Test-Path $PidPath) { Remove-Item $PidPath -Force }
