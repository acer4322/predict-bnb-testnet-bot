$ErrorActionPreference = "Stop"

python -m py_compile tools/analyze_target_opportunity_risk_alignment_v241.py
python -m pytest -q tests/test_target_opportunity_risk_alignment_v241.py
python tools/analyze_target_opportunity_risk_alignment_v241.py
