from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
from tools import test_r4_preposition_gap_responsibility_fixed25_v1 as fx
OUT=ROOT/'data/research/r4_v0/hourly';PRIOR=OUT/'r4_queue_rest_latency_focus25_v1.json';ENTRY=1092
CONFIGS=[('PAIRED_FLAT_REST0',ENTRY,0),('PAIRED_FLAT_REST500',ENTRY+500,500)]

def main():
 prior=json.loads(PRIOR.read_text(encoding='utf-8'));base_rows=[r for r in prior['rows'] if r['config']=='REACTIVE_0'];base={int(r['marketId']):r for r in base_rows};ids=sorted(base);ds,missing=fx.load_exact(ids);rows=[];errs=[]
 for mid in ids:
  if mid not in ds:continue
  for name,lead,rest in CONFIGS:
   try:
    r=v2.simulate(ds[mid],lead,'RESP_PAIRED_FLAT');r['config']=name;r['exchangeRestMs']=rest;rows.append(r)
   except Exception as e:errs.append({'marketId':mid,'config':name,'error':f'{type(e).__name__}: {e}'})
  print(json.dumps({'progressMarket':mid,'rows':len(rows),'errors':len(errs)},ensure_ascii=False),flush=True)
 ag={n:fx.agg([r for r in rows if r['config']==n]) for n,_,_ in CONFIGS};pv={n:fx.paired(base,{int(r['marketId']):r for r in rows if r['config']==n}) for n,_,_ in CONFIGS}
 rep={'version':'R4_PREPOSITION_PAIRED_FLAT_FIXED25_V1','researchOnly':True,'fixedCohortSource':str(PRIOR.relative_to(ROOT)).replace('\\','/'),'candidate':'At FLAT prearm state, never send a one-sided queue option. Hold intent until an opposite-side strict-past-supported prearm request exists, then submit equal paired quantity simultaneously. At non-FLAT states, use weak-side gap responsibility.','guards':{'entryLatencyMs':ENTRY,'noDreamFill':True,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','responseLatencyMs':273,'winnerUsed':False,'takerPathFrozenExogenous':True,'noThresholdSweep':True,'liveTradingChanges':False,'futureFrozenNeedAnchor':'execution counterfactual only'},'priorBaseline':prior['aggregate']['REACTIVE_0'],'priorOneSidedFlat':prior['aggregate']['RESP_REST0'],'aggregate':ag,'pairedVsPriorReactive':pv,'missingMarketIds':sorted(missing),'errors':errs,'rows':rows}
 p=OUT/'r4_preposition_paired_flat_fixed25_v1.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':ag,'pairedVsPriorReactive':pv,'missing':sorted(missing),'errors':errs[:5]},ensure_ascii=False))
if __name__=='__main__':main()
