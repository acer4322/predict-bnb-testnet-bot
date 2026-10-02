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
    Write-Host "TARGET TAKER ORDINARY PAPER PNL ROBUSTNESS V1"
    Write-Host "Goal: test whether positive aggregate OOF PnL survives fold/direction/phase/price and market-level exposure audits"
    Write-Host "Frozen policy: TOP10 hazard + SIDE_TAIL_20 + NO MIXED veto + NO price cap"
    Write-Host "Primary execution audit: 50 bps slippage + Predict 200 bps fee + $1/action"
    Write-Host "No model refit. No threshold, side, phase, price bucket, or paper/live promotion."

    $Oof = Join-Path $Root "data\research\target_taker_trigger_conditioned_action_v1_oof.csv"
    $SignalLegacy = Join-Path $Root "data\wallet_taker_signals.db"
    $SignalPublic = Join-Path $Root "data\public_research_archive_v1.db"
    $Settlement = Join-Path $Root "data\research\target_taker_official_settlements_v1.db"

    if (-not (Test-Path $Oof)) { throw "Missing OOF CSV: $Oof" }
    if (-not (Test-Path $Settlement)) {
        throw "Missing official settlement DB: $Settlement`nRun .\run-target-taker-ordinary-paper-pnl-v1.ps1 first."
    }
    if ((-not (Test-Path $SignalLegacy)) -and (-not (Test-Path $SignalPublic))) {
        throw "Missing both signal archives."
    }

    Write-Host "`n[1/3] Validate robustness analyzer..."
    python -m py_compile .\tools\analyze_target_taker_ordinary_paper_pnl_robustness_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Robustness analyzer syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/3] Run exposure/fold audit tests..."
        python -m pytest -q .\tests\test_target_taker_ordinary_paper_pnl_robustness_v1.py
        if ($LASTEXITCODE -ne 0) { throw "Robustness tests failed." }
    }
    else {
        Write-Host "`n[2/3] Tests skipped (use -RunTests to enable)."
    }

    Write-Host "`n[3/3] Rebuild frozen 50 bps execution rows and audit robustness..."
    $Args = @(
        ".\tools\analyze_target_taker_ordinary_paper_pnl_robustness_v1.py",
        "--window-rows", $WindowRows,
        "--min-history-rows", $MinHistoryRows,
        "--max-price-lag-ms", $MaxPriceLagMs,
        "--fee-bps", $FeeBps,
        "--stake", $Stake,
        "--settlement-db", $Settlement
    )
    if (Test-Path $SignalLegacy) { $Args += @("--signal-db", $SignalLegacy) }
    if (Test-Path $SignalPublic) { $Args += @("--signal-db", $SignalPublic) }
    python @Args
    if ($LASTEXITCODE -ne 0) { throw "Robustness audit failed." }

    Write-Host "`nDone. No strategy was promoted."
    Write-Host "Report: data\research\target_taker_ordinary_paper_pnl_robustness_v1_report.json"
}
finally {
    Pop-Location
}
