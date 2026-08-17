$ErrorActionPreference = 'Stop'

python -m pytest tests/test_target_controller_prediction_joint_support_v275b.py -q
python tools/audit_target_controller_prediction_joint_support_v275b.py
