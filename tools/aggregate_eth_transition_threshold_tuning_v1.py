from __future__ import annotations
import argparse,json
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',default='data/research/lan_worker_returns');ap.add_argument('--output',required=True);a=ap.parse_args()
 specs=[('P050',.50,'eth-transition-tune-p050-1912941-20260904-v1'),('P060',.60,'eth-transition-tune-p060-1912941-20260904-v1'),('P090',.90,'eth-transition-tune-p090-1912941-20260904-v1'),('P097',.97,'eth-transition-tune-p097-1912941-20260904-v1'),('P098',.98,'eth-transition-tune-p098-1912941-20260904-v1')]
 rows=[]
 for name,th,jid in specs:
  p=Path(a.root)/jid/'result.json'
  if not p.exists():continue
  d=json.load(open(p,encoding='utf-8'));ag=d['aggregate'];rows.append({'name':name,'threshold':th,'jobId':jid,**ag})
 rows.sort(key=lambda x:x['threshold']);counts=[r['admissionAllows'] for r in rows]
 safety=all(r.get('allSafetyZero') for r in rows) if rows else False;mono=all(a>=b for a,b in zip(counts,counts[1:]));distinct=len(set(counts))>=2
 out={'version':'ETH_TRANSITION_THRESHOLD_TUNING_V1_AGGREGATE','date':'2026-09-04','researchOnly':True,'rows':rows,'gates':{'allFiveComplete':len(rows)==5,'allSafetyZero':safety,'admissionCountMonotoneNonIncreasing':mono,'atLeastTwoDistinctAdmissionCounts':distinct},'decision':'FUNCTIONALITY_PASS_STAGEA_NEXT' if len(rows)==5 and safety and mono and distinct else 'INCOMPLETE_OR_FIX_TUNING_SEAM','boundary':['functionality smoke only; do not select production threshold from one market','no-trade not automatically preferred','stable next-composite stays shadow-only']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
