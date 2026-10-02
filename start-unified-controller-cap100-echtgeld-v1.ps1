param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $Root "data"
$Port = 8787
$Base = "http://127.0.0.1:$Port"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Get-ListenerPid {
    try {
        $row = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if ($row) { return [int]$row.OwningProcess }
    } catch { }
    return $null
}
function Get-Json([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 5 } catch { return $null }
}

$Engine = Get-Json "http://127.0.0.1:8781/health"
if ($null -eq $Engine -or -not [bool]$Engine.ok) {
    throw "8781 Echtgeld Engine must be healthy before starting CAP100 8787 adapter."
}
if (-not [bool]$Engine.cap100ExecutionAdapter) {
    throw "8781 does not expose the CAP100 execution adapter. Start Echtgeld V23 first."
}

$ListenerPid = Get-ListenerPid
if ($ListenerPid) {
    $Command = ""
    try { $Command = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerPid").CommandLine } catch { }
    if (-not $Command.ToLowerInvariant().Contains("unified_controller_cap100_echtgeld")) {
        throw "Port $Port is occupied by an unrecognized process. PID=$ListenerPid command=$Command"
    }
    & taskkill.exe /PID $ListenerPid /T /F | Out-Null
    Start-Sleep -Milliseconds 500
}

$env:UNIFIED_CONTROLLER_CAP100_LIVE_HOST = "127.0.0.1"
$env:UNIFIED_CONTROLLER_CAP100_LIVE_PORT = "$Port"
$env:UNIFIED_CONTROLLER_CAP100_ENGINE_URL = "http://127.0.0.1:8781"
$Stdout = Join-Path $Data "unified-controller-cap100-echtgeld-v1.stdout.log"
$Stderr = Join-Path $Data "unified-controller-cap100-echtgeld-v1.stderr.log"
$Process = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "predict_bot.unified_controller_cap100_echtgeld_v1") `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru
$Process.Id | Set-Content (Join-Path $Root ".unified-controller-cap100-echtgeld-v1.pid")

$Deadline = (Get-Date).AddSeconds(45)
do {
    if ($Process.HasExited) {
        $tail = ""
        if (Test-Path $Stderr) { $tail = (Get-Content $Stderr -Tail 80) -join [Environment]::NewLine }
        throw "CAP100 8787 exited during startup. $tail"
    }
    $Health = Get-Json "$Base/health"
    if ($Health -and [bool]$Health.ok) { break }
    Start-Sleep -Milliseconds 350
} while ((Get-Date) -lt $Deadline)
$Health = Get-Json "$Base/health"
if ($null -eq $Health -or -not [bool]$Health.ok) { throw "CAP100 8787 did not become healthy. Check $Stderr" }
if ([bool]$Health.paperOnly) { throw "8787 unexpectedly reports paperOnly=true" }
if (-not [bool]$Health.liveOrdersAffected) { throw "8787 does not report live adapter mode" }

Write-Host "CAP100 Echtgeld adapter is listening on $Base/state"
Write-Host "  Decision owner : 8787 frozen CAP100 controller"
Write-Host "  Venue owner    : 8781 only"
Write-Host "  Fill authority : 8781 venue-confirmed FILL_DELTA only; no paper queue-clear fills"
Write-Host "  Activation     : requires 8781 source CAP100_8787 + RESUME; then waits for next complete market"
Write-Host "  Current ready  : $($Health.deploymentLiveReady)"
Write-Host "  Current block  : $($Health.blockReason)"

if (-not $NoBrowser) { Write-Host "State: $Base/state" }
