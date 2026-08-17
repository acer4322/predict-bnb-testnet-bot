$ErrorActionPreference = "Stop"
python -m py_compile tools/audit_target_controller_prediction_independent_windows_v275.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pytest tests/test_target_controller_prediction_independent_windows_v275.py -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python tools/audit_target_controller_prediction_independent_windows_v275.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Report: data/research/target_controller_prediction_independent_windows_v275_report.json"
