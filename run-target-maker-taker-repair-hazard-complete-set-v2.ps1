param(
    [string]$Asset = "BTC",
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [int]$FeeBps = 200,
    [int]$MaxSignalLagMs = 1500,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET MAKER -> TAKER REPAIR HAZARD + COMPLETE SET V2"
    Write-Host "Hypothesis: when Maker-heavy inventory becomes unlikely to win, especially MID/TAIL, later Taker repair should become more likely/larger"
    Write-Host "Risk set: latest target-blind prediction snapshot per market-second; official signed inventory strictly before snapshot"
    Write-Host "Primary fixed comparison: POST_FIRST_TAKER + MID/TAIL, heavy probability <0.40 vs >=0.60, future repair within 5s"
    Write-Host "Complete-set audit: FIFO Maker BID lots -> later complementary Taker BID fills; pair cost <1 vs ~=1 (+/-1c) vs >1"
    Write-Host "Research only. No model fitting, threshold tuning, or paper/live promotion."

    $TargetDb = Join-Path $Root "data\target_wallet_official_v1.db"
    $PublicDataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    $LegacySignal = Join-Path $Root "data\wallet_taker_signals.db"
    $PublicSignal = Join-Path $Root "data\public_research_archive_v1.db"

    if (-not (Test-Path $TargetDb)) { throw "Missing official Target DB: $TargetDb" }
    if (-not (Test-Path $PublicDataset)) { throw "Missing frozen public cohort dataset: $PublicDataset" }
    if ((-not (Test-Path $LegacySignal)) -and (-not (Test-Path $PublicSignal))) {
        throw "Missing both target-blind signal archives. Expected data\wallet_taker_signals.db and/or data\public_research_archive_v1.db"
    }

    Write-Host "`n[1/3] Validate analyzer..."
    python -m py_compile .\tools\analyze_target_maker_taker_repair_hazard_complete_set_v2.py
    if ($LASTEXITCODE -ne 0) { throw "V2 analyzer syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/3] Run repair-hazard / complete-set tests..."
        python -m pytest -q .\tests\test_target_maker_taker_repair_hazard_complete_set_v2.py
        if ($LASTEXITCODE -ne 0) { throw "V2 tests failed." }
    }
    else {
        Write-Host "`n[2/3] Tests skipped (use -RunTests to enable)."
    }

    Write-Host "`n[3/3] Build causal risk set + complete-set economics..."
    $Args = @(
        ".\tools\analyze_target_maker_taker_repair_hazard_complete_set_v2.py",
        "--target-db", $TargetDb,
        "--public-dataset", $PublicDataset,
        "--asset", $Asset,
        "--special-start", $SpecialStart,
        "--fee-bps", $FeeBps,
        "--max-signal-lag-ms", $MaxSignalLagMs
    )
    if (Test-Path $LegacySignal) { $Args += @("--signal-db", $LegacySignal) }
    if (Test-Path $PublicSignal) { $Args += @("--signal-db", $PublicSignal) }
    python @Args
    if ($LASTEXITCODE -ne 0) { throw "V2 repair hazard / complete-set analysis failed." }

    Write-Host "`nDone. No lifecycle rule was promoted."
    Write-Host "Report: data\research\target_maker_taker_repair_hazard_complete_set_v2_report.json"
    Write-Host "Risk rows: data\research\target_maker_taker_repair_hazard_v2_risk.csv"
    Write-Host "Pair rows: data\research\target_maker_taker_complete_set_v2_pairs.csv"
}
finally {
    Pop-Location
}
