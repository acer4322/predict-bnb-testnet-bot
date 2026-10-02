param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$Port = 8790
$Base = "http://127.0.0.1:$Port"
$ExpectedVersion = "R3S_R31_V1_1_3_WTP1_CONTINUOUS_GATE_FIX_ECHTGELD"
$ExpectedSource = "R3S_R31_8790"
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
    throw "8781 must expose the CAP100 Echtgeld adapter before R3-S+R3.1 can start."
}
if ($null -eq $EngineState -or $null -eq $EngineState.cap100) {
    throw "8781 /cap100/state is unavailable."
}
$Allowed = @($EngineState.cap100.allowedSources | ForEach-Object { [string]$_ })
if ($Allowed -notcontains $ExpectedSource) {
    throw "8781 runtime has not loaded $ExpectedSource. Restart 8781 with the current source before starting 8790."
}
if ([string]$EngineState.cap100.r3sR31StrategyVersion -ne $ExpectedVersion) {
    throw "8781/8790 R3-S version handshake mismatch: engine=$($EngineState.cap100.r3sR31StrategyVersion) controller=$ExpectedVersion"
}

$ListenerPid = Get-ListenerPid
if ($ListenerPid) {
    $Command = ""
    try { $Command = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid").CommandLine } catch { }
    if (-not $Command.ToLowerInvariant().Contains("unified_controller_r3s_r31_echtgeld_v1")) {
        throw "Port $Port is occupied by an unrecognized process. PID=$ListenerPid command=$Command"
    }
    if (-not $Restart) {
        $ExistingHealth = Get-Json "$Base/health"
        if ($ExistingHealth -and [string]$ExistingHealth.version -eq $ExpectedVersion) {
            Write-Host "R3-S+R3.1 Echtgeld controller is already running on $Base/state (PID $ListenerPid)."
            Write-Host "  Current ready : $($ExistingHealth.deploymentLiveReady)"
            Write-Host "  Current block : $($ExistingHealth.blockReason)"
            return
        }
        throw "8790 is running an old or unhealthy R3-S+R3.1 version. Re-run with -Restart after reviewing its state."
    }
    Stop-Process -Id $ListenerPid -Force
    Start-Sleep -Milliseconds 500
}

$env:PYTHONPATH = "src"
$env:UNIFIED_CONTROLLER_CAP100_LIVE_HOST = "127.0.0.1"
$env:UNIFIED_CONTROLLER_R3S_R31_LIVE_PORT = "$Port"
$env:UNIFIED_CONTROLLER_CAP100_ENGINE_URL = "http://127.0.0.1:8781"
$Stdout = Join-Path $Data "unified-controller-r3s-r31-echtgeld-v1.stdout.log"
$Stderr = Join-Path $Data "unified-controller-r3s-r31-echtgeld-v1.stderr.log"
$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.unified_controller_r3s_r31_echtgeld_v1") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
$Process.Id | Set-Content (Join-Path $Root ".unified-controller-r3s-r31-echtgeld-v1.pid")

$Deadline = (Get-Date).AddSeconds(60)
do {
    if ($Process.HasExited) {
        $Tail = ""
        if (Test-Path $Stderr) { $Tail = (Get-Content $Stderr -Tail 80) -join [Environment]::NewLine }
        throw "R3-S+R3.1 8790 exited during startup. $Tail"
    }
    $Health = Get-Json "$Base/health"
    if ($Health -and [string]$Health.version -eq $ExpectedVersion -and [string]$Health.blockReason -ne "ENGINE_NOT_CHECKED") { break }
    Start-Sleep -Milliseconds 350
} while ((Get-Date) -lt $Deadline)

$Health = Get-Json "$Base/health"
$State = Get-Json "$Base/state"
if ($null -eq $Health -or [string]$Health.version -ne $ExpectedVersion) {
    throw "R3-S+R3.1 8790 did not expose the expected version. Check $Stderr"
}
if ([bool]$Health.paperOnly -or -not [bool]$Health.liveOrdersAffected) {
    throw "8790 runtime mode assertion failed: paperOnly=$($Health.paperOnly) liveOrdersAffected=$($Health.liveOrdersAffected)"
}
if ([string]$State.entrySource -ne $ExpectedSource) {
    throw "8790 entrySource mismatch: $($State.entrySource)"
}
if ([string]$State.liveExecution.runtimeHardeningVersion -ne "R3S_R31_HBI1_GHOST1_LAT1") {
    throw "8790 runtime hardening mismatch: $($State.liveExecution.runtimeHardeningVersion)"
}
if (-not [bool]$State.liveExecution.heartbeatIsolated -or -not [bool]$State.liveExecution.heartbeatUsesDedicatedHttpClient -or -not [bool]$State.liveExecution.engineMonitorSeparated) {
    throw "8790 heartbeat isolation hardening is not active."
}
if (-not [bool]$State.liveExecution.safety.heartbeatIndependentOfControllerLock) {
    throw "8790 heartbeat still depends on controller lock."
}
if (-not [bool]$State.liveExecution.safety.preVenuePlannedGhostRetirement) {
    throw "8790 pre-venue PLANNED ghost retirement is not active."
}
if ([double]$State.r3sR31Bundle.configuredShares -ne 10.0) {
    throw "8790 configuredShares mismatch: $($State.r3sR31Bundle.configuredShares)"
}
if ([bool]$State.r3sR31Bundle.r31ActionAuthority) {
    throw "R2.1 unexpectedly has action authority."
}
if ([string]$State.r3sR31Bundle.semanticAcceptance -ne "PASS_R31_LEGACY_R21_PLUS_REAL_ECHTGELD_INCIDENTS_15_OF_15") {
    throw "8790 semantic cooperation artifact mismatch."
}

if ([string]$State.r3sR31Bundle.activeStackVersion -ne "R3S_ACTIVE_STACK_V1_1_0") {
    throw "8790 active stack mismatch: $($State.r3sR31Bundle.activeStackVersion)"
}
$ExpectedComponents = @("R3_ACTIVE_QUANTITY_STRICTPAST_V2","R3_STABLE_ADD_ELIGIBILITY_PILOT500_V2_NORMALIZED","R3_STABLE_EXPANSION_TEACHER_V1_FULL","R3_POST_ADD_OUR_STATE_DISTILL_V2_STREAM","R3_EARLY_RECOVERY_CONTINUATION_PILOT500_V2_HFTSCALE","R3S_TRIPLE_CONFIRM_CONTAINMENT_V1","R3S_WTP_EPISODE_ANCHOR_V1")
$ActualComponents = @($State.r3sR31Bundle.components.PSObject.Properties.Value | ForEach-Object { [string]$_ })
foreach ($Component in $ExpectedComponents) { if ($ActualComponents -notcontains $Component) { throw "8790 missing R3-S component: $Component" } }
if ([string]$State.r3sR31Bundle.wtpVersion -ne "R3S_WTP_EPISODE_ANCHOR_V1") { throw "8790 WTP component mismatch: $($State.r3sR31Bundle.wtpVersion)" }
if (-not [bool]$State.liveExecution.safety.takerConfirmTimeoutCancelOwnedBy8781) {
    throw "8790 does not delegate the fixed Taker confirmation timeout to durable venue owner 8781."
}
if (-not [bool]$State.liveExecution.safety.takerTimeoutCancelWaitsForTerminalAck) {
    throw "8790 Taker timeout path does not retain ownership until terminal ACK."
}
if (-not [bool]$State.liveExecution.safety.lifecyclePollingIndependentOfMarketSnapshots) {
    throw "8790 lifecycle delivery still depends on public strategy snapshots."
}

Write-Host "R3-S+R3.1 Echtgeld controller is listening on $Base/state"
Write-Host "  Decision owner : R3-S V1.1.3 WTP1 Continuous Gate Fix (AQ2/SA2/SE1/PA2/ER2/TC1/WT1/MBF1/CGF1) on 8790"
Write-Host "  Incident layer : R3.1 information-only; all non-terminal children remain visible; 8781 owns cancel execution"
Write-Host "  Venue owner    : 8781 only"
Write-Host "  Entry source   : $ExpectedSource"
Write-Host "  Quantity       : Maker exactly 10 shares; Taker R3 dynamic sizing with no fixed 18-share cap; venue minimum notional enforced by 8781"
Write-Host "  Taker lifecycle: 8781 cancels unfinished remainder after 2200ms; 8790 keeps polling terminal events even after strategy snapshots stop"
Write-Host "  Runtime guard  : HBI1 isolated heartbeat + GHOST1 pre-venue child retirement + LAT1 slow-stage profiling"
Write-Host "  Activation     : operator must select $ExpectedSource and RESUME 8781; next complete market only"
Write-Host "  Current ready  : $($Health.deploymentLiveReady)"
Write-Host "  Current block  : $($Health.blockReason)"
