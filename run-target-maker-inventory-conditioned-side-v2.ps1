param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [ValidateSet("quick", "full")]
    [string]$Preset = "quick",
    [int]$MinTrainMarkets = 60,
    [int]$TestMarkets = 12,
    [int]$MaxFolds = 2,
    [int]$Interactions = 4,
    [int]$MaxRounds = 500,
    [int]$OuterBags = 3,
    [switch]$RunTests,
    [switch]$RebuildDirectPlacementV1
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunStarted = Get-Date

function Write-ProgressLine {
    param([string]$Message)
    $Now = Get-Date
    $Elapsed = $Now - $RunStarted
    Write-Host ("[{0} +{1:00}:{2:00}] {3}" -f $Now.ToString("HH:mm:ss"), [int]$Elapsed.TotalMinutes, $Elapsed.Seconds, $Message)
}

function Invoke-TimedStep {
    param(
        [string]$Label,
        [scriptblock]$Action
    )
    $StepStarted = Get-Date
    Write-ProgressLine "$Label START"
    & $Action
    $Code = $LASTEXITCODE
    $Elapsed = (Get-Date) - $StepStarted
    if ($Code -ne 0) {
        throw "$Label failed with exit code $Code after $([math]::Round($Elapsed.TotalSeconds, 1))s."
    }
    Write-ProgressLine "$Label DONE ($([math]::Round($Elapsed.TotalSeconds, 1))s)"
}

Push-Location $Root
try {
    Write-ProgressLine "TARGET MAKER INVENTORY-CONDITIONED SIDE V2"
    Write-ProgressLine "Preset=$Preset | default is QUICK: 3 feature sets, 2 folds, 500 rounds, 3 bags, no SPECIAL audit."
    Write-ProgressLine "Quick screen stops after ordinary OOF evidence. FULL never starts automatically."
    Write-ProgressLine "Leakage guard: official fill enters state only when event_ms < placement_first_ms."

    $BehaviorV1 = Join-Path $Root "data\research\target_maker_direct_behavior_v1.csv"
    $TargetDb = Join-Path $Root "data\target_wallet_official_v1.db"
    $PublicDataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    $DatasetV2 = Join-Path $Root "data\research\target_maker_inventory_conditioned_side_v2.csv"
    $MetaV2 = Join-Path $Root "data\research\target_maker_inventory_conditioned_side_v2.meta.json"
    $ReportV2 = Join-Path $Root "data\research\target_maker_inventory_conditioned_side_v2_report.json"

    if (-not (Test-Path $TargetDb)) { throw "Missing official Target DB: $TargetDb" }
    if (-not (Test-Path $PublicDataset)) { throw "Missing frozen public dataset: $PublicDataset" }

    Invoke-TimedStep "[1/4] Syntax check" {
        python -m py_compile `
            .\tools\build_target_maker_inventory_conditioned_side_v2.py `
            .\tools\train_target_maker_inventory_conditioned_side_v2.py
    }

    if ($RunTests) {
        Invoke-TimedStep "[2/4] Strict-past leakage tests" {
            python -m pytest -q .\tests\test_target_maker_inventory_conditioned_side_v2.py
        }
    }
    else {
        Write-ProgressLine "[2/4] Tests skipped (use -RunTests to enable)"
    }

    if ($RebuildDirectPlacementV1 -or -not (Test-Path $BehaviorV1)) {
        Invoke-TimedStep "[3a/4] Rebuild V1 inferred-placement dataset" {
            python .\tools\build_target_maker_direct_placement_v1_dataset.py
        }
    }
    else {
        Write-ProgressLine "[3a/4] Reusing existing V1 inferred-placement dataset"
    }

    if (-not (Test-Path $BehaviorV1)) { throw "Missing V1 Maker behavior dataset after preflight: $BehaviorV1" }

    Invoke-TimedStep "[3b/4] Attach strict-past official inventory" {
        python .\tools\build_target_maker_inventory_conditioned_side_v2.py `
            --behavior-dataset $BehaviorV1 `
            --target-db $TargetDb `
            --public-dataset $PublicDataset `
            --output $DatasetV2 `
            --meta $MetaV2 `
            --special-start $SpecialStart
    }

    Write-ProgressLine "[4/4] EBM screen starting. Python will print time + elapsed at every feature set and fold."
    Invoke-TimedStep "[4/4] Maker-side EBM $Preset screen" {
        python .\tools\train_target_maker_inventory_conditioned_side_v2.py `
            --dataset $DatasetV2 `
            --meta $MetaV2 `
            --report $ReportV2 `
            --preset $Preset `
            --min-train-markets $MinTrainMarkets `
            --test-markets $TestMarkets `
            --max-folds $MaxFolds `
            --interactions $Interactions `
            --max-rounds $MaxRounds `
            --outer-bags $OuterBags
    }

    Write-ProgressLine "STOP GATE reached. No longer test is started automatically."
    Write-Host ""
    Write-Host "Review this report before any full run:"
    Write-Host "  data\research\target_maker_inventory_conditioned_side_v2_report.json"
    Write-Host ""
    Write-Host "Quick evidence to inspect:"
    Write-Host "  PUBLIC_ONLY"
    Write-Host "  PUBLIC_PLUS_MAKER_INVENTORY"
    Write-Host "  PUBLIC_PLUS_FULL_INVENTORY"
    Write-Host ""
    Write-Host "Only after evidence review, a longer run can be launched manually with -Preset full and larger fold/round settings."
}
finally {
    Pop-Location
}
