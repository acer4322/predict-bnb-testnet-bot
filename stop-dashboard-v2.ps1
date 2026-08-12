param([switch]$Quiet)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Stop-OwnedProcess([string]$PidFile, [string]$Label, [switch]$Tree) {
    $Path = Join-Path $Root $PidFile
    if (-not (Test-Path $Path)) { return }
    try {
        $ProcessId = [int](Get-Content $Path -Raw)
        $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if ($Process) {
            if ($Tree) {
                & taskkill.exe /PID $ProcessId /T /F | Out-Null
            }
            else {
                Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
            }
            if (-not $Quiet) {
                $Suffix = if ($Tree) { " process tree" } else { "" }
                Write-Host "Stopped $Label ($ProcessId)$Suffix"
            }
        }
    }
    finally {
        Remove-Item $Path -Force -ErrorAction SilentlyContinue
    }
}

function Get-ListeningProcessId([int]$Port) {
    try {
        $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
        if ($Connection) { return [int]$Connection.OwningProcess }
    }
    catch { }
    return $null
}

function Get-ProcessCommandLine([int]$ProcessId) {
    try {
        $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        return [string]$Process.CommandLine
    }
    catch { return "" }
}

function Stop-RecognizedCoreTree {
    # start-dashboard-v2.ps1 can intentionally reuse an already-running core and
    # therefore omit .api-v2.pid.  For an explicit Dashboard V2 stop/restart we
    # still need to terminate that old tree or updated 8768 Python code will never
    # be imported.  Only touch it after verifying both the 8768 child identity and
    # its predict_bot.supervisor parent; unknown listeners remain untouched.
    $ListenerProcessId = Get-ListeningProcessId 8768
    if (-not $ListenerProcessId) { return }

    try {
        $ListenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ListenerProcessId" -ErrorAction Stop
    }
    catch { return }

    $SupervisorProcessId = [int]$ListenerProcess.ParentProcessId
    $ListenerCommand = [string]$ListenerProcess.CommandLine
    $SupervisorCommand = Get-ProcessCommandLine $SupervisorProcessId
    $LooksLikeStrategySidecar = $ListenerCommand.ToLowerInvariant().Contains("predict_bot.cross_oracle_strategy_dedicated")
    $LooksLikeSupervisor = $SupervisorCommand.ToLowerInvariant().Contains("predict_bot.supervisor")

    if (-not ($LooksLikeStrategySidecar -and $LooksLikeSupervisor)) {
        if (-not $Quiet) {
            Write-Warning "Leaving unrecognized 8768 listener untouched (PID=$ListenerProcessId; command=$ListenerCommand; parent PID=$SupervisorProcessId; parent command=$SupervisorCommand)"
        }
        return
    }

    & taskkill.exe /PID $SupervisorProcessId /T /F | Out-Null
    if ($LASTEXITCODE -eq 0 -and -not $Quiet) {
        Write-Host "Stopped recognized core API supervisor ($SupervisorProcessId) process tree"
    }
    Remove-Item (Join-Path $Root ".api-v2.pid") -Force -ErrorAction SilentlyContinue
}

Stop-OwnedProcess ".web-v2.pid" "Dashboard V2"
Stop-OwnedProcess ".predict-fun-v2.pid" "Predict.fun observer"
# Multi-asset supervisor owns 8770/8772/8773 plus the 8774/8775 clone children.
# Stop the verified owned tree so a restart cannot leave stale child listeners
# that prevent the new clone services from being launched.
Stop-OwnedProcess ".multi-live.pid" "multi-asset live supervisor" -Tree
# The core supervisor owns 8766/8767/8768/8769. Killing only the parent can
# leave orphaned Python listeners running old code on those ports, making a later
# Dashboard V2 restart appear successful while it silently reuses stale services.
Stop-OwnedProcess ".api-v2.pid" "core API supervisor" -Tree
# If the launcher reused an already-running recognized core, there is no PID file.
# An explicit stop must still clear that verified tree so the next start imports
# the newly pulled strategy modules.
Stop-RecognizedCoreTree
