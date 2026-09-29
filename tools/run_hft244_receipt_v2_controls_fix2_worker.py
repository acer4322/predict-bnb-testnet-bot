"""A single harness-only continuation against the unchanged V2 binary."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

BUNDLE=Path(__file__).resolve().parent
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_receipt_v2_env1_20260910')
spec=importlib.util.spec_from_file_location('native_build_v1',Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'))
d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
d.SOURCE=ROOT/'.tmp/hft244_accounting_source_v1'; build=ROOT/'.tmp/hft244_accounting_build_v1'
assert 'BTC5M_LAN_RESULT_DIR' in os.environ and d.digest(d.OLD)==d.OLD_SHA
manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
for item in manifest['files']: assert d.digest(BUNDLE/item['name'])==item['sha256']
native=build/'candidate_python/hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
assert d.digest(native)=='06e36073579d94218f7c39673a14b6a13796081331c22284de869cef3b2ba1a9'
script=ROOT/'tools/check_hft244_receipt_v2_controls_fix2.py'; assert not script.exists()
shutil.copy2(BUNDLE/script.name,script)
result=dict(marketBE=0,promotion=False,harnessCorrectionOnly=True)
try:
    d.bounded('v2-controls-fix2',[sys.executable,str(script)],90)
    result['verdict']='PHASE_PASS'
except Exception as exc: result.update(verdict='STOP_V2_CONTROL',error=type(exc).__name__+': '+str(exc))
finally:
    result['originalUnchanged']=d.digest(d.OLD)==d.OLD_SHA
    result['candidateUnchanged']=d.digest(native)=='06e36073579d94218f7c39673a14b6a13796081331c22284de869cef3b2ba1a9'
    for name in ['v2-controls-fix2.json','v2-controls-fix2-progress.json']:
        if (build/name).exists(): shutil.copy2(build/name,d.RESULT/name)
    d.save(d.RESULT/'COMPACT.json',result); print(json.dumps(result),flush=True)
if result['verdict']!='PHASE_PASS' or not result['originalUnchanged'] or not result['candidateUnchanged']: raise SystemExit(2)
