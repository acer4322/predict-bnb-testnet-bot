param(
    [int]$MinTrainMarkets = 180,
    [int]$TestMarkets = 50,
    [int]$MaxFolds = 7,
    [int]$Interactions = 10,
    [int]$MaxRounds = 600,
    [int]$OuterBags = 3,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER ACTION MODEL V1"
    Write-Host "Training: ORDINARY_PRE_SPECIAL only"
    Write-Host "Unit: one strict-past cap2 action-burst onset"
    Write-Host "Tasks: CLEAN/MIXED -> SAME/FLIP -> side audit"
    Write-Host "Special/post-special rows: AUDIT ONLY; never fit"
    Write-Host "No paper/live promotion in this step."

    $Dataset = Join-Path $Root "data\research\target_taker_action_onset_preflight_v1.csv"
    if (-not (Test-Path $Dataset)) {
        throw "Missing preflight CSV: $Dataset`nRun .\run-target-taker-action-onset-preflight-v1.ps1 first."
    }

    Write-Host "`n[1/2] Validate action-model trainer..."
    python -m py_compile .\tools\train_target_taker_action_model_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Action model syntax check failed." }

    if ($RunTests) {
        Write-Host "      Run prior-side / region-boundary unit tests..."
        python -m pytest .\tests\test_target_taker_action_model_v1.py -q
        if ($LASTEXITCODE -ne 0) { throw "Action model tests failed." }
    } else {
        Write-Host "      Unit tests skipped. Add -RunTests to execute them."
    }

    Write-Host "`n[2/2] Run ordinary-market chronological walk-forward EBMs..."
    python .\tools\train_target_taker_action_model_v1.py `
        --min-train-markets $MinTrainMarkets `
        --test-markets $TestMarkets `
        --max-folds $MaxFolds `
        --interactions $Interactions `
        --max-rounds $MaxRounds `
        --outer-bags $OuterBags
    if ($LASTEXITCODE -ne 0) { throw "Action model walk-forward failed." }

    Write-Host "`nDone. No strategy was promoted."
    Write-Host "Report: data\research\target_taker_action_model_v1_report.json"
    Write-Host "OOF:    data\research\target_taker_action_model_v1_oof.csv"
}
finally {
    Pop-Location
}
