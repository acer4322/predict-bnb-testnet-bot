from __future__ import annotations
import json,lzma,math,statistics,zipfile,tempfile,shutil
from pathlib import Path
from collections import defaultdict
ROOT=Path.cwd().resolve()
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OFF=P/'ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1.json'
BUNDLES=[P/'eth_v23_unseen20_bundle.zip',P/'eth_v23_confirmatory20_bundle.zip']
RAW_GLOBS=['data/research/lan_worker_returns/eth-v23-unseen20-c*/result.json','data/research/lan_worker_returns/eth-v23-confirm20-c*-v2/result.json']
OUT=P/'ETH_REPAIR_V28_SECONDARY_OPTION_AVAILABILITY_SHADOW_V1.json'
EPS=1e-9

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def apply(book,u):
 if int(u[3]):
  book['bids']={round(float(k),12):float(v) for k,v in (u[4] or {}).items()}
  book['asks']={round(float(k),12):float(v) for k,v in (u[5] or {}).items()}
  return
 for key in ('bids','asks'):
  for r in (u[6] or {}).get(key,[]) or []:
   p=round(float(r[0]),12);after=float(r[2])
   if after<=EPS:book[key].pop(p,None)
   else:book[key][p]=after

def out_depth(book,side,p):
 p=round(float(p),12)
 if side=='UP':return float(book['bids'].get(p,0.0))
 return float(book['asks'].get(round(1.0-p,12),0.0))

def levels(book,side,ceiling):
 if side=='UP':raw=[float(x) for x in book['bids']]
 else:raw=[1.0-float(x) for x in book['asks']]
 return sorted({round(p,12) for p in raw if p>EPS and p<=float(ceiling)+EPS},reverse=True)

def best_bid(book,side):
 if not book['bids'] or not book['asks']:return None
 return max(book['bids']) if side=='UP' else 1.0-min(book['asks'])

def load_residuals():
 idx={}
 paths=[]
 for pat in RAW_GLOBS:paths.extend(ROOT.glob(pat))
 for path in paths:
  d=json.loads(path.read_text(encoding='utf-8'))
  for mr in d.get('rows',[]):
   mid=int(mr['marketId']);v=mr.get('V23',{});c=v.get('causal',{});f=v.get('functional',{})
   fills=c.get('fillTrace',[])
   # Keep all actual fills; exact cycle matching is by timestamp/side/price from offline artifact.
   idx.setdefault(mid,[]).extend({'t':int(x['t']),'side':x['side'],'price':float(x['price']),'qty':float(x['qty'])} for x in fills)
 return idx,sorted(str(p.relative_to(ROOT)) for p in paths)

def match_residual(idx,row):
 cand=[]
 for x in idx.get(int(row['marketId']),[]):
  if x['side']!=row['firstSide']:continue
  if abs(float(x['price'])-float(row['firstPrice']))>1e-7:continue
  cand.append((abs(int(x['t'])-int(row['firstFillAt'])),x))
 if not cand:return None
 d,x=min(cand,key=lambda z:z[0])
 return float(x['qty']) if d<=1500 else None

def load_tapes():
 tapes={};tmp=Path(tempfile.mkdtemp(prefix='v28opt_'))
 for b in BUNDLES:
  with zipfile.ZipFile(b) as z:
   for n in z.namelist():
    if n.startswith('tapes/') and n.endswith('.json.xz'):
     mid=int(Path(n).stem.split('.')[0]);
     if mid not in tapes:tapes[mid]=json.loads(lzma.decompress(z.read(n)).decode('utf-8'))
 return tapes,tmp

