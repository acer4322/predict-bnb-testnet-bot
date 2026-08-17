param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [string]$Horizons = "5",
    [int]$MinTrainMarkets = 180,
    [int]$TestMarkets = 50,
    [int]$MaxFolds = 7,
    [int]$MaxRounds = 600,
    [int]$OuterBags = 3,
    [int]$Interactions = 10,
    [int]$WindowRowsPerPhase = 600,
    [int]$MinHistoryRowsPerPhase = 120
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER BURST HAZARD ORDINARY WALK-FORWARD V1"
    Write-Host "Goal: validate the mechanism found/amplified in the special regime back on ordinary pre-special markets."
    Write-Host "Risk set: cap2 POST_FIRST + IDLE only"
    Write-Host "Features: frozen16"
    Write-Host "Default horizon: 5s"
    Write-Host "Special rows: EXCLUDED from model train/test and ordinary trigger replay"
    Write-Host "Walk-forward: expanding train -> next market-disjoint test block"
    Write-Host "Trigger replay: causal phase-adaptive RAW rank; LEVEL_CD2/CD5 + RISING controls"
    Write-Host "Special boundary: $SpecialStart"

    $Dataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    if (-not (Test-Path $Dataset)) {
        throw "Missing $Dataset. Run .\run-target-taker-action-burst-hazard-dataset-v1.ps1 first."
    }

    $Trainer = ".\tools\train_target_taker_post_first_idle_burst_hazard_ordinary_walkforward_v1.py"
    $Replay = ".\tools\analyze_target_taker_burst_hazard_trigger_replay_v1.py"
    $Scores = ".\data\research\target_taker_post_first_idle_burst_hazard_ordinary_walkforward_v1_scores.csv"
    $ModelReport = ".\data\research\target_taker_post_first_idle_burst_hazard_ordinary_walkforward_v1_report.json"
    $TriggerReport = ".\data\research\target_taker_burst_hazard_ordinary_walkforward_trigger_replay_v1_report.json"

    Write-Host "`n[1/3] Validate scripts..."
    python -m py_compile $Trainer $Replay
    if ($LASTEXITCODE -ne 0) { throw "Ordinary walk-forward syntax check failed." }

    Write-Host "`n[2/3] Fit ordinary-only walk-forward EBM and produce genuine OOF scores..."
    $TrainArgs = @(
        ".\tools\run_with_progress_heartbeat.py",
        ".\tools\run_with_joblib_threading.py",
        $Trainer,
        "--special-start", $SpecialStart,
        "--horizons", $Horizons,
        "--min-train-markets", "$MinTrainMarkets",
        "--test-markets", "$TestMarkets",
        "--max-folds", "$MaxFolds",
        "--max-rounds", "$MaxRounds",
        "--outer-bags", "$OuterBags",
        "--interactions", "$Interactions",
        "--report", $ModelReport,
        "--scores", $Scores
    )
    python @TrainArgs
    if ($LASTEXITCODE -ne 0) { throw "Ordinary walk-forward training failed." }

    Write-Host "`n[3/3] Replay OOF raw scores as causal phase-adaptive triggers..."
    python $Replay `
        --scores $Scores `
        --dataset $Dataset `
        --report $TriggerReport `
        --window-rows $WindowRowsPerPhase `
        --min-history-rows $MinHistoryRowsPerPhase
    if ($LASTEXITCODE -ne 0) { throw "Ordinary trigger replay failed." }

    Write-Host "`nDone."
    Write-Host "Model report:   data\research\target_taker_post_first_idle_burst_hazard_ordinary_walkforward_v1_report.json"
    Write-Host "Trigger report: data\research\target_taker_burst_hazard_ordinary_walkforward_trigger_replay_v1_report.json"
    Write-Host "Scores:         data\research\target_taker_post_first_idle_burst_hazard_ordinary_walkforward_v1_scores.csv"
}
finally {
    Pop-Location
}
