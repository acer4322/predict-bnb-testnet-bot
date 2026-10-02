from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.build_pair_completion_tradeoff_curriculum_v3 import make_row
COHORT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/pair_completion_canonical_cohort_v1.json'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/pair_completion_tradeoff_curriculum_canonical_v3.jsonl'
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--limit',type=int,default=8); a=ap.parse_args()
 mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
 done=set()
 if OUT.exists():
  for x in OUT.read_text(encoding='utf-8').splitlines():
   if x.strip(): done.add(int(json.loads(x)['marketId']))
 todo=[m for m in mids if m not in done][:max(1,a.limit)]
 rows=[]
 with OUT.open('a',encoding='utf-8') as fh:
  for i,m in enumerate(todo,1):
   r=make_row(m); rows.append(r); fh.write(json.dumps(r,ensure_ascii=False,allow_nan=True)+'\n'); fh.flush(); print(json.dumps({'progress':i,'marketId':m,'pareto':r.get('paretoLabel'),'dTrack20':r.get('deltaTargetErrorArea20s'),'dCost20':r.get('deltaCompletionCost20s')},ensure_ascii=False),flush=True)
 done2=len(done)+len(rows)
 print(json.dumps({'ok':True,'processedThisRun':len(rows),'done':done2,'total':len(mids),'remaining':len(mids)-done2,'output':str(OUT)},ensure_ascii=False))
if __name__=='__main__': main()
