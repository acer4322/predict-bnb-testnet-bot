param(
    [int]$WindowRows = 600,
    [int]$MinHistoryRows = 120,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER BURST HAZARD TRIGGER REPLAY V1"
    Write-Host "No EBM training. Reuses V2 raw score CSV."
    Write-Host "Thresholds: causal rolling raw-score percentiles, separately for OPEN/MID/TAIL."
    Write-Host "Policies: LEVEL/RISING x 2s/5s cooldown."
    Write-Host "Window rows per phase: $WindowRows"
    Write-Host "Minimum prior rows per phase: $MinHistoryRows"

    $Scores = Join-Path $Root "data\research\target_taker_post_first_idle_burst_hazard_fast_v2_scores.csv"
    $Dataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    foreach ($Path in @($Scores, $Dataset)) {
        if (-not (Test-Path $Path)) { throw "Missing required input: $Path" }
    }

    Write-Host "`n[1/2] Validate replay analyzer..."
    python -m py_compile .\tools\analyze_target_taker_burst_hazard_trigger_replay_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Trigger replay syntax check failed." }

    if ($RunTests) {
        python -m pytest .\tests\test_target_taker_burst_hazard_trigger_replay_v1.py -q
        if ($LASTEXITCODE -ne 0) { throw "Trigger replay tests failed." }
    } else {
        Write-Host "      Unit tests skipped. Add -RunTests to execute them."
    }

    Write-Host "`n[2/2] Replay causal phase-adaptive triggers..."
    python .\tools\analyze_target_taker_burst_hazard_trigger_replay_v1.py `
        --window-rows $WindowRows `
        --min-history-rows $MinHistoryRows
    if ($LASTEXITCODE -ne 0) { throw "Trigger replay failed." }

    Write-Host "`nDone."
    Write-Host "Report: data\research\target_taker_burst_hazard_trigger_replay_v1_report.json"
}
finally {
    Pop-Location
}
