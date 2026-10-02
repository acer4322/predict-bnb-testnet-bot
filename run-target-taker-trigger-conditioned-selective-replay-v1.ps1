param(
    [int]$WindowRows = 300,
    [int]$MinHistoryRows = 40,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $Root
try {
    Write-Host "TARGET TAKER TRIGGER-CONDITIONED SELECTIVE REPLAY V1"
    Write-Host "Goal: abstain on weak absolute-side scores instead of acting on every validated hazard trigger"
    Write-Host "Input: existing trigger-conditioned action OOF CSV; no EBM refit"
    Write-Host "Side gates: causal phase-local Top/Bottom 10%, 20%, 30%"
    Write-Host "Audit: optional causal Top20 MIXED-risk veto"
    Write-Host "Success: selected action -> subsequent CLEAN Target burst within 5s -> predicted side matches"
    Write-Host "This is imitation precision, not trading PnL."

    $Oof = Join-Path $Root "data\research\target_taker_trigger_conditioned_action_v1_oof.csv"
    if (-not (Test-Path $Oof)) {
        throw "Missing OOF CSV: $Oof`nRun .\run-target-taker-trigger-conditioned-action-v1.ps1 first."
    }

    Write-Host "`n[1/3] Validate selective replay analyzer..."
    python -m py_compile .\tools\analyze_target_taker_trigger_conditioned_selective_replay_v1.py
    if ($LASTEXITCODE -ne 0) { throw "Selective replay syntax check failed." }

    if ($RunTests) {
        Write-Host "`n[2/3] Run causal threshold / label-isolation tests..."
        python -m pytest -q .\tests\test_target_taker_trigger_conditioned_selective_replay_v1.py
        if ($LASTEXITCODE -ne 0) { throw "Selective replay tests failed." }
    }
    else {
        Write-Host "`n[2/3] Tests skipped (use -RunTests to enable)."
    }

    Write-Host "`n[3/3] Replay selective side policies from existing OOF scores..."
    python .\tools\analyze_target_taker_trigger_conditioned_selective_replay_v1.py `
        --window-rows $WindowRows `
        --min-history-rows $MinHistoryRows
    if ($LASTEXITCODE -ne 0) { throw "Selective replay failed." }

    Write-Host "`nDone. No model was refit and no strategy was promoted."
    Write-Host "Report: data\research\target_taker_trigger_conditioned_selective_replay_v1_report.json"
}
finally {
    Pop-Location
}
