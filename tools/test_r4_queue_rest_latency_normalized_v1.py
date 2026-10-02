from __future__ import annotations
import argparse,json,math,sys
from collections import Counter
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2

OUT=ROOT/'data/research/r4_v0/hourly';ENTRY=1092
RESTS=(0,500,1000,2000)

def md(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return median(xs) if xs else None

def quant(xs,p):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 z=(len(xs)-1)*p;lo=int(z);hi=min(lo+1,len(xs)-1);w=z-lo;return xs[lo]*(1-w)+xs[hi]*w

def agg_rows(rr):
 fills=sum(r['makerFilledShares'] for r in rr);early=sum(r['earlyMakerFillShares'] for r in rr);reasons=Counter()
 for r in rr:reasons.update(r['pruneReasons'])
 resp_prunes=sum(v for k,v in reasons.items() if k!='ORIGINAL_LIFECYCLE_CANCEL')
 return {'markets':len(rr),'makerFilledShares':fills,'earlyMakerFillShares':early,'earlyFillShareOfMakerFills':early/fills if fills>0 else 0.,
         'earlySurplusFillShares':sum(r['earlySurplusFillShares'] for r in rr),'earlyFloorDamage':sum(r['earlyFloorDamage'] for r in rr),
         'prearmedOrders':sum(r['prearmedOrders'] for r in rr),'responsibilityPrunes':resp_prunes,'fallbackOrders':sum(r['fallbackOrders'] for r in rr),'pruneReasons':dict(reasons),
         'everSafeMarkets':sum(r['everSafe'] for r in rr),'durableBaseMarkets':sum(r['durableBase'] for r in rr),'durableBaseRate':sum(r['durableBase'] for r in rr)/len(rr) if rr else None,
         'medianFinalFloor':md([r['final']['floor'] for r in rr]),'p10FinalFloor':quant([r['final']['floor'] for r in rr],.10),
         'medianFinalAbsNet':md([r['final']['absNet'] for r in rr]),'p90FinalAbsNet':quant([r['final']['absNet'] for r in rr],.90),
         'medianPositiveDurationSec':md([r['positiveDurationSec'] for r in rr]),'medianMaxFloorDrawdown':md([r['maxFloorDrawdown'] for r in rr])}

def paired(rows,cfg,base='REACTIVE_0'):
 b={r['marketId']:r for r in rows if r['config']==base};c={r['marketId']:r for r in rows if r['config']==cfg};ids=sorted(set(b)&set(c))
 fd=[c[m]['final']['floor']-b[m]['final']['floor'] for m in ids];an=[c[m]['final']['absNet']-b[m]['final']['absNet'] for m in ids]
 return {'markets':len(ids),'durableGained':sum((not b[m]['durableBase']) and c[m]['durableBase'] for m in ids),'durableLost':sum(b[m]['durableBase'] and (not c[m]['durableBase']) for m in ids),
         'everSafeGained':sum((not b[m]['everSafe']) and c[m]['everSafe'] for m in ids),'everSafeLost':sum(b[m]['everSafe'] and (not c[m]['everSafe']) for m in ids),
         'medianDeltaFinalFloor':md(fd),'meanDeltaFinalFloor':sum(fd)/len(fd) if fd else None,'floorImprovedMarkets':sum(x>1e-9 for x in fd),'floorWorsenedMarkets':sum(x<-1e-9 for x in fd),
         'medianDeltaFinalAbsNet':md(an),'meanDeltaFinalAbsNet':sum(an)/len(an) if an else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--max-markets',type=int,default=10);a=ap.parse_args();ds=v2.choose_files(a.max_markets)
 configs=[('REACTIVE_0',0,'REACTIVE')]
 for rest in RESTS:
  lead=ENTRY+rest
  configs.append((f'KEEP_REST{rest}',lead,'KEEP'))
  configs.append((f'RESP_REST{rest}',lead,'RESP_RELATION'))
 rows=[];errors=[]
 for d in ds:
  mid=int(d['marketId'])
  for name,lead,pol in configs:
   try:
    r=v2.simulate(d,lead,pol);r['config']=name;r['exchangeRestMs']=None if name=='REACTIVE_0' else rest_from_name(name);rows.append(r)
   except Exception as e:errors.append({'marketId':mid,'config':name,'error':f'{type(e).__name__}: {e}'})
  print(json.dumps({'progressMarket':mid,'rows':len(rows),'errors':len(errors)},ensure_ascii=False),flush=True)
 aggregate={name:agg_rows([r for r in rows if r['config']==name]) for name,_,_ in configs}
 paired_vs_reactive={name:paired(rows,name) for name,_,_ in configs if name!='REACTIVE_0'}
 report={'version':'R4_QUEUE_REST_LATENCY_NORMALIZED_V1','researchOnly':True,
         'question':'Separate latency compensation from true pre-need exchange queue resting. User-side lead = measured entry latency (1092ms) + preregistered exchange-rest interval.',
         'configs':[{'name':n,'userLeadMs':l,'exchangeRestMs':None if n=='REACTIVE_0' else rest_from_name(n),'policy':p} for n,l,p in configs],
         'guards':{'entryLatencyMs':ENTRY,'exchangeRestGridMs':list(RESTS),'gridRationale':'0/0.5/1/2s true exchange resting after compensating measured entry latency; not outcome-tuned','noDreamFill':True,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','responseLatencyMs':273,'winnerUsed':False,'takerPathFrozenExogenous':True,'noThresholdSweep':True,'liveTradingChanges':False},
         'aggregate':aggregate,'pairedVsReactive':paired_vs_reactive,'errors':errors,'rows':rows}
 OUT.mkdir(parents=True,exist_ok=True);p=OUT/'r4_queue_rest_latency_normalized_v1.json';p.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':aggregate,'pairedVsReactive':paired_vs_reactive,'errors':errors[:5]},ensure_ascii=False))

def rest_from_name(name):
 if 'REST' not in name:return None
 return int(name.split('REST',1)[1])

if __name__=='__main__':main()
