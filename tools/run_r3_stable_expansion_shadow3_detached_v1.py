from __future__ import annotations
import json,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; status=D/'r3_stable_expansion_shadow3_status_v1.json'; log=D/'r3_stable_expansion_shadow3_v1.log'; mids=[1677482,1679102,1678736]
status.write_text(json.dumps({'state':'RUNNING','startedAt':time.time(),'markets':mids,'completed':[],'log':str(log.relative_to(ROOT))},indent=2),encoding='utf-8')
try:
 completed=[]
 with log.open('w',encoding='utf-8') as f:
  for mid in mids:
   cp=subprocess.run([sys.executable,str(ROOT/'tools/shadow_r3_stable_expansion_hft_market_v1.py'),'--market-id',str(mid)],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
   if cp.returncode!=0:
    status.write_text(json.dumps({'state':'FAILED','marketId':mid,'exitCode':cp.returncode,'completed':completed,'log':str(log.relative_to(ROOT))},indent=2),encoding='utf-8'); raise SystemExit(cp.returncode)
   completed.append(mid); status.write_text(json.dumps({'state':'RUNNING','startedAt':None,'markets':mids,'completed':completed,'log':str(log.relative_to(ROOT))},indent=2),encoding='utf-8')
 status.write_text(json.dumps({'state':'COMPLETE','exitCode':0,'finishedAt':time.time(),'markets':mids,'completed':completed,'artifacts':[f'data/research/r3_v0/r3_stable_expansion_hft_shadow_market{m}_v1.json' for m in mids],'log':str(log.relative_to(ROOT))},indent=2),encoding='utf-8')
except SystemExit: raise
except Exception as e:
 status.write_text(json.dumps({'state':'FAILED','error':repr(e),'traceback':traceback.format_exc(),'log':str(log.relative_to(ROOT))},indent=2),encoding='utf-8'); raise