def main():
 off=json.loads(OFF.read_text(encoding='utf-8'));cycles=off['rows'];residx,rawpaths=load_residuals();tapes,tmp=load_tapes()
 try:
  bym=defaultdict(list)
  for r in cycles:bym[int(r['marketId'])].append(r)
  outrows=[]
  for mid,rr in bym.items():
   pay=tapes[mid];ups=sorted(pay['updates'],key=lambda u:(int(u[1]),int(u[0])))
   for r in sorted(rr,key=lambda x:int(x['firstFillAt'])):
    start=int(r['firstFillAt']);nextfills=[int(x['firstFillAt']) for x in rr if int(x['firstFillAt'])>start]
    end=min(start+30000,min(nextfills) if nextfills else start+30000)
    residual=match_residual(residx,r);book={'bids':{},'asks':{}};started=False;primary=secondary=None
    initpd=initsd=None;lastpd=lastsd=None;minpd=minsd=None;dropP=dropS=0;addP=addS=0;firstDropP=firstDropS=None;zeroP=zeroS=None;checks=0;secAvailableChecks=0;atBestP=atBestS=0;maxBehindP=maxBehindS=0.;firstLevels=[]
    for u in ups:
     t=int(u[1]);apply(book,u)
     if t<start:continue
     if t>end:break
     if not book['bids'] or not book['asks']:continue
     if not started:
      ls=levels(book,r['oppSide'],r['economicCeiling']);firstLevels=ls[:8]
      if ls:primary=ls[0]
      if len(ls)>=2:secondary=ls[1]
      if primary is not None:initpd=lastpd=minpd=out_depth(book,r['oppSide'],primary)
      if secondary is not None:initsd=lastsd=minsd=out_depth(book,r['oppSide'],secondary)
      started=True
     checks+=1;bb=best_bid(book,r['oppSide'])
     if primary is not None:
      pd=out_depth(book,r['oppSide'],primary)
      if pd<lastpd-EPS:dropP+=1;firstDropP=firstDropP if firstDropP is not None else t-start
      elif pd>lastpd+EPS:addP+=1
      minpd=min(minpd,pd);lastpd=pd
      if pd<=EPS and zeroP is None:zeroP=t-start
      if bb is not None:
       bh=max(0.,(bb-primary)/.01);maxBehindP=max(maxBehindP,bh);atBestP+=bh<.5
     if secondary is not None:
      sd=out_depth(book,r['oppSide'],secondary)
      if sd<lastsd-EPS:dropS+=1;firstDropS=firstDropS if firstDropS is not None else t-start
      elif sd>lastsd+EPS:addS+=1
      minsd=min(minsd,sd);lastsd=sd
      if sd<=EPS and zeroS is None:zeroS=t-start
      if bb is not None:
       bh=max(0.,(bb-secondary)/.01);maxBehindS=max(maxBehindS,bh);atBestS+=bh<.5
      secAvailableChecks+=sd>EPS
    q1=(1.0/primary if primary and primary>EPS else None);q2=(1.0/secondary if secondary and secondary>EPS else None)
    feasible=bool(residual is not None and q1 is not None and q2 is not None and q1+q2<=residual+1e-7)
    completion=int(r['packageRepairFillLagMs']) if r.get('packageRepairFillLagMs') is not None else None
    rec={
      'marketId':mid,'cycleIndex':int(r['cycleIndex']),'completedWithin30s':bool(r['completedWithin30sByTrace']),'firstFillAt':start,'cycleWindowMs':end-start,'oppSide':r['oppSide'],'economicCeiling':float(r['economicCeiling']),'residualQty':residual,
      'initialLegalLevels':firstLevels,'primaryShadowPrice':primary,'secondaryShadowPrice':secondary,'distinctGapTicks':((primary-secondary)/.01 if primary is not None and secondary is not None else None),'primaryMinQty':q1,'secondaryMinQty':q2,'combinedMinQty':(q1+q2 if q1 is not None and q2 is not None else None),'sharedBudgetFeasible':feasible,
      'checks':checks,'secondaryVisibleFraction':(secAvailableChecks/checks if checks and secondary is not None else None),
      'primaryInitialDepth':initpd,'secondaryInitialDepth':initsd,'primaryDepthDropEvents':dropP,'secondaryDepthDropEvents':dropS,'primaryDepthAddEvents':addP,'secondaryDepthAddEvents':addS,
      'primaryFirstDepthDropMs':firstDropP,'secondaryFirstDepthDropMs':firstDropS,'primaryLevelZeroMs':zeroP,'secondaryLevelZeroMs':zeroS,
      'primaryDepletionFraction':((initpd-minpd)/max(initpd,EPS) if initpd is not None and minpd is not None else None),'secondaryDepletionFraction':((initsd-minsd)/max(initsd,EPS) if initsd is not None and minsd is not None else None),
      'primaryAtBestFraction':(atBestP/checks if checks and primary is not None else None),'secondaryAtBestFraction':(atBestS/checks if checks and secondary is not None else None),'primaryMaxBehindTicks':maxBehindP if primary is not None else None,'secondaryMaxBehindTicks':maxBehindS if secondary is not None else None,
      'secondaryProgressBeforeCompletionOrDeadline':bool(firstDropS is not None and firstDropS <= (completion if completion is not None else end-start)),'secondaryZeroBeforeCompletionOrDeadline':bool(zeroS is not None and zeroS <= (completion if completion is not None else end-start))
    };outrows.append(rec)
  def block(z):
   feas=[x for x in z if x['sharedBudgetFeasible']]
   return {'cycles':len(z),'markets':len({x['marketId'] for x in z}),'secondaryLevelExistsRate':sum(x['secondaryShadowPrice'] is not None for x in z)/len(z) if z else None,'sharedBudgetFeasibleRate':len(feas)/len(z) if z else None,'secondaryProgressRate':sum(bool(x['secondaryDepthDropEvents']>0) for x in feas)/len(feas) if feas else None,'secondaryZeroRate':sum(x['secondaryLevelZeroMs'] is not None for x in feas)/len(feas) if feas else None,'secondaryProgressBeforeEndRate':sum(bool(x['secondaryProgressBeforeCompletionOrDeadline']) for x in feas)/len(feas) if feas else None,'secondaryZeroBeforeEndRate':sum(bool(x['secondaryZeroBeforeCompletionOrDeadline']) for x in feas)/len(feas) if feas else None,'distinctGapTicks':stats([x['distinctGapTicks'] for x in feas]),'combinedMinQtyToResidual':stats([x['combinedMinQty']/x['residualQty'] for x in feas if x['residualQty']]),'secondaryVisibleFraction':stats([x['secondaryVisibleFraction'] for x in feas]),'secondaryFirstDepthDropMs':stats([x['secondaryFirstDepthDropMs'] for x in feas]),'secondaryLevelZeroMs':stats([x['secondaryLevelZeroMs'] for x in feas]),'secondaryDepletionFraction':stats([x['secondaryDepletionFraction'] for x in feas]),'primaryDepletionFraction':stats([x['primaryDepletionFraction'] for x in feas]),'secondaryMaxBehindTicks':stats([x['secondaryMaxBehindTicks'] for x in feas]),'primaryMaxBehindTicks':stats([x['primaryMaxBehindTicks'] for x in feas])}
  completed=[x for x in outrows if x['completedWithin30s']];failed=[x for x in outrows if not x['completedWithin30s']]
  payload={'version':'ETH_REPAIR_V28_SECONDARY_OPTION_AVAILABILITY_SHADOW_V1','researchOnly':True,'behaviorChange':False,'coverage':{'cycles':len(outrows),'markets':len({x['marketId'] for x in outrows}),'residualMatched':sum(x['residualQty'] is not None for x in outrows),'rawV23ResultPaths':rawpaths},'summary':{'all':block(outrows),'completed':block(completed),'failed':block(failed)},'rows':outrows,'interpretationBoundary':['Offline shadow only; no second child submitted.','Primary/secondary fixed public levels are chosen at cycle start under the frozen economic ceiling; no hindsight switching.','Actual V23 first-leg fill quantity is used only as the shared package budget proxy.','Public depth depletion is anonymous and mixes trades/cancels; it is queue context, not private fill proof.','No PnL tuning and no BTC numeric policy transfer.']}
  OUT.write_text(json.dumps(payload,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT),'coverage':payload['coverage'],'summary':payload['summary']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
