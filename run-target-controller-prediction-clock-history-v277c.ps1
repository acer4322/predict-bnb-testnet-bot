$ErrorActionPreference = 'Stop'

python -m py_compile tools/audit_target_controller_prediction_clock_history_v277c.py
python -m pytest tests/test_target_controller_prediction_clock_history_v277c.py -q
python tools/audit_target_controller_prediction_clock_history_v277c.py
