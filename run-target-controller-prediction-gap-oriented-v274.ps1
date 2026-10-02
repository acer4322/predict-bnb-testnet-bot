$ErrorActionPreference = "Stop"
python -m py_compile tools/analyze_target_controller_prediction_gap_oriented_v274.py tests/test_target_controller_prediction_gap_oriented_v274.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pytest tests/test_target_controller_prediction_gap_oriented_v274.py -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python tools/analyze_target_controller_prediction_gap_oriented_v274.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Report: data/research/target_controller_prediction_gap_oriented_v274_report.json"
