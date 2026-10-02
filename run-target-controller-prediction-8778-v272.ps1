$ErrorActionPreference = "Stop"

Write-Host "[V2.7.2] Compile 8778 Prediction audit"
python -m py_compile tools/audit_target_controller_prediction_8778_v272.py tests/test_target_controller_prediction_8778_v272.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7.2] Focused strict-past / namespace / freshness tests"
python -m pytest tests/test_target_controller_prediction_8778_v272.py -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7.2] Audit 8778 Predict book coverage for the exact V2.7 fragment"
python tools/audit_target_controller_prediction_8778_v272.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7.2] Done"
Write-Host "Report: data/research/target_controller_prediction_8778_audit_v272.json"
Write-Host "Primary evidence uses received_at_ms < sample_ms and a 2000ms freshness gate."
Write-Host "If decision.status is READY_FOR_V272_EBM_AB, the next step is the unchanged-protocol V2.7.1 vs V2.7.2 EBM A/B."
