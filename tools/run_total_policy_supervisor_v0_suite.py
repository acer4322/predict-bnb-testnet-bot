from __future__ import annotations
import subprocess, sys
for p in ['tools/test_total_policy_supervisor_v0.py','tools/replay_cap100_1513668_total_policy_counterfactual_v0.py']:
    print('===',p,'===',flush=True)
    subprocess.run([sys.executable,p],check=True)
