param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$Port = 8781
$Base = "http://127.0.0.1:$Port"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Import-PersistentEnvironment([string]$Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if ([string]::IsNullOrWhiteSpace($Value)) { $Value = [Environment]::GetEnvironmentVariable($Name, "Machine") }
    if (-not [string]::IsNullOrWhiteSpace($Value)) { Set-Item -Path "Env:$Name" -Value $Value }
}
@(
    "PREDICT_FUN_API_KEY","PREDICT_FUN_PRIVATE_KEY","PREDICT_FUN_PRIVY_PRIVATE_KEY","PREDICT_FUN_ACCOUNT_ADDRESS","PREDICT_FUN_JWT",
    "BINANCE_API_KEY","BINANCE_API_SECRET","PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE","PREDICT_TARGET_TAKER_BINANCE_SYMBOL",
    "PREDICT_TARGET_TAKER_BINANCE_BSC_RPC_URL","PREDICT_TARGET_TAKER_BINANCE_USDT_ADDRESS",
    "PREDICT_ECHTGELD_BINANCE_BALANCE_ACCOUNT_TYPE","PREDICT_ECHTGELD_SETTLEMENT_DB","PREDICT_ECHTGELD_AUTO_REDEEM"
) | ForEach-Object { Import-PersistentEnvironment $_ }

$env:PREDICT_ECHTGELD_ENGINE_HOST = "127.0.0.1"
$env:PREDICT_ECHTGELD_ENGINE_PORT = "$Port"

function Get-ListenerPid {
    try {
        $row = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($row) { return [int]$row.OwningProcess }
    } catch { }
    return $null
}
function Get-Json([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 4 } catch { return $null }
}
function Test-Health {
    $h = Get-Json "$Base/health"
    return $null -ne $h -and [bool]$h.ok
}

$ListenerPid = Get-ListenerPid
if ($ListenerPid) {
    $Command = ""
    try { $Command = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid").CommandLine } catch { }
    if (-not $Command.ToLowerInvariant().Contains("predict_bot.echtgeld_engine")) {
        throw "Port $Port is occupied by an unrecognized process. PID=$ListenerPid command=$Command"
    }
    $Health = Get-Json "$Base/health"
    if ($Health -and [bool]$Health.armed) {
        throw "8781 is LIVE ARMED. Pause Echtgeld before migrating/restarting the engine. PID=$ListenerPid version=$($Health.version)"
    }
    Write-Host "Replacing PAUSED/old Echtgeld engine PID=$ListenerPid with Poly multi-strategy V10."
    & taskkill.exe /PID $ListenerPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop old Echtgeld engine PID=$ListenerPid" }
    Start-Sleep -Milliseconds 500
}

$Stdout = Join-Path $Data "echtgeld-engine-v2.stdout.log"
$Stderr = Join-Path $Data "echtgeld-engine-v2.stderr.log"
$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.echtgeld_engine_v10") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
$Process.Id | Set-Content (Join-Path $Root ".echtgeld-engine-v2.pid")

$Deadline = (Get-Date).AddSeconds(45)
do {
    if ($Process.HasExited) {
        $tail = ""
        if (Test-Path $Stderr) { $tail = (Get-Content $Stderr -Tail 60) -join [Environment]::NewLine }
        throw "Echtgeld Engine exited during startup. $tail"
    }
    if (Test-Health) { break }
    Start-Sleep -Milliseconds 350
} while ((Get-Date) -lt $Deadline)
if (-not (Test-Health)) { throw "Echtgeld Engine did not become healthy on $Base. Check $Stderr" }

$Health = Get-Json "$Base/health"
if (-not ([string]$Health.version).Contains("ECHTGELD_ENGINE_V2")) { throw "Unexpected Echtgeld version: $($Health.version)" }
if (-not [bool]$Health.polyFastGatewayEnabled) { throw "Poly Fast gateway is not enabled." }
if (-not [bool]$Health.polyFastRoundLifecycle) { throw "Poly Fast round lifecycle is not enabled." }
if (-not [bool]$Health.polyAwareRedeem) { throw "Poly-aware 4310 redeem is not enabled." }
if (-not [bool]$Health.polyPnlInStopLoss) { throw "Poly realized PnL is not included in stop loss." }
if (-not [bool]$Health.polyClaimSettlementRepair) { throw "Poly claim settlement repair is not enabled." }
if (-not [bool]$Health.claimSettlementClosesActiveRound) { throw "Claim settlement does not close active Poly rounds." }
if (-not [bool]$Health.polyGapStrategyAccepted) { throw "8781 does not accept R_POLY_GAP_SCALP_LIVE." }
if (-not [bool]$Health.polyPinnedStrategyAccepted) { throw "8781 does not accept experimental PINNED strategy." }
if ([bool]$Health.armed) { throw "New Echtgeld engine unexpectedly started ARMED." }

Write-Host "Echtgeld Engine V10 is ready and PAUSED: $Base/state"
Write-Host "  Poly entry : POST $Base/poly-intent (GAP + PINNED)"
Write-Host "  Poly exit  : POST $Base/poly-exit-intent"
Write-Host "  Lifecycle  : GET  $Base/poly-lifecycle"
Write-Host "  Redeem     : Binance PENDING_CLAIM/canClaim payout is settlement evidence even if redeem tx becomes ambiguous"
Write-Host "  PnL/Risk   : settled Poly payout closes the round and is merged into the existing stop-loss basis"
Write-Host "  Safety     : 8781 remains the only venue owner; startup is always PAUSED; ambiguous BUY/SELL/redeem is never blindly retried"

if (-not $NoBrowser) { Write-Host "Dashboard control page: http://127.0.0.1:4320/echtgeld.html" }
