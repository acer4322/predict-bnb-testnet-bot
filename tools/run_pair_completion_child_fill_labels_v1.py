from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_pair_completion_counterfactual_v2 as cf
COHORT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/pair_completion_canonical_cohort_v1.json'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/pair_completion_child_fill_labels_canonical_v1.jsonl'
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--limit',type=int,default=20); a=ap.parse_args()
 mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
 done=set()
 if OUT.exists():
  for x in OUT.read_text(encoding='utf-8').splitlines():
   if x.strip(): done.add(int(json.loads(x)['marketId']))
 todo=[m for m in mids if m not in done][:max(1,a.limit)]
 rows=[]
 with OUT.open('a',encoding='utf-8') as fh:
  for i,m in enumerate(todo,1):
   r=cf.run_recovery(m,enable_intervention=False)
   row={'version':'PAIR_COMPLETION_CHILD_FILL_LABELS_CANONICAL_V1','marketId':m,'checkpointMs':r.get('candidateAtMs'),'candidateRecoverySide':r.get('candidateRecoverySide'),'candidateOriginalChildNum':r.get('candidateOriginalChildNum'),'childKeepLabels':r.get('candidateChildKeepLabels')}
   rows.append(row); fh.write(json.dumps(row,ensure_ascii=False,allow_nan=True)+'\n'); fh.flush()
   lab=row.get('childKeepLabels') or {}; print(json.dumps({'progress':i,'marketId':m,'hasChild':row.get('candidateOriginalChildNum') is not None,'fill1':lab.get('anyFill1s'),'fill3':lab.get('anyFill3s'),'fill5':lab.get('anyFill5s')},ensure_ascii=False),flush=True)
 done2=len(done)+len(rows)
 print(json.dumps({'ok':True,'processedThisRun':len(rows),'done':done2,'total':len(mids),'remaining':len(mids)-done2,'output':str(OUT)},ensure_ascii=False))
if __name__=='__main__':main()
