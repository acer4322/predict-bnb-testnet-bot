$ErrorActionPreference = "Stop"

Write-Host "[V2.7.2] Compile controlled 8778 Prediction A/B"
python -m py_compile tools/analyze_target_controller_fragment_ebm_v272.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7.2] Focused guard tests"
python -m pytest tests/test_target_controller_prediction_8778_v272.py tests/test_target_controller_fragment_ebm_v272.py -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7.2] Check research dependencies"
python -c "from interpret.glassbox import ExplainableBoostingClassifier; import numpy; import sklearn"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Research dependencies are missing. Install with:"
    Write-Host '  python -m pip install -e ".[research]"'
    exit 2
}

Write-Host "[V2.7.2] Run 3s LOMO EBM A/B: no Prediction vs receivedStrict 8778 Prediction"
python tools/analyze_target_controller_fragment_ebm_v272.py --horizons 3
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7.2] Done"
Write-Host "Report: data/research/target_controller_fragment_ebm_v272_report.json"
Write-Host "Rows:   data/research/target_controller_fragment_ebm_v272_training_rows.csv"
Write-Host "OOF:    data/research/target_controller_fragment_ebm_v272_local_explanations.csv"
