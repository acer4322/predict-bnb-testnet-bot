param(
    [int]$EntryDelaySeconds = 3,
    [int]$WindowRows = 600,
    [int]$MinHistoryRows = 120,
    [int]$Interactions = 10,
    [int]$MaxRounds = 600,
    [int]$OuterBags = 3,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER TRIGGER-CONDITIONED ACTION V1"
    Write-Host "Goal: bridge validated ordinary 5s hazard triggers to deployment-clean absolute side"
    Write-Host "Hazard triggers: full-timeline OOF, phase-adaptive LEVEL, own entry/cooldown state only"
    Write-Host "Candidates: TOP10_LEVEL_CD5 + TOP20_LEVEL_CD5"
    Write-Host "Action training: strictly earlier ORDINARY_PRE_SPECIAL onset rows only"
    Write-Host "Primary target: next CLEAN subsequent burst UP/DOWN within 5s"
    Write-Host "Secondary target: next subsequent burst CLEAN/MIXED within 5s"
    Write-Host "No special-regime fitting and no paper/live promotion."

    $HazardScores = Join-Path $Root "data\research\target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1_scores.csv"
    $HazardDataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    $OnsetDataset = Join-Path $Root "data\research\target_taker_action_onset_preflight_v1.csv"

    foreach ($Path in @($HazardScores, $HazardDataset, $OnsetDataset)) {
        if (-not (Test-Path $Path)) {
            throw "Missing required input: $Path`nRun the previous hazard/full-timeline and action-onset steps first."
        }
    }

    Write-Host "`n[1/3] Validate trigger-conditioned action analyzer..."
    python -m py_compile .\tools\analyze_target_taker_trigger_conditioned_action_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Trigger-conditioned action syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/3] Run causal-trigger / label-boundary tests..."
        python -m pytest -q .\tests\test_target_taker_trigger_conditioned_action_v1.py
        if ($LASTEXITCODE -ne 0) { throw "Trigger-conditioned action tests failed." }
    }
    else {
        Write-Host "`n[2/3] Tests skipped (use -RunTests to enable)."
    }

    Write-Host "`n[3/3] Fit historical action models and score live-compatible OOF hazard triggers..."
    $Args = @(
        ".\tools\run_with_progress_heartbeat.py",
        ".\tools\run_with_joblib_threading.py",
        ".\tools\analyze_target_taker_trigger_conditioned_action_v1.py",
        "--entry-delay-seconds", "$EntryDelaySeconds",
        "--window-rows", "$WindowRows",
        "--min-history-rows", "$MinHistoryRows",
        "--interactions", "$Interactions",
        "--max-rounds", "$MaxRounds",
        "--outer-bags", "$OuterBags"
    )
    python @Args
    if ($LASTEXITCODE -ne 0) { throw "Trigger-conditioned action analysis failed." }

    Write-Host "`nDone. No strategy was promoted."
    Write-Host "Report: data\research\target_taker_trigger_conditioned_action_v1_report.json"
    Write-Host "OOF:    data\research\target_taker_trigger_conditioned_action_v1_oof.csv"
}
finally {
    Pop-Location
}
