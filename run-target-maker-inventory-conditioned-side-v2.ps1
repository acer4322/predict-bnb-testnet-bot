param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [int]$MinTrainMarkets = 60,
    [int]$TestMarkets = 18,
    [int]$MaxFolds = 4,
    [int]$Interactions = 8,
    [int]$MaxRounds = 1800,
    [int]$OuterBags = 6,
    [switch]$RunTests,
    [switch]$RebuildDirectPlacementV1
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET MAKER INVENTORY-CONDITIONED SIDE V2"
    Write-Host "Question: does strict-past Target inventory explain inferred Maker placement side beyond V1 public features?"
    Write-Host "Fit: ORDINARY_PRE_SPECIAL chronological walk-forward only; SPECIAL is untouched audit."
    Write-Host "Leakage guard: official fill enters state only when event_ms < placement_first_ms; same timestamp is excluded."
    Write-Host "Research teacher state only. Live deployment must substitute our own inventory."

    $BehaviorV1 = Join-Path $Root "data\research\target_maker_direct_behavior_v1.csv"
    $TargetDb = Join-Path $Root "data\target_wallet_official_v1.db"
    $PublicDataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    $DatasetV2 = Join-Path $Root "data\research\target_maker_inventory_conditioned_side_v2.csv"
    $MetaV2 = Join-Path $Root "data\research\target_maker_inventory_conditioned_side_v2.meta.json"
    $ReportV2 = Join-Path $Root "data\research\target_maker_inventory_conditioned_side_v2_report.json"

    if (-not (Test-Path $TargetDb)) { throw "Missing official Target DB: $TargetDb" }
    if (-not (Test-Path $PublicDataset)) { throw "Missing frozen public dataset: $PublicDataset" }

    Write-Host "`n[1/4] Validate V2 scripts..."
    python -m py_compile `
        .\tools\build_target_maker_inventory_conditioned_side_v2.py `
        .\tools\train_target_maker_inventory_conditioned_side_v2.py
    if ($LASTEXITCODE -ne 0) { throw "Maker inventory-conditioned V2 syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/4] Run strict-past inventory/leakage tests..."
        python -m pytest -q .\tests\test_target_maker_inventory_conditioned_side_v2.py
        if ($LASTEXITCODE -ne 0) { throw "Maker inventory-conditioned V2 tests failed." }
    }
    else {
        Write-Host "`n[2/4] Tests skipped (use -RunTests to enable)."
    }

    if ($RebuildDirectPlacementV1 -or -not (Test-Path $BehaviorV1)) {
        Write-Host "`n[3/4] Rebuild V1 inferred-placement dataset, then attach strict-past official inventory..."
        python .\tools\build_target_maker_direct_placement_v1_dataset.py
        if ($LASTEXITCODE -ne 0) { throw "V1 Maker direct-placement dataset build failed." }
    }
    else {
        Write-Host "`n[3/4] Reuse existing V1 inferred-placement dataset and attach strict-past official inventory..."
    }

    if (-not (Test-Path $BehaviorV1)) { throw "Missing V1 Maker behavior dataset after preflight: $BehaviorV1" }
    python .\tools\build_target_maker_inventory_conditioned_side_v2.py `
        --behavior-dataset $BehaviorV1 `
        --target-db $TargetDb `
        --public-dataset $PublicDataset `
        --output $DatasetV2 `
        --meta $MetaV2 `
        --special-start $SpecialStart
    if ($LASTEXITCODE -ne 0) { throw "Maker inventory-conditioned V2 dataset build failed." }

    Write-Host "`n[4/4] Compare PUBLIC_ONLY vs inventory-conditioned Maker-side models..."
    python .\tools\train_target_maker_inventory_conditioned_side_v2.py `
        --dataset $DatasetV2 `
        --meta $MetaV2 `
        --report $ReportV2 `
        --min-train-markets $MinTrainMarkets `
        --test-markets $TestMarkets `
        --max-folds $MaxFolds `
        --interactions $Interactions `
        --max-rounds $MaxRounds `
        --outer-bags $OuterBags
    if ($LASTEXITCODE -ne 0) { throw "Maker inventory-conditioned side V2 training failed." }

    Write-Host "`nDone. No Maker-side rule was promoted."
    Write-Host "Dataset: data\research\target_maker_inventory_conditioned_side_v2.csv"
    Write-Host "Meta: data\research\target_maker_inventory_conditioned_side_v2.meta.json"
    Write-Host "Report: data\research\target_maker_inventory_conditioned_side_v2_report.json"
}
finally {
    Pop-Location
}
