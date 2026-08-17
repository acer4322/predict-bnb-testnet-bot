param(
    [string]$TestStart = "2026-08-16T12:00:00+08:00",
    [string]$TestEnd = "",
    [switch]$SkipSequence,
    [int]$MaxRounds = 600,
    [int]$OuterBags = 3,
    [int]$Interactions = 10
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER INTRAMARKET REPLAY V1"
    Write-Host "Test start: $TestStart"
    if ($TestEnd) {
        Write-Host "Test end:   $TestEnd"
    } else {
        Write-Host "Test end:   latest dataset row"
    }
    Write-Host "Goal: opening-only vs first-signal-once vs continuous every-second Target timing"

    python -c "import sys, pandas, sklearn, interpret; print(f'Python {sys.version.split()[0]} | interpret {interpret.__version__} | pandas {pandas.__version__} | sklearn {sklearn.__version__}')"
    if ($LASTEXITCODE -ne 0) {
        throw 'Research dependencies missing. Run: pip install -e ".[research]"'
    }

    $Dataset = Join-Path $Root "data\research\target_taker_direct_eligibility_special_regime_v1.csv"
    $Official = Join-Path $Root "data\target_wallet_official_v1.db"
    $Shadow = Join-Path $Root "data\predict_wallet_shadow.db"
    if (-not (Test-Path $Dataset)) { throw "Eligibility dataset missing: $Dataset" }
    if (-not (Test-Path $Official)) { throw "Official Target DB missing: $Official" }
    if (-not (Test-Path $Shadow)) { throw "Legacy shadow DB missing: $Shadow" }

    if (-not $SkipSequence) {
        Write-Host "`n[1/2] Reconstruct the full Target Taker intramarket sequence..."
        Write-Host "      FIRST_ENTRY / SAME_SIDE_REENTRY / SIDE_FLIP"
        Write-Host "      fine phase: 300-240 / 240-180 / 180-120 / 120-60 / 60-30 / 30-0s"
        Write-Host "      macro phase: OPEN >180s / MID 60-180s / TAIL <=60s"
        python .\tools\analyze_target_taker_intramarket_sequence_v1.py
        if ($LASTEXITCODE -ne 0) { throw "Intramarket sequence reconstruction failed." }
    } else {
        Write-Host "`n[1/2] Skip sequence reconstruction; reuse existing events/risk-set CSV."
    }

    Write-Host "`n[2/2] Validate frozen16 as a continuous every-second hazard gate..."
    Write-Host "      comparisons: OPENING_ONLY / FIRST_SIGNAL_ONCE / CONTINUOUS"
    Write-Host "      horizons: 5s, 2s"
    Write-Host "      threshold: learned from historical calibration quantiles only"
    Write-Host "      event capture: FIRST_ENTRY / SAME_SIDE_REENTRY / SIDE_FLIP + OPEN/MID/TAIL"
    Write-Host "      config: max_rounds=$MaxRounds outer_bags=$OuterBags interactions=$Interactions"
    Write-Host "      heartbeat: every 30s while EBM fitting/replay is still running"

    $args = @(
        ".\tools\run_with_progress_heartbeat.py",
        ".\tools\run_with_joblib_threading.py",
        ".\tools\validate_target_taker_continuous_hazard_v1.py",
        "--test-start", $TestStart,
        "--horizons", "5,2",
        "--max-rounds", "$MaxRounds",
        "--outer-bags", "$OuterBags",
        "--interactions", "$Interactions"
    )
    if ($TestEnd) { $args += @("--test-end", $TestEnd) }
    python @args
    if ($LASTEXITCODE -ne 0) { throw "Continuous hazard replay failed." }

    Write-Host "`nDone."
    Write-Host "Sequence report: data\research\target_taker_intramarket_sequence_v1_report.json"
    Write-Host "Sequence events: data\research\target_taker_intramarket_sequence_v1_events.csv"
    Write-Host "Per-second risk set: data\research\target_taker_intramarket_riskset_v1.csv"
    Write-Host "Hazard replay report: data\research\target_taker_continuous_hazard_v1_report.json"
    Write-Host "Per-second hazard scores: data\research\target_taker_continuous_hazard_v1_scores.csv"
}
finally {
    Pop-Location
}
