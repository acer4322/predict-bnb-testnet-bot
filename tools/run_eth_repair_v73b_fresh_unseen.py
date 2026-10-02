from __future__ import annotations
import importlib.util,sys
from pathlib import Path
p=Path(__file__).with_name('run_eth_repair_v73b_active_fallback_exante_recoverability_shadow.py')
s=importlib.util.spec_from_file_location('v73b_fresh_base',p)
m=importlib.util.module_from_spec(s);sys.modules['v73b_fresh_base']=m;s.loader.exec_module(m)
try:
 i=sys.argv.index('--market-ids'); mids=[int(x) for x in sys.argv[i+1].split(',') if x.strip()]
except Exception as ex:
 raise SystemExit(f'missing/invalid --market-ids: {ex}')
m.FIXED=mids
m.main()
