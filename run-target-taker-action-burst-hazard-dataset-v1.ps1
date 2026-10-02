param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [string]$SpecialEnd = "",
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER ACTION BURST HAZARD DATASET V1"
    Write-Host "Primary burst definition: idle gap <=1s, onset cap <=2s"
    Write-Host "Sensitivity audit: same idle gap, onset cap <=3s"
    Write-Host "No EBM training in this step."
    Write-Host "Special start: $SpecialStart"
    if ($SpecialEnd) { Write-Host "Special end:   $SpecialEnd" }

    $Public = Join-Path $Root "data\research\target_taker_direct_eligibility_special_regime_v1.csv"
    $Legacy = Join-Path $Root "data\predict_wallet_shadow.db"
    $Official = Join-Path $Root "data\target_wallet_official_v1.db"

    foreach ($Path in @($Public, $Legacy, $Official)) {
        if (-not (Test-Path $Path)) { throw "Missing required input: $Path" }
    }

    Write-Host "`n[1/2] Validate builder..."
    python -m py_compile .\tools\build_target_taker_action_burst_hazard_dataset_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Burst hazard dataset syntax check failed." }

    if ($RunTests) {
        Write-Host "      Run strict-future / idle-risk unit tests..."
        python -m pytest .\tests\test_target_taker_action_burst_hazard_dataset_v1.py -q
        if ($LASTEXITCODE -ne 0) { throw "Burst hazard dataset tests failed." }
    } else {
        Write-Host "      Unit tests skipped. Add -RunTests to execute them."
    }

    Write-Host "`n[2/2] Merge full Target history, build cap2/cap3 bursts, label public seconds..."
    $Args = @(
        ".\tools\build_target_taker_action_burst_hazard_dataset_v1.py",
        "--special-start", $SpecialStart
    )
    if ($SpecialEnd) {
        $Args += @("--special-end", $SpecialEnd)
    }
    python @Args
    if ($LASTEXITCODE -ne 0) { throw "Target Taker burst hazard dataset build/preflight failed." }

    Write-Host "`nDone. EBM training was NOT started."
    Write-Host "Dataset:   data\research\target_taker_action_burst_hazard_v1.csv"
    Write-Host "Meta:      data\research\target_taker_action_burst_hazard_v1.meta.json"
    Write-Host "Preflight: data\research\target_taker_action_burst_hazard_v1_preflight.json"
}
finally {
    Pop-Location
}
