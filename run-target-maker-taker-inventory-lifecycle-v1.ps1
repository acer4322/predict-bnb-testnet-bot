param(
    [string]$Asset = "BTC",
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [int]$MaxPhaseLagMs = 2000,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET MAKER -> TAKER INVENTORY LIFECYCLE V1"
    Write-Host "Goal: test whether later Target Taker actions add risk or manage/repair signed Maker inventory"
    Write-Host "Inventory: official fill legs, BID=+shares, ASK=-shares, directional delta=UP-DOWN"
    Write-Host "Action unit: reconstructed order-hash parent; fill timeline remains leg-resolution"
    Write-Host "Comparison: ordinary pre-special vs frozen 2026-08-16 special research cohort"
    Write-Host "PnL: official collector Maker/Taker/combined cashflow accounting; no explicit fee deduction"
    Write-Host "Research only. No model fitting and no paper/live promotion."

    $TargetDb = Join-Path $Root "data\target_wallet_official_v1.db"
    $PublicDataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"

    if (-not (Test-Path $TargetDb)) {
        throw "Missing official Target wallet DB: $TargetDb"
    }
    if (-not (Test-Path $PublicDataset)) {
        throw "Missing frozen public research dataset: $PublicDataset`nRun the Target Taker action-burst hazard dataset builder first."
    }

    Write-Host "`n[1/3] Validate lifecycle analyzer..."
    python -m py_compile .\tools\analyze_target_maker_taker_inventory_lifecycle_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Lifecycle analyzer syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/3] Run signed-inventory / semantic-boundary tests..."
        python -m pytest -q .\tests\test_target_maker_taker_inventory_lifecycle_v1.py
        if ($LASTEXITCODE -ne 0) { throw "Lifecycle tests failed." }
    }
    else {
        Write-Host "`n[2/3] Tests skipped (use -RunTests to enable)."
    }

    Write-Host "`n[3/3] Replay official Maker/Taker inventory lifecycle..."
    python .\tools\analyze_target_maker_taker_inventory_lifecycle_v1.py `
        --target-db $TargetDb `
        --public-dataset $PublicDataset `
        --asset $Asset `
        --special-start $SpecialStart `
        --max-phase-lag-ms $MaxPhaseLagMs
    if ($LASTEXITCODE -ne 0) { throw "Inventory lifecycle replay failed." }

    Write-Host "`nDone. No Maker/Taker lifecycle rule was promoted."
    Write-Host "Report: data\research\target_maker_taker_inventory_lifecycle_v1_report.json"
    Write-Host "Actions: data\research\target_maker_taker_inventory_lifecycle_v1.csv"
}
finally {
    Pop-Location
}
