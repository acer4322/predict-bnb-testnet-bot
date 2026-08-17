param(
    [int]$WindowRows = 300,
    [int]$MinHistoryRows = 40,
    [int]$MaxPriceLagMs = 1500,
    [int]$FeeBps = 200,
    [double]$Stake = 1.0,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER ORDINARY PAPER PNL V1"
    Write-Host "Goal: test economic value of the frozen ordinary Target-like OOF policy"
    Write-Host "Policy: TOP10_LEVEL_CD5 + causal SIDE_TAIL_20 + NO MIXED veto"
    Write-Host "Entry: strict-as-of public prediction ask; hold to OFFICIAL market settlement"
    Write-Host "Official settlement source: simulation.db when covered, otherwise Predict market-detail start/end price"
    Write-Host "Audits: 0/25/50/100 bps slippage, price-cap sensitivity, UP/DOWN, phase and fold"
    Write-Host "No Target future labels decide trades. No paper/live promotion."

    $Oof = Join-Path $Root "data\research\target_taker_trigger_conditioned_action_v1_oof.csv"
    $SignalLegacy = Join-Path $Root "data\wallet_taker_signals.db"
    $SignalPublic = Join-Path $Root "data\public_research_archive_v1.db"
    $SettlementSimulation = Join-Path $Root "data\simulation.db"
    $SettlementResearch = Join-Path $Root "data\research\target_taker_official_settlements_v1.db"

    if (-not (Test-Path $Oof)) {
        throw "Missing OOF CSV: $Oof`nRun .\run-target-taker-trigger-conditioned-action-v1.ps1 first."
    }
    if ((-not (Test-Path $SignalLegacy)) -and (-not (Test-Path $SignalPublic))) {
        throw "Missing both signal archives: $SignalLegacy and $SignalPublic"
    }

    Write-Host "`n[1/4] Validate settlement backfill + paper PnL analyzers..."
    python -m py_compile .\tools\backfill_target_taker_official_settlements_v1.py .\tools\analyze_target_taker_ordinary_paper_pnl_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Paper PnL syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/4] Run official-settlement / strict-as-of / fee tests..."
        python -m pytest -q .\tests\test_target_taker_official_settlement_backfill_v1.py .\tests\test_target_taker_ordinary_paper_pnl_v1.py
        if ($LASTEXITCODE -ne 0) { throw "Paper PnL tests failed." }
    }
    else {
        Write-Host "`n[2/4] Tests skipped (use -RunTests to enable)."
    }

    Write-Host "`n[3/4] Backfill OFFICIAL settlements for selected OOF markets..."
    $BackfillArgs = @(
        ".\tools\backfill_target_taker_official_settlements_v1.py",
        "--oof", $Oof,
        "--output-db", $SettlementResearch,
        "--window-rows", $WindowRows,
        "--min-history-rows", $MinHistoryRows
    )
    if (Test-Path $SettlementSimulation) {
        $BackfillArgs += @("--simulation-db", $SettlementSimulation)
    }
    python @BackfillArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Official settlement backfill produced no usable settlements. Check data\research\target_taker_official_settlements_v1_report.json"
    }

    Write-Host "`n[4/4] Replay OOF actions against historical asks and OFFICIAL outcomes..."
    $Args = @(
        ".\tools\analyze_target_taker_ordinary_paper_pnl_v1.py",
        "--window-rows", $WindowRows,
        "--min-history-rows", $MinHistoryRows,
        "--max-price-lag-ms", $MaxPriceLagMs,
        "--fee-bps", $FeeBps,
        "--stake", $Stake,
        "--settlement-db", $SettlementResearch
    )
    if (Test-Path $SignalLegacy) {
        $Args += @("--signal-db", $SignalLegacy)
    }
    if (Test-Path $SignalPublic) {
        $Args += @("--signal-db", $SignalPublic)
    }
    python @Args
    if ($LASTEXITCODE -ne 0) { throw "Ordinary paper PnL replay failed." }

    Write-Host "`nDone. No price cap, side restriction, paper strategy, or live strategy was promoted."
    Write-Host "Settlement report: data\research\target_taker_official_settlements_v1_report.json"
    Write-Host "PnL report: data\research\target_taker_ordinary_paper_pnl_v1_report.json"
}
finally {
    Pop-Location
}
