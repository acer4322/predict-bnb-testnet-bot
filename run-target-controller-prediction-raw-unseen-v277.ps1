$ErrorActionPreference = 'Stop'

python -m py_compile tools/analyze_target_controller_prediction_raw_unseen_v277.py
python -m pytest tests/test_target_controller_prediction_raw_unseen_v277.py -q
python tools/analyze_target_controller_prediction_raw_unseen_v277.py
