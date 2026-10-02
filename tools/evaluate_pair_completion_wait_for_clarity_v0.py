from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_pair_completion_counterfactual_v3_sequence as cf
from tools.build_pair_completion_tradeoff_curriculum_v3 import pareto_label
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
PAIR=D/'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
OUT=D/'pair_completion_wait_for_clarity_v0.jsonl'
H=(5,10,20)
def row(mid:int,delay:int)->dict:
 k=cf.run_recovery(mid,False,candidate_delay_ms=delay); r=cf.run_recovery(mid,True,candidate_delay_ms=delay)
 if r.get('intervention') is None:
  return {'marketId':mid,'delayMs':delay,'hasCandidate':False,'paretoLabel':'NO_PERSISTENT_STATE','candidateAtMs':r.get('candidateAtMs')}
 dt=[];dc=[]
 z={'marketId':mid,'delayMs':delay,'hasCandidate':True,'candidateAtMs':int(r['intervention']['atMs']),'candidateSide':k.get('candidateRecoverySide'),'actionMode':r['intervention'].get('actionMode'),'resolvedDuringCancel':bool(r['intervention'].get('resolvedDuringCancel')),'features':dict(r['intervention'].get('features') or {})}
 for h in H:
  a=float(r[f'targetErrorArea{h}s'])-float(k[f'targetErrorArea{h}s']); b=(float(r[f'completionCost{h}s'])-float(k[f'completionCost{h}s'])) if r.get(f'completionCost{h}s') is not None and k.get(f'completionCost{h}s') is not None else None; z[f'deltaTrack{h}s']=a;z[f'deltaCost{h}s']=b;dt.append(a);dc.append(b)
 z['paretoLabel']=pareto_label(dt,dc) if len(dc)==3 and all(x is not None for x in dc) else 'AMBIGUOUS_COST';return z
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',default='');ap.add_argument('--delays-ms',default='0,2000,5000');ap.add_argument('--reset',action='store_true');a=ap.parse_args(); delays=[int(x) for x in a.delays_ms.split(',') if x.strip()]
 canonical=[json.loads(x) for x in PAIR.read_text(encoding='utf-8').splitlines() if x.strip()]; canonical.sort(key=lambda r:int(r['checkpointMs'])); mids=[int(x) for x in a.market_ids.split(',') if x.strip()] if a.market_ids else [int(r['marketId']) for r in canonical]
 if a.reset and OUT.exists():OUT.unlink()
 done=set()
 if OUT.exists():
  for x in OUT.read_text(encoding='utf-8').splitlines():
   if x.strip():q=json.loads(x);done.add((int(q['marketId']),int(q['delayMs'])))
 rows=[]
 with OUT.open('a',encoding='utf-8') as fh:
  for mid in mids:
   for d in delays:
    if (mid,d) in done:continue
    q=row(mid,d);rows.append(q);fh.write(json.dumps(q,ensure_ascii=False,allow_nan=True)+'\n');fh.flush();print(json.dumps({'marketId':mid,'delayMs':d,'candidate':q.get('hasCandidate'),'label':q.get('paretoLabel'),'atMs':q.get('candidateAtMs')},ensure_ascii=False),flush=True)
 print(json.dumps({'ok':True,'rowsThisRun':len(rows),'output':str(OUT)},ensure_ascii=False))
if __name__=='__main__':main()
