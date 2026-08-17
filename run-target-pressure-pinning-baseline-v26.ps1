$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python -m py_compile tools/analyze_target_pressure_pinning_baseline_v26.py
python -m pytest tests/test_target_pressure_pinning_baseline_v26.py -q
python tools/analyze_target_pressure_pinning_baseline_v26.py @args
