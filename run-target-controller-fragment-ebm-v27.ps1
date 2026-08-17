$ErrorActionPreference = "Stop"

Write-Host "[V2.7] Compile analyzer"
python -m py_compile tools/analyze_target_controller_fragment_ebm_v27.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7] Focused guard tests"
python -m pytest tests/test_target_controller_fragment_ebm_v27.py -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7] Check research dependencies"
python -c "from interpret.glassbox import ExplainableBoostingClassifier; import numpy; import sklearn"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Research dependencies are missing. Install with:"
    Write-Host '  python -m pip install -e ".[research]"'
    exit 2
}

Write-Host "[V2.7] Run laptop-friendly 3s fragment EBM"
python tools/analyze_target_controller_fragment_ebm_v27.py --horizons 3
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[V2.7] Done"
Write-Host "Report: data/research/target_controller_fragment_ebm_v27_report.json"
Write-Host "Rows:   data/research/target_controller_fragment_ebm_v27_training_rows.csv"
Write-Host "OOF:    data/research/target_controller_fragment_ebm_v27_local_explanations.csv"
Write-Host "Optional sensitivity after the first pass:"
Write-Host "  python tools/analyze_target_controller_fragment_ebm_v27.py --horizons 1,3,5"
