param(
    [string]$SpecialStart = "2026-08-16T12:00:00+08:00",
    [string]$SpecialEnd = "",
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER ACTION ONSET PREFLIGHT V1"
    Write-Host "Primary unit: one cap2 action burst onset = one row"
    Write-Host "Features: latest STRICTLY PRIOR public snapshot only"
    Write-Host "Primary transition labels: CLEAN/MIXED -> SAME/FLIP"
    Write-Host "Ordinary pre-special = strategy-recovery population"
    Write-Host "Post-special-start = audit/magnifier only"
    Write-Host "No EBM training in this step."
    Write-Host "Special start: $SpecialStart"
    if ($SpecialEnd) { Write-Host "Special end:   $SpecialEnd" }

    $Dataset = Join-Path $Root "data\research\target_taker_action_burst_hazard_v1.csv"
    $Legacy = Join-Path $Root "data\predict_wallet_shadow.db"
    $Official = Join-Path $Root "data\target_wallet_official_v1.db"
    foreach ($Path in @($Dataset, $Legacy, $Official)) {
        if (-not (Test-Path $Path)) { throw "Missing required input: $Path" }
    }

    Write-Host "`n[1/2] Validate strict-past action-onset builder..."
    python -m py_compile .\tools\build_target_taker_action_onset_preflight_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Action-onset preflight syntax check failed." }

    if ($RunTests) {
        python -m pytest .\tests\test_target_taker_action_onset_preflight_v1.py -q
        if ($LASTEXITCODE -ne 0) { throw "Action-onset preflight tests failed." }
    } else {
        Write-Host "      Unit tests skipped. Add -RunTests to execute them."
    }

    Write-Host "`n[2/2] Build strict-pre-onset action labels..."
    $Args = @(
        ".\tools\build_target_taker_action_onset_preflight_v1.py",
        "--special-start", $SpecialStart
    )
    if ($SpecialEnd) {
        $Args += @("--special-end", $SpecialEnd)
    }
    python @Args
    if ($LASTEXITCODE -ne 0) { throw "Action-onset preflight failed." }

    Write-Host "`nDone. EBM training was NOT started."
    Write-Host "Report:  data\research\target_taker_action_onset_preflight_v1_report.json"
    Write-Host "Dataset: data\research\target_taker_action_onset_preflight_v1.csv"
}
finally {
    Pop-Location
}
