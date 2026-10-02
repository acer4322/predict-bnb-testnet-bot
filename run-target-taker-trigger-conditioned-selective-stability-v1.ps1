param(
    [int]$WindowRows = 300,
    [int]$MinHistoryRows = 40,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER TRIGGER-CONDITIONED SELECTIVE STABILITY V1"
    Write-Host "Goal: verify selective-policy gains across chronological OOF folds and audit UP/DOWN asymmetry"
    Write-Host "No EBM fitting. Uses existing trigger-conditioned OOF scores only."

    $OOF = Join-Path $Root "data\research\target_taker_trigger_conditioned_action_v1_oof.csv"
    if (-not (Test-Path $OOF)) {
        throw "Missing $OOF`nRun .\run-target-taker-trigger-conditioned-action-v1.ps1 first."
    }

    Write-Host "`n[1/2] Validate analyzer..."
    python -m py_compile .\tools\analyze_target_taker_trigger_conditioned_selective_stability_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Selective stability syntax check failed." }

    if ($RunTests) {
        Write-Host "      Run fold/direction audit tests..."
        python -m pytest -q .\tests\test_target_taker_trigger_conditioned_selective_stability_v1.py
        if ($LASTEXITCODE -ne 0) { throw "Selective stability tests failed." }
    }
    else {
        Write-Host "      Unit tests skipped. Add -RunTests to execute them."
    }

    Write-Host "`n[2/2] Replay existing OOF scores by fold and predicted side..."
    python .\tools\analyze_target_taker_trigger_conditioned_selective_stability_v1.py `
        --window-rows $WindowRows `
        --min-history-rows $MinHistoryRows
    if ($LASTEXITCODE -ne 0) { throw "Selective stability replay failed." }

    Write-Host "`nDone. No strategy was promoted."
    Write-Host "Report: data\research\target_taker_trigger_conditioned_selective_stability_v1_report.json"
}
finally {
    Pop-Location
}
