from __future__ import annotations
import json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; mids=[1679102,1678736]; status=D/'r3_stable_cf2_status_v1.json'; log=D/'r3_stable_cf2_v1.log'; status.write_text(json.dumps({'state':'RUNNING','markets':mids,'completed':[]},indent=2),encoding='utf-8'); done=[]
with log.open('w',encoding='utf-8') as f:
 for m in mids:
  cp=subprocess.run([sys.executable,str(ROOT/'tools/shadow_r3_stable_expansion_hft_market_v1.py'),'--market-id',str(m)],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
  if cp.returncode: status.write_text(json.dumps({'state':'FAILED','marketId':m,'exitCode':cp.returncode,'completed':done,'log':str(log.relative_to(ROOT))},indent=2),encoding='utf-8'); raise SystemExit(cp.returncode)
  done.append(m); status.write_text(json.dumps({'state':'RUNNING','markets':mids,'completed':done},indent=2),encoding='utf-8')
status.write_text(json.dumps({'state':'COMPLETE','markets':mids,'completed':done,'artifacts':[f'data/research/r3_v0/r3_stable_expansion_hft_shadow_market{m}_v1.json' for m in mids]},indent=2),encoding='utf-8')
