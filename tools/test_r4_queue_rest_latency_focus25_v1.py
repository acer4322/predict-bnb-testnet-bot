from __future__ import annotations
import argparse,json,math,sys
from collections import Counter
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2

OUT=ROOT/'data/research/r4_v0/hourly';ENTRY=1092
CONFIGS=[('REACTIVE_0',0,'REACTIVE',None),('KEEP_REST0',ENTRY,'KEEP',0),('RESP_REST0',ENTRY,'RESP_RELATION',0),('RESP_REST500',ENTRY+500,'RESP_RELATION',500),('RESP_REST2000',ENTRY+2000,'RESP_RELATION',2000)]

def md(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return median(xs) if xs else None

def qt(xs,p):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 z=(len(xs)-1)*p;lo=int(z);hi=min(lo+1,len(xs)-1);w=z-lo;return xs[lo]*(1-w)+xs[hi]*w

def agg(rr):
 fills=sum(r['makerFilledShares'] for r in rr);early=sum(r['earlyMakerFillShares'] for r in rr);reas=Counter()
 for r in rr:reas.update(r['pruneReasons'])
 return {'markets':len(rr),'makerFilledShares':fills,'earlyMakerFillShares':early,'earlyFillShareOfMakerFills':early/fills if fills else 0.,'earlySurplusFillShares':sum(r['earlySurplusFillShares'] for r in rr),'earlyFloorDamage':sum(r['earlyFloorDamage'] for r in rr),'responsibilityPrunes':sum(v for k,v in reas.items() if k!='ORIGINAL_LIFECYCLE_CANCEL'),'fallbackOrders':sum(r['fallbackOrders'] for r in rr),'everSafeMarkets':sum(r['everSafe'] for r in rr),'durableBaseMarkets':sum(r['durableBase'] for r in rr),'durableBaseRate':sum(r['durableBase'] for r in rr)/len(rr) if rr else None,'medianFinalFloor':md([r['final']['floor'] for r in rr]),'p10FinalFloor':qt([r['final']['floor'] for r in rr],.1),'medianFinalAbsNet':md([r['final']['absNet'] for r in rr]),'p90FinalAbsNet':qt([r['final']['absNet'] for r in rr],.9),'medianPositiveDurationSec':md([r['positiveDurationSec'] for r in rr]),'medianMaxFloorDrawdown':md([r['maxFloorDrawdown'] for r in rr]),'pruneReasons':dict(reas)}

def paired(rows,cfg):
 b={r['marketId']:r for r in rows if r['config']=='REACTIVE_0'};c={r['marketId']:r for r in rows if r['config']==cfg};ids=sorted(set(b)&set(c));fd=[c[m]['final']['floor']-b[m]['final']['floor'] for m in ids];an=[c[m]['final']['absNet']-b[m]['final']['absNet'] for m in ids]
 return {'markets':len(ids),'durableGained':sum((not b[m]['durableBase']) and c[m]['durableBase'] for m in ids),'durableLost':sum(b[m]['durableBase'] and not c[m]['durableBase'] for m in ids),'everSafeGained':sum((not b[m]['everSafe']) and c[m]['everSafe'] for m in ids),'everSafeLost':sum(b[m]['everSafe'] and not c[m]['everSafe'] for m in ids),'medianDeltaFinalFloor':md(fd),'meanDeltaFinalFloor':sum(fd)/len(fd) if fd else None,'floorImprovedMarkets':sum(x>1e-9 for x in fd),'floorWorsenedMarkets':sum(x<-1e-9 for x in fd),'medianDeltaFinalAbsNet':md(an),'meanDeltaFinalAbsNet':sum(an)/len(an) if an else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--max-markets',type=int,default=25);a=ap.parse_args();ds=v2.choose_files(a.max_markets);rows=[];errs=[]
 for d in ds:
  mid=int(d['marketId'])
  for name,lead,pol,rest in CONFIGS:
   try:
    r=v2.simulate(d,lead,pol);r['config']=name;r['exchangeRestMs']=rest;rows.append(r)
   except Exception as e:errs.append({'marketId':mid,'config':name,'error':f'{type(e).__name__}: {e}'})
  print(json.dumps({'progressMarket':mid,'rows':len(rows),'errors':len(errs)},ensure_ascii=False),flush=True)
 ag={n:agg([r for r in rows if r['config']==n]) for n,_,_,_ in CONFIGS};pv={n:paired(rows,n) for n,_,_,_ in CONFIGS if n!='REACTIVE_0'}
 rep={'version':'R4_QUEUE_REST_LATENCY_FOCUS25_V1','researchOnly':True,'candidate':'Responsibility-filtered latency compensation versus true extra queue-rest intervals; fixed after 10-market latency-normalized pilot.','configs':[{'name':n,'userLeadMs':l,'policy':p,'exchangeRestMs':r} for n,l,p,r in CONFIGS],'guards':{'pilotSelection':'Configs frozen from prior 10-market diagnostic; no outcome tuning inside this cohort','entryLatencyMs':ENTRY,'noDreamFill':True,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','responseLatencyMs':273,'winnerUsed':False,'takerPathFrozenExogenous':True,'noThresholdSweep':True,'liveTradingChanges':False,'futureFrozenNeedAnchor':'upper-bound execution timing only, not runtime promotion evidence'},'aggregate':ag,'pairedVsReactive':pv,'errors':errs,'rows':rows}
 OUT.mkdir(parents=True,exist_ok=True);p=OUT/'r4_queue_rest_latency_focus25_v1.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':ag,'pairedVsReactive':pv,'errors':errs[:5]},ensure_ascii=False))
if __name__=='__main__':main()
