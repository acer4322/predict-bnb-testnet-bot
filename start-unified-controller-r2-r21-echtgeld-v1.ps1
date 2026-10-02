param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$Port = 8789
$Base = "http://127.0.0.1:$Port"
$ExpectedVersion = "UNIFIED_R2_R21_V353_MAKER10_DUST_FIX_TAKER_SIZING_PENDING_V1"
$ExpectedSource = "R2_R21_8789"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Get-Json([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 5 } catch { return $null }
}

function Get-ListenerPid {
    try {
        $row = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($row) { return [int]$row.OwningProcess }
    } catch { }
    return $null
}

$EngineHealth = Get-Json "http://127.0.0.1:8781/health"
$EngineState = Get-Json "http://127.0.0.1:8781/cap100/state"
if ($null -eq $EngineHealth -or -not [bool]$EngineHealth.cap100ExecutionAdapter) {
    throw "8781 must expose the CAP100 Echtgeld adapter before R2+R2.1 can start."
}
if ($null -eq $EngineState -or $null -eq $EngineState.cap100) {
    throw "8781 /cap100/state is unavailable."
}
$Allowed = @($EngineState.cap100.allowedSources | ForEach-Object { [string]$_ })
if ($Allowed -notcontains $ExpectedSource) {
    throw "8781 runtime has not loaded $ExpectedSource. Restart 8781 with the current source before starting 8789."
}

$ListenerPid = Get-ListenerPid
if ($ListenerPid) {
    $Command = ""
    try { $Command = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid").CommandLine } catch { }
    if (-not $Command.ToLowerInvariant().Contains("unified_controller_r2_r21_echtgeld_v1")) {
        throw "Port $Port is occupied by an unrecognized process. PID=$ListenerPid command=$Command"
    }
    if (-not $Restart) {
        $ExistingHealth = Get-Json "$Base/health"
        if ($ExistingHealth -and [string]$ExistingHealth.version -eq $ExpectedVersion) {
            Write-Host "R2+R2.1 Echtgeld controller is already running on $Base/state (PID $ListenerPid)."
            Write-Host "  Current ready : $($ExistingHealth.deploymentLiveReady)"
            Write-Host "  Current block : $($ExistingHealth.blockReason)"
            return
        }
        throw "8789 is running an old or unhealthy R2+R2.1 version. Re-run with -Restart after reviewing its state."
    }
    Stop-Process -Id $ListenerPid -Force
    Start-Sleep -Milliseconds 500
}

$env:PYTHONPATH = "src"
$env:UNIFIED_CONTROLLER_CAP100_LIVE_HOST = "127.0.0.1"
$env:UNIFIED_CONTROLLER_R2_R21_LIVE_PORT = "$Port"
$env:UNIFIED_CONTROLLER_CAP100_ENGINE_URL = "http://127.0.0.1:8781"
$Stdout = Join-Path $Data "unified-controller-r2-r21-echtgeld-v1.stdout.log"
$Stderr = Join-Path $Data "unified-controller-r2-r21-echtgeld-v1.stderr.log"
$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.unified_controller_r2_r21_echtgeld_v1") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
$Process.Id | Set-Content (Join-Path $Root ".unified-controller-r2-r21-echtgeld-v1.pid")

$Deadline = (Get-Date).AddSeconds(60)
do {
    if ($Process.HasExited) {
        $Tail = ""
        if (Test-Path $Stderr) { $Tail = (Get-Content $Stderr -Tail 80) -join [Environment]::NewLine }
        throw "R2+R2.1 8789 exited during startup. $Tail"
    }
    $Health = Get-Json "$Base/health"
    if ($Health -and [string]$Health.version -eq $ExpectedVersion -and [string]$Health.blockReason -ne "ENGINE_NOT_CHECKED") { break }
    Start-Sleep -Milliseconds 350
} while ((Get-Date) -lt $Deadline)

$Health = Get-Json "$Base/health"
$State = Get-Json "$Base/state"
if ($null -eq $Health -or [string]$Health.version -ne $ExpectedVersion) {
    throw "R2+R2.1 8789 did not expose the expected version. Check $Stderr"
}
if ([bool]$Health.paperOnly -or -not [bool]$Health.liveOrdersAffected) {
    throw "8789 runtime mode assertion failed: paperOnly=$($Health.paperOnly) liveOrdersAffected=$($Health.liveOrdersAffected)"
}
if ([string]$State.entrySource -ne $ExpectedSource) {
    throw "8789 entrySource mismatch: $($State.entrySource)"
}
if ([string]$State.liveExecution.runtimeHardeningVersion -ne "R2_R21_HBI1_GHOST1") {
    throw "8789 runtime hardening mismatch: $($State.liveExecution.runtimeHardeningVersion)"
}
if (-not [bool]$State.liveExecution.heartbeatIsolated -or -not [bool]$State.liveExecution.heartbeatUsesDedicatedHttpClient -or -not [bool]$State.liveExecution.engineMonitorSeparated) {
    throw "8789 heartbeat isolation hardening is not active."
}
if (-not [bool]$State.liveExecution.safety.heartbeatIndependentOfControllerLock) {
    throw "8789 heartbeat still depends on controller lock."
}
if (-not [bool]$State.liveExecution.safety.preVenuePlannedGhostRetirement) {
    throw "8789 pre-venue PLANNED ghost retirement is not active."
}
if ([double]$State.r2r21Bundle.configuredShares -ne 10.0) {
    throw "8789 configuredShares mismatch: $($State.r2r21Bundle.configuredShares)"
}
if ([bool]$State.r2r21Bundle.r21ActionAuthority) {
    throw "R2.1 unexpectedly has action authority."
}
if ([string]$State.r2r21Bundle.semanticAcceptance -ne "PASS_V33_L1_L11_AND_RANDOMIZED_CHAOS_INFORMATION_ONLY_PLUS_V3_MULTICHILD_RANKER_SHADOW") {
    throw "8789 semantic cooperation artifact mismatch."
}
if (-not [bool]$State.liveExecution.safety.takerConfirmTimeoutCancelOwnedBy8781) {
    throw "8789 does not delegate the fixed Taker confirmation timeout to durable venue owner 8781."
}
if (-not [bool]$State.liveExecution.safety.takerTimeoutCancelWaitsForTerminalAck) {
    throw "8789 Taker timeout path does not retain ownership until terminal ACK."
}
if (-not [bool]$State.liveExecution.safety.lifecyclePollingIndependentOfMarketSnapshots) {
    throw "8789 lifecycle delivery still depends on public strategy snapshots."
}

Write-Host "R2+R2.1 Echtgeld controller is listening on $Base/state"
Write-Host "  Decision owner : Frozen R2 on 8789"
Write-Host "  Incident layer : R2.1 information-only + V3 ranking; R2 owns autonomous KEEP/REASSESS; 8781 owns cancel execution"
Write-Host "  Venue owner    : 8781 only"
Write-Host "  Entry source   : $ExpectedSource"
Write-Host "  Quantity       : exactly 10 shares; no strategy notional cap; maker/taker minimum notional enforced by 8781"
Write-Host "  Taker lifecycle: 8781 cancels unfinished remainder after 2200ms; 8789 keeps polling terminal events even after strategy snapshots stop"
Write-Host "  Runtime guard  : HBI1 isolated heartbeat + GHOST1 pre-venue child retirement"
Write-Host "  Activation     : operator must select $ExpectedSource and RESUME 8781; next complete market only"
Write-Host "  Current ready  : $($Health.deploymentLiveReady)"
Write-Host "  Current block  : $($Health.blockReason)"
