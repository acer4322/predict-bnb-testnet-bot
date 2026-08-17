param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [int]$FitStrideSeconds = 2,
    [int]$MinTrainMarkets = 180,
    [int]$TestMarkets = 50,
    [int]$MaxFolds = 7,
    [int]$Interactions = 6,
    [int]$MaxRounds = 500,
    [int]$OuterBags = 4,
    [switch]$RunTests,
    [switch]$SkipSettlementBackfill
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET MAKER HEAVY-SIDE SURVIVAL V3"
    Write-Host "Goal: learn target-blind P(UP wins) from ordinary public state, then orient it to Maker-heavy survival."
    Write-Host "Fit: ordinary only, chronological market walk-forward."
    Write-Host "Audit: 2026-08-16 special cohort is never used for fit or calibration."
    Write-Host "Compare: raw Predict midpoint vs PREDICT_CONTEXT EBM vs LEAN_SPOT EBM vs FULL_PUBLIC EBM."
    Write-Host "Primary probability metrics: log-loss + Brier; AUC is secondary."
    Write-Host "Repair audit: rejoin OOF/special survival scores to the frozen V2 POST_FIRST_TAKER risk-set."
    Write-Host "Research only. No cutoff or live rule promotion."

    $PublicDataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    $RiskCsv = Join-Path $Root "data\research\target_maker_taker_repair_hazard_v2_risk.csv"
    $SettlementDb = Join-Path $Root "data\research\target_maker_survival_settlements_v3.db"

    if (-not (Test-Path $PublicDataset)) {
        throw "Missing frozen public dataset: $PublicDataset"
    }
    if (-not (Test-Path $RiskCsv)) {
        throw "Missing V2 repair risk-set: $RiskCsv`nRun .\run-target-maker-taker-repair-hazard-complete-set-v2.ps1 first."
    }

    Write-Host "`n[1/4] Validate V3 scripts..."
    python -m py_compile `
        .\tools\backfill_target_maker_survival_settlements_v3.py `
        .\tools\analyze_target_maker_heavy_survival_v3.py
    if ($LASTEXITCODE -ne 0) { throw "V3 syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/4] Run V3 semantic/leakage-boundary tests..."
        python -m pytest -q .\tests\test_target_maker_heavy_survival_v3.py
        if ($LASTEXITCODE -ne 0) { throw "V3 tests failed." }
    }
    else {
        Write-Host "`n[2/4] Tests skipped (use -RunTests to enable)."
    }

    if (-not $SkipSettlementBackfill) {
        Write-Host "`n[3/4] Backfill official outcomes for the full frozen public cohort..."
        python .\tools\backfill_target_maker_survival_settlements_v3.py `
            --public-dataset $PublicDataset `
            --output-db $SettlementDb
        if ($LASTEXITCODE -ne 0) { throw "V3 settlement backfill failed." }
    }
    else {
        Write-Host "`n[3/4] Settlement backfill skipped."
        if (-not (Test-Path $SettlementDb)) {
            throw "Settlement backfill skipped but DB is missing: $SettlementDb"
        }
    }

    Write-Host "`n[4/4] Train ordinary walk-forward survival models + untouched special audit..."
    python .\tools\analyze_target_maker_heavy_survival_v3.py `
        --public-dataset $PublicDataset `
        --settlement-db $SettlementDb `
        --risk-csv $RiskCsv `
        --special-start $SpecialStart `
        --fit-stride-seconds $FitStrideSeconds `
        --min-train-markets $MinTrainMarkets `
        --test-markets $TestMarkets `
        --max-folds $MaxFolds `
        --interactions $Interactions `
        --max-rounds $MaxRounds `
        --outer-bags $OuterBags
    if ($LASTEXITCODE -ne 0) { throw "V3 survival analysis failed." }

    Write-Host "`nDone. No survival cutoff or repair rule was promoted."
    Write-Host "Settlement report: data\research\target_maker_survival_settlements_v3_report.json"
    Write-Host "V3 report: data\research\target_maker_heavy_survival_v3_report.json"
    Write-Host "Scores: data\research\target_maker_heavy_survival_v3_scores.csv"
}
finally {
    Pop-Location
}
