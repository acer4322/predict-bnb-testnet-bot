param(
    [int]$IdleGapSeconds = 1,
    [string]$MaxDurationSeconds = "1,2,3,5",
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER ACTION BURSTS V2 - CAPPED EPISODES"
    Write-Host "Idle gap: <= $IdleGapSeconds s"
    Write-Host "Action-burst max durations: $MaxDurationSeconds s"
    Write-Host "Goal: separate long activity episodes from bounded decision/execution bursts."
    Write-Host "No EBM refit: reuses existing sequence events + frozen16 continuous hazard scores."

    $Events = Join-Path $Root "data\research\target_taker_intramarket_sequence_v1_events.csv"
    $Scores = Join-Path $Root "data\research\target_taker_continuous_hazard_v1_scores.csv"
    $HazardReport = Join-Path $Root "data\research\target_taker_continuous_hazard_v1_report.json"

    if (-not (Test-Path $Events)) { throw "Missing $Events. Run intramarket replay V1 first." }
    if (-not (Test-Path $Scores)) { throw "Missing $Scores. Run intramarket replay V1 first." }
    if (-not (Test-Path $HazardReport)) { throw "Missing $HazardReport. Run intramarket replay V1 first." }

    Write-Host "`n[1/2] Validate V2 analyzer..."
    python -m py_compile .\tools\analyze_target_taker_action_bursts_v2.py
    if ($LASTEXITCODE -ne 0) { throw "V2 syntax check failed." }

    if ($RunTests) {
        Write-Host "      Run capped-burst unit tests..."
        python -m pytest .\tests\test_target_taker_action_bursts_v2.py -q
        if ($LASTEXITCODE -ne 0) { throw "V2 capped-burst tests failed." }
    } else {
        Write-Host "      Unit tests skipped. Add -RunTests to run them."
    }

    Write-Host "`n[2/2] Build activity episodes, cap action bursts, replay existing hazard scores..."
    python .\tools\analyze_target_taker_action_bursts_v2.py `
        --idle-gap-seconds $IdleGapSeconds `
        --max-duration-seconds $MaxDurationSeconds
    if ($LASTEXITCODE -ne 0) { throw "Target Taker capped-burst V2 analysis failed." }

    Write-Host "`nDone."
    Write-Host "Report: data\research\target_taker_action_bursts_v2_report.json"
    Write-Host "Burst rows: data\research\target_taker_action_bursts_v2.csv"
}
finally {
    Pop-Location
}
