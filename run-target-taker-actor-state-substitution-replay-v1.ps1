param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [string]$EntryDelays = "1,3,5,10",
    [int]$MinTrainMarkets = 180,
    [int]$TestMarkets = 50,
    [int]$MaxFolds = 7,
    [int]$MaxRounds = 600,
    [int]$OuterBags = 3,
    [int]$Interactions = 10,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER ACTOR-STATE SUBSTITUTION REPLAY V1"
    Write-Host "Goal: remove Target POST_FIRST/IDLE truth from runtime gating"
    Write-Host "Training: ordinary POST_FIRST+IDLE risk rows only"
    Write-Host "Scoring: ALL public seconds in ordinary OOF test markets"
    Write-Host "Runtime actor state: our fixed first-entry delay + our own cooldown only"
    Write-Host "Entry-delay sensitivity: $EntryDelays seconds after first public market row"
    Write-Host "Candidates: TOP10/TOP20 x LEVEL_CD5/LEVEL_CD2"
    Write-Host "Thresholds: fold-local, phase-adaptive, prior raw scores only"
    Write-Host "Special start boundary: $SpecialStart"

    $Dataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    if (-not (Test-Path $Dataset)) {
        throw "Missing $Dataset. Run .\run-target-taker-action-burst-hazard-dataset-v1.ps1 first."
    }

    Write-Host "`n[1/4] Validate scripts..."
    python -m py_compile `
        .\tools\train_target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1.py `
        .\tools\analyze_target_taker_actor_state_substitution_replay_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Actor-state substitution syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/4] Run actor-state boundary tests..."
        python -m pytest -q .\tests\test_target_taker_actor_state_substitution_replay_v1.py
        if ($LASTEXITCODE -ne 0) { throw "Actor-state substitution tests failed." }
    }
    else {
        Write-Host "`n[2/4] Tests skipped (use -RunTests to enable)."
    }

    Write-Host "`n[3/4] Refit ordinary walk-forward hazard and score FULL OOF market timelines..."
    $TrainArgs = @(
        ".\tools\run_with_progress_heartbeat.py",
        ".\tools\run_with_joblib_threading.py",
        ".\tools\train_target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1.py",
        "--special-start", $SpecialStart,
        "--horizon", "5",
        "--min-train-markets", "$MinTrainMarkets",
        "--test-markets", "$TestMarkets",
        "--max-folds", "$MaxFolds",
        "--max-rounds", "$MaxRounds",
        "--outer-bags", "$OuterBags",
        "--interactions", "$Interactions"
    )
    python @TrainArgs
    if ($LASTEXITCODE -ne 0) { throw "Full-timeline ordinary OOF hazard scoring failed." }

    Write-Host "`n[4/4] Replay with strategy-owned actor state only..."
    python .\tools\analyze_target_taker_actor_state_substitution_replay_v1.py `
        --entry-delays $EntryDelays
    if ($LASTEXITCODE -ne 0) { throw "Actor-state substitution replay failed." }

    Write-Host "`nDone."
    Write-Host "Full timeline report: data\research\target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1_report.json"
    Write-Host "Actor replay report:  data\research\target_taker_actor_state_substitution_replay_v1_report.json"
    Write-Host "Full timeline scores: data\research\target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1_scores.csv"
}
finally {
    Pop-Location
}
