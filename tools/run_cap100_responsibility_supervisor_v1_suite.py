from __future__ import annotations
import subprocess,sys
for p in ['tools/test_cap100_responsibility_supervisor_1513668_v1.py','tools/test_cap100_responsibility_supervisor_pnl_1513668_v1.py']:
    print('===',p,'===',flush=True); subprocess.run([sys.executable,p],check=True)
