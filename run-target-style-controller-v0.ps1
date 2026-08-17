$ErrorActionPreference = 'Stop'

python -m py_compile src/predict_bot/target_style_controller_v0.py tools/backtest_target_style_controller_v0.py
python -m pytest tests/test_target_style_controller_v0.py -q
python tools/backtest_target_style_controller_v0.py
