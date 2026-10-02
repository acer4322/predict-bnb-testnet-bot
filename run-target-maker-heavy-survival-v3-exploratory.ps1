param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [int]$FitStrideSeconds = 2,
    [int]$MinTrainMarkets = 180,
    [int]$TestMarkets = 50,
    [int]$MaxFolds = 7,
    [int]$Interactions = 6,
    [int]$MaxRounds = 500,
    [int]$OuterBags = 4,
    [double]$MinTargetPnlCoverage = 0.95,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET MAKER CONTROLLER V3.3 EXPLORATORY CANONICAL SUBSET"
    Write-Host "Uses cached canonical settlements only; no settlement API refresh."
    Write-Host "Coverage gates for mechanism discovery: ordinary >=90%, SPECIAL >=60%."
    Write-Host "Strict 95% promotion gate remains unchanged; this run cannot promote live cutoffs."

    $PublicDataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    $RiskCsv = Join-Path $Root "data\research\target_maker_taker_repair_hazard_v2_risk.csv"
    $SettlementDb = Join-Path $Root "data\research\target_maker_survival_settlements_v3.db"
    $TargetDb = Join-Path $Root "data\target_wallet_official_v1.db"

    foreach ($required in @($PublicDataset, $RiskCsv, $SettlementDb, $TargetDb)) {
        if (-not (Test-Path $required)) { throw "Missing required input: $required" }
    }

    Write-Host "`n[1/3] Validate exploratory wrapper + V3.3 analyzer..."
    python -m py_compile `
        .\tools\analyze_target_maker_heavy_survival_v3_3.py `
        .\tools\analyze_target_maker_heavy_survival_v3_3_exploratory.py
    if ($LASTEXITCODE -ne 0) { throw "Exploratory V3.3 syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/3] Re-run V3.3 semantic tests..."
        python -m pytest -q .\tests\test_target_maker_portfolio_pnl_semantics_v3_3.py
        if ($LASTEXITCODE -ne 0) { throw "V3.3 semantic tests failed." }
    }
    else {
        Write-Host "`n[2/3] Tests skipped; strict runner already passed 22 tests."
    }

    Write-Host "`n[3/3] Train on available canonical subset + incomplete SPECIAL stress audit..."
    python .\tools\analyze_target_maker_heavy_survival_v3_3_exploratory.py `
        --public-dataset $PublicDataset `
        --settlement-db $SettlementDb `
        --risk-csv $RiskCsv `
        --target-db $TargetDb `
        --min-target-pnl-coverage $MinTargetPnlCoverage `
        --special-start $SpecialStart `
        --fit-stride-seconds $FitStrideSeconds `
        --min-train-markets $MinTrainMarkets `
        --test-markets $TestMarkets `
        --max-folds $MaxFolds `
        --interactions $Interactions `
        --max-rounds $MaxRounds `
        --outer-bags $OuterBags
    if ($LASTEXITCODE -ne 0) { throw "Exploratory V3.3 survival/PnL analysis failed." }

    Write-Host "`nDone. Exploratory only; no cutoff or live rule was promoted."
    Write-Host "Report: data\research\target_maker_heavy_survival_v3_report.json"
    Write-Host "Scores: data\research\target_maker_heavy_survival_v3_scores.csv"
}
finally {
    Pop-Location
}
