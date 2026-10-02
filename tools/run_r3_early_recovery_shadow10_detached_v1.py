from __future__ import annotations
import json,subprocess,sys,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/r3_v0'
MIDS=[1679102,1679098,1679097,1678739,1678736,1678735,1678695,1677654,1677482,1677368]
STATUS=D/'r3_early_recovery_shadow10_status_v1.json';OUT=D/'r3_early_recovery_shadow10_v1.json';LOG=D/'r3_early_recovery_shadow10_v1.log'
def one(m):
 cp=subprocess.run([sys.executable,str(ROOT/'tools/shadow_r3_early_recovery_hft_market_v2_hftscale.py'),'--market-id',str(m)],cwd=ROOT,capture_output=True,text=True)
 return m,cp.returncode,cp.stdout[-2000:],cp.stderr[-2000:]
def save(rows,errs,state):
 rep={'version':'R3_EARLY_RECOVERY_SHADOW10_V1','state':state,'markets':MIDS,'completed':len(rows),'errors':errs,'rows':rows};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');STATUS.write_text(json.dumps({'state':state,'completed':len(rows),'errors':len(errs),'artifact':str(OUT),'log':str(LOG)},indent=2),encoding='utf-8')
def main():
 rows=[];errs={};save(rows,errs,'RUNNING')
 with ThreadPoolExecutor(max_workers=3) as ex:
  fs={ex.submit(one,m):m for m in MIDS}
  for f in as_completed(fs):
   m,rc,so,se=f.result()
   if rc: errs[str(m)]={'exit':rc,'stderr':se}
   else:
    p=D/f'r3_early_recovery_hft_shadow_market{m}_v2_hftscale.json'
    d=json.loads(p.read_text(encoding='utf-8')); rows.append({'marketId':m,'summary':d.get('summary'),'attempts':d.get('attempts',[])})
   save(rows,errs,'RUNNING')
 save(rows,errs,'COMPLETE')
if __name__=='__main__':main()
