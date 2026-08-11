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
            if (-not $Quiet) { Write-Host "Stopped $Label ($ProcessId)$($(if ($Tree) { ' process tree' } else { '' }))" }
        }
    }
    finally {
        Remove-Item $Path -Force -ErrorAction SilentlyContinue
    }
}

# Only stop processes started by start-dashboard-v2.ps1. If it detected an
# already-running core API, Predict.fun observer, or multi-asset service, no PID
# file was created and this script deliberately leaves that external process alone.
Stop-OwnedProcess ".web-v2.pid" "Dashboard V2"
Stop-OwnedProcess ".predict-fun-v2.pid" "Predict.fun observer"
# Multi-asset supervisor owns 8770/8772/8773 plus the 8774/8775 clone children.
# Stop the verified owned tree so a restart cannot leave stale child listeners
# that prevent the new clone services from being launched.
Stop-OwnedProcess ".multi-live.pid" "multi-asset live supervisor" -Tree
Stop-OwnedProcess ".api-v2.pid" "core API supervisor"
