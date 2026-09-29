from __future__ import annotations
import json,lzma,math,sys
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2

SRC=ROOT/'data/hft_forward_paper_v1/markets'; OUT=ROOT/'data/research/r4_v0/hourly'; PRIOR=OUT/'r4_queue_rest_latency_focus25_v1.json'; ENTRY=1092
NEW=[('RESP_WEAK_REST0',ENTRY,'RESP_WEAK',0),('RESP_GAP_REST0',ENTRY,'RESP_GAP',0),('RESP_GAP_REST500',ENTRY+500,'RESP_GAP',500)]

def md(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return median(xs) if xs else None

def qt(xs,p):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 z=(len(xs)-1)*p;lo=int(z);hi=min(lo+1,len(xs)-1);w=z-lo;return xs[lo]*(1-w)+xs[hi]*w

def load_exact(ids):
 want=set(ids);found={}
 for p in sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
  if not want:break
  try:
   with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  except Exception:continue
  mid=int(d.get('marketId') or 0);student=str(d.get('student') or '')
  if mid in want and 'R2_RESIDUAL' in student and (d.get('orderMeta') or {}):found[mid]=d;want.remove(mid)
 return found,want

def agg(rr):
 fills=sum(r['makerFilledShares'] for r in rr);early=sum(r['earlyMakerFillShares'] for r in rr)
 return {'markets':len(rr),'makerFilledShares':fills,'prearmedOrders':sum(r['prearmedOrders'] for r in rr),'prearmedShares':sum(r.get('prearmedShares',0) for r in rr),'prearmWeak':sum((r.get('prearmRelations') or {}).get('WEAK',0) for r in rr),'prearmFlat':sum((r.get('prearmRelations') or {}).get('FLAT',0) for r in rr),'earlyMakerFillShares':early,'earlyFillShareOfMakerFills':early/fills if fills else 0.,'earlySurplusFillShares':sum(r['earlySurplusFillShares'] for r in rr),'earlyFloorDamage':sum(r['earlyFloorDamage'] for r in rr),'everSafeMarkets':sum(r['everSafe'] for r in rr),'durableBaseMarkets':sum(r['durableBase'] for r in rr),'durableBaseRate':sum(r['durableBase'] for r in rr)/len(rr) if rr else None,'medianFinalFloor':md([r['final']['floor'] for r in rr]),'p10FinalFloor':qt([r['final']['floor'] for r in rr],.1),'medianFinalAbsNet':md([r['final']['absNet'] for r in rr]),'p90FinalAbsNet':qt([r['final']['absNet'] for r in rr],.9),'medianPositiveDurationSec':md([r['positiveDurationSec'] for r in rr]),'medianMaxFloorDrawdown':md([r['maxFloorDrawdown'] for r in rr])}

def paired(base,cand):
 ids=sorted(set(base)&set(cand));fd=[cand[m]['final']['floor']-base[m]['final']['floor'] for m in ids];an=[cand[m]['final']['absNet']-base[m]['final']['absNet'] for m in ids]
 return {'markets':len(ids),'durableGained':sum((not base[m]['durableBase']) and cand[m]['durableBase'] for m in ids),'durableLost':sum(base[m]['durableBase'] and not cand[m]['durableBase'] for m in ids),'everSafeGained':sum((not base[m]['everSafe']) and cand[m]['everSafe'] for m in ids),'everSafeLost':sum(base[m]['everSafe'] and not cand[m]['everSafe'] for m in ids),'medianDeltaFinalFloor':md(fd),'meanDeltaFinalFloor':sum(fd)/len(fd) if fd else None,'floorImprovedMarkets':sum(x>1e-9 for x in fd),'floorWorsenedMarkets':sum(x<-1e-9 for x in fd),'medianDeltaAbsNet':md(an),'meanDeltaAbsNet':sum(an)/len(an) if an else None}

def main():
 prior=json.loads(PRIOR.read_text(encoding='utf-8'));base_rows=[r for r in prior['rows'] if r['config']=='REACTIVE_0'];base={int(r['marketId']):r for r in base_rows};ids=sorted(base);ds,missing=load_exact(ids);rows=[];errs=[]
 for mid in ids:
  if mid not in ds:continue
  for name,lead,pol,rest in NEW:
   try:
    r=v2.simulate(ds[mid],lead,pol);r['config']=name;r['exchangeRestMs']=rest;rows.append(r)
   except Exception as e:errs.append({'marketId':mid,'config':name,'error':f'{type(e).__name__}: {e}'})
  print(json.dumps({'progressMarket':mid,'rows':len(rows),'errors':len(errs)},ensure_ascii=False),flush=True)
 ag={n:agg([r for r in rows if r['config']==n]) for n,_,_,_ in NEW};pv={n:paired(base,{int(r['marketId']):r for r in rows if r['config']==n}) for n,_,_,_ in NEW}
 rep={'version':'R4_PREPOSITION_GAP_RESPONSIBILITY_FIXED25_V1','researchOnly':True,'fixedCohortSource':str(PRIOR.relative_to(ROOT)).replace('\\','/'),'marketIds':ids,'missingMarketIds':sorted(missing),'candidates':{'RESP_WEAK_REST0':'At measured-latency compensation lead, prearm only if current realized inventory makes side strictly weak; FLAT opening is excluded.','RESP_GAP_REST0':'WEAK_ONLY plus prearmed quantity <= current realized weak-side gap minus already-live same-side prearm remainder.','RESP_GAP_REST500':'Same GAP responsibility, with 500ms true exchange resting after measured latency compensation.'},'guards':{'entryLatencyMs':ENTRY,'noDreamFill':True,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','responseLatencyMs':273,'winnerUsed':False,'takerPathFrozenExogenous':True,'noThresholdSweep':True,'liveTradingChanges':False,'futureFrozenNeedAnchor':'execution upper bound only; not runtime promotion evidence'},'priorBaseline':prior['aggregate']['REACTIVE_0'],'priorRespRest0':prior['aggregate']['RESP_REST0'],'aggregate':ag,'pairedVsPriorReactive':pv,'errors':errs,'rows':rows}
 p=OUT/'r4_preposition_gap_responsibility_fixed25_v1.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':ag,'pairedVsPriorReactive':pv,'missing':sorted(missing),'errors':errs[:5]},ensure_ascii=False))
if __name__=='__main__':main()
