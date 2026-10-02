$ErrorActionPreference = 'Stop'

python -m py_compile tools/backtest_target_style_controller_v0_r25.py
python -m pytest tests/test_target_style_controller_v0.py -q
python tools/backtest_target_style_controller_v0_r25.py
