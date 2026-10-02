from __future__ import annotations
import json,sys,warnings
from pathlib import Path
warnings.filterwarnings('ignore')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.r4_cross_for_value_single_fork_v1 import run_one
SRC=ROOT/'data/research/r4_v0/hourly/r4_baseline_cross_candidates_v1.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_first_per_market_older24_v2.json'
PART=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_first_per_market_older24_v2_partial.json'

def main():
 d=json.loads(SRC.read_text(encoding='utf-8')); targets=[]; meta={}
 for m in d['markets']:
  cs=[c for c in m.get('candidates',[]) if float(c.get('floor',0))<0]
  if m.get('seedEquivalent') and cs:
   c=cs[0]; k=(int(m['marketId']),int(c['atMs'])); targets.append(k); meta[k]=c
 done={}
 if PART.exists():
  try:
   for r in json.loads(PART.read_text(encoding='utf-8')).get('rows',[]): done[(int(r['marketId']),int(r['atMs']))]=r
  except: pass
 for i,k in enumerate(targets,1):
  if k in done: continue
  r=run_one(*k); r['baselineCandidate']=meta[k]; A=r.get('R3',{});B=r.get('SINGLE_CROSS',{});df=float(r.get('deltaFloor',0) or 0)
  changed=bool(r.get('treatmentExecuted')) and any(abs(float(B.get(x,0))-float(A.get(x,0)))>1e-9 for x in ['makerFillEvents','makerFilledShares','takerFills','finalAbsNet','worstCaseFloor','makerNet'])
  r['economicActionable']=changed;r['valueClass']='VALUE_POS' if df>1e-9 else 'VALUE_NEG' if df<-1e-9 else 'NO_OP';done[k]=r
  PART.write_text(json.dumps({'rows':list(done.values())},indent=2),encoding='utf-8');print(json.dumps({'i':i,'n':len(targets),'marketId':k[0],'class':r['valueClass'],'deltaFloor':df}),flush=True)
 rows=[done[k] for k in targets if k in done]; agg={'rows':len(rows),'markets':len(rows),'valuePos':sum(r['valueClass']=='VALUE_POS' for r in rows),'valueNeg':sum(r['valueClass']=='VALUE_NEG' for r in rows),'noOp':sum(r['valueClass']=='NO_OP' for r in rows),'actionable':sum(r['economicActionable'] for r in rows),'meanDeltaFloor':sum(float(r.get('deltaFloor',0) or 0) for r in rows)/max(1,len(rows))}
 OUT.write_text(json.dumps({'version':'R4_CROSS_FOR_VALUE_FIRST_PER_MARKET_OLDER24_V2','definition':'One earliest negative-floor baseline-reachable candidate per independent older realistic-HFT market, exact paired KEEP vs one CROSS.','aggregate':agg,'rows':rows,'researchOnly':True,'actionAuthority':False},indent=2),encoding='utf-8'); print(json.dumps(agg,indent=2),flush=True)
if __name__=='__main__':main()
