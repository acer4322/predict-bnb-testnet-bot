param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [string]$SpecialEnd = "",
    [string]$Horizons = "5,2",
    [int]$MaxRounds = 600,
    [int]$OuterBags = 3,
    [int]$Interactions = 10
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER POST-FIRST IDLE BURST HAZARD FAST V1"
    Write-Host "Risk set: cap2 POST_FIRST + IDLE only"
    Write-Host "Features: frozen16 only"
    Write-Host "Primary label: cap2 bounded burst onset; cap3 scored as audit"
    Write-Host "Experiments: history -> full special; history + early special -> late special"
    Write-Host "Metrics: raw + calibrated AUC/AP/log-loss, OPEN/MID/TAIL, calibration top-K gates, unique-burst capture"
    Write-Host "Calibration B uses historical calibration + >=6 early-special calibration markets (not the old 3-market calibrator)."
    Write-Host "Heartbeat: every 30s while an EBM fit is alive"
    Write-Host "Special start: $SpecialStart"
    if ($SpecialEnd) { Write-Host "Special end:   $SpecialEnd" }

    $Dataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    if (-not (Test-Path $Dataset)) {
        throw "Missing $Dataset. Run .\run-target-taker-action-burst-hazard-dataset-v1.ps1 first."
    }

    Write-Host "`n[1/2] Validate trainer..."
    python -m py_compile .\tools\train_target_taker_post_first_idle_burst_hazard_fast_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Post-first idle burst hazard trainer syntax check failed." }

    Write-Host "`n[2/2] Run four fast EBM fits with threading/shared-memory backend + heartbeat..."
    $Args = @(
        ".\tools\run_with_progress_heartbeat.py",
        ".\tools\run_with_joblib_threading.py",
        ".\tools\train_target_taker_post_first_idle_burst_hazard_fast_v1.py",
        "--special-start", $SpecialStart,
        "--horizons", $Horizons,
        "--max-rounds", "$MaxRounds",
        "--outer-bags", "$OuterBags",
        "--interactions", "$Interactions"
    )
    if ($SpecialEnd) {
        $Args += @("--special-end", $SpecialEnd)
    }

    python @Args
    if ($LASTEXITCODE -ne 0) { throw "Post-first idle burst hazard fast training failed." }

    Write-Host "`nDone."
    Write-Host "Report: data\research\target_taker_post_first_idle_burst_hazard_fast_v1_report.json"
    Write-Host "Scores: data\research\target_taker_post_first_idle_burst_hazard_fast_v1_scores.csv"
}
finally {
    Pop-Location
}
