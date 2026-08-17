param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

python -m py_compile tools/analyze_target_pressure_pinning_v25.py
if (-not $SkipTests) {
    python -m pytest tests/test_target_pressure_pinning_v25.py -q
}
python tools/analyze_target_pressure_pinning_v25.py
