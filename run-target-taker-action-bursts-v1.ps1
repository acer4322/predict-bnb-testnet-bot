param(
    [string]$GapSeconds = "0,1,2,5",
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER ACTION BURSTS V1"
    Write-Host "Gap sensitivity: $GapSeconds seconds"
    Write-Host "Goal: parent orders -> strategy-level burst candidates -> burst-onset hazard replay"
    Write-Host "No EBM refit: reuses the existing intramarket sequence + continuous hazard scores."

    $Events = Join-Path $Root "data\research\target_taker_intramarket_sequence_v1_events.csv"
    $Scores = Join-Path $Root "data\research\target_taker_continuous_hazard_v1_scores.csv"
    $HazardReport = Join-Path $Root "data\research\target_taker_continuous_hazard_v1_report.json"

    if (-not (Test-Path $Events)) {
        throw "Missing sequence events: $Events`nRun .\run-target-taker-intramarket-replay-v1.ps1 first."
    }
    if (-not (Test-Path $Scores)) {
        throw "Missing continuous hazard scores: $Scores`nRun .\run-target-taker-intramarket-replay-v1.ps1 first."
    }
    if (-not (Test-Path $HazardReport)) {
        throw "Missing continuous hazard report: $HazardReport`nRun .\run-target-taker-intramarket-replay-v1.ps1 first."
    }

    Write-Host "`n[1/2] Validate burst analyzer syntax..."
    python -m py_compile .\tools\analyze_target_taker_action_bursts_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Burst analyzer syntax check failed." }

    if ($RunTests) {
        Write-Host "      Run burst clustering unit tests..."
        python -m pytest .\tests\test_target_taker_action_bursts_v1.py -q
        if ($LASTEXITCODE -ne 0) { throw "Burst clustering tests failed." }
    } else {
        Write-Host "      Unit tests skipped. Add -RunTests to execute them."
    }

    Write-Host "`n[2/2] Collapse Target parents and replay distinct burst onsets..."
    Write-Host "      gap=0s: same-second parents only (timestamp-order-safe baseline)"
    Write-Host "      gap=1/2/5s: decision-burst sensitivity"
    Write-Host "      metrics: compression, MIXED share, SAME/FLIP share, burst capture, burst-window precision"
    python .\tools\analyze_target_taker_action_bursts_v1.py --gap-seconds $GapSeconds
    if ($LASTEXITCODE -ne 0) { throw "Target Taker burst analysis failed." }

    Write-Host "`nDone."
    Write-Host "Report: data\research\target_taker_action_bursts_v1_report.json"
    Write-Host "Burst rows: data\research\target_taker_action_bursts_v1.csv"
}
finally {
    Pop-Location
}
